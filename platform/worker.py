"""Worker 执行器：codex 子进程封装 + 编排层门禁（V2.2 §3.1 / AC-06 结论）。

AC-06 结论落地：门禁必须在编排层（沙箱外）执行——Worker 沙箱内 esbuild spawn EPERM（C-001）。
计量：codex exec 输出解析 tokens used（发现 C），calls 计数 = 每次 codex 调用 1 次。
隔离：codex_home 参数注入 CODEX_HOME，使 Worker 只读专属凭据（P1 CODEX_HOME 隔离实验结论）。
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

_TOKENS_RE = re.compile(r"tokens?\s*used\s*:?\s*\n\s*([\d,]+)", re.IGNORECASE)


@dataclass
class WorkerResult:
    returncode: int
    output: str
    tokens: int | None
    report: str  # codex 结尾报告（非日志部分）

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def _kill_tree(pid: int) -> None:
    """终止整个进程树（C5 实验结论）。

    codex 在 Windows 是 node shim → codex.exe 两级结构；只杀直接子进程会留下孤儿，
    实测其仍在 5s 内写完 40 个文件。超时必须连树一起终止。
    """
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
        return
    import signal
    try:
        os.killpg(os.getpgid(pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def run_codex(prompt: str, workdir: str, *, model: str = "gpt-6.1-terra",
              sandbox: str = "workspace-write", timeout: int = 1200,
              codex_home: str | None = None, log_dir: str | None = None,
              images: list[str] | None = None) -> WorkerResult:
    """同步执行 codex exec；捕获输出并解析 tokens。

    codex_home：隔离 home 目录（须含 auth.json）。不传则沿用当前用户 home。
    images：参考图片绝对路径，按 `codex exec -i <file>` 附加到初始 prompt。
    注意（AC-06）：沙箱网络不阻断，隔离模式下 Worker 仅能读取专属 home 的凭据。
    C5：超时按进程树终止（taskkill /T 或 killpg），避免孤儿 codex 继续写仓库。
    """
    codex_bin = shutil.which("codex")  # Windows：npm 全局命令是 .cmd shim，须解析全路径
    args = [codex_bin] if codex_bin else ["codex"]
    env = None
    if codex_home:
        home = Path(codex_home).expanduser().resolve()
        if not (home / "auth.json").is_file():
            raise RuntimeError(
                f"CODEX_HOME 隔离模式缺少凭据：{home / 'auth.json'}。"
                "请从 ~/.codex/auth.json 复制到该目录后重试（该文件不得提交入库）。"
            )
        home.mkdir(parents=True, exist_ok=True)
        env = {**os.environ, "CODEX_HOME": str(home)}
    # 根因修复（task-002 诊断）：多行 prompt 作为命令行参数会被截断（codex 只收到第一行，
    # 证据："the request ends before specifying the new behavior"）。改用 stdin 完整传递（`-` 占位）。
    exec_args = ["exec"]
    for img in images or []:
        exec_args += ["-i", str(Path(img).resolve())]
    exec_args += ["-s", sandbox, "-c", f'model="{model}"', "-C", str(workdir), "-"]
    proc = subprocess.Popen(
        [*args, *exec_args],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace", env=env,
        start_new_session=(os.name != "nt"),
    )
    timed_out = False
    try:
        stdout, stderr = proc.communicate(prompt, timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_tree(proc.pid)
        try:
            stdout, stderr = proc.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            stdout, stderr = "", ""
    out = (stdout or "") + (stderr or "")
    rc = proc.returncode
    if timed_out:
        rc = -9
        out += f"\n[AgentCode] TIMEOUT after {timeout}s；已终止进程树（含 codex 子进程）"
    # 诊断留痕：每次 codex 调用的完整输出落盘（V2.2 §12 观测原则）
    log_path = (Path(log_dir) if log_dir
                else Path(workdir).parent / "AgentCode" / "projects" / "river" / "logs")
    log_path.mkdir(parents=True, exist_ok=True)
    import time
    (log_path / f"codex-{int(time.time())}.log").write_text(out, encoding="utf-8", errors="replace")
    m = _TOKENS_RE.search(out)
    tokens = int(m.group(1).replace(",", "")) if m else None
    report = ""
    if m:
        tail = out[m.end():]
        report = "\n".join(l for l in tail.splitlines()
                           if l.strip() and not l.startswith("20") and "INFO" not in l)[:1500]
    return WorkerResult(rc, out, tokens, report)


def run_gate(command: str, workdir: str, timeout: int = 900) -> tuple[bool, str]:
    """编排层门禁（沙箱外）。返回 (passed, 输出尾部)。超时同样按进程树终止。"""
    proc = subprocess.Popen(
        command, shell=True, cwd=str(workdir), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace", start_new_session=(os.name != "nt"),
    )
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(proc.pid)
        try:
            out, err = proc.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            out, err = "", ""
        return False, f"GATE TIMEOUT after {timeout}s: {command}\n{(out or '') + (err or '')}"[-2000:]
    return proc.returncode == 0, ((out or "") + (err or ""))[-2000:]


def git(workdir: str, *args: str) -> tuple[int, str]:
    proc = subprocess.run(["git", "-C", str(workdir), *args], capture_output=True,
                          text=True, encoding="utf-8", errors="replace")
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
