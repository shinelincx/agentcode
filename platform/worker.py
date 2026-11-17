"""Worker 执行器：codex 子进程封装 + 编排层门禁（V2.2 §3.1 / AC-06 结论）。

AC-06 结论落地：门禁必须在编排层（沙箱外）执行——Worker 沙箱内 esbuild spawn EPERM（C-001）。
计量：codex exec 输出解析 tokens used（发现 C），calls 计数 = 每次 codex 调用 1 次。
隔离：codex_home 参数注入 CODEX_HOME，使 Worker 只读专属凭据（P1 CODEX_HOME 隔离实验结论）。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

_TOKENS_RE = re.compile(r"tokens?\s*used\s*:?\s*\n\s*([\d,]+)", re.IGNORECASE)


@dataclass
class WorkerResult:
    returncode: int
    output: str
    tokens: int | None
    report: str  # codex 结尾报告（非日志部分）
    final_message: str | None = None

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    @property
    def message(self) -> str:
        """CLI 始终单独提供最终消息。旧适配器可继续使用原输出。"""
        return self.output if self.final_message is None else self.final_message


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
              images: list[str] | None = None, output_schema: dict | None = None) -> WorkerResult:
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
    with tempfile.TemporaryDirectory(prefix="agentcode-codex-output-") as output_dir:
        message_path = Path(output_dir) / "last-message.txt"
        exec_args += ["--output-last-message", str(message_path)]
        if output_schema:
            schema_path = Path(output_dir) / "schema.json"
            schema_path.write_text(json.dumps(output_schema, ensure_ascii=False), encoding="utf-8")
            exec_args += ["--output-schema", str(schema_path)]
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
        final_message = (message_path.read_text(encoding="utf-8-sig", errors="replace")
                         if message_path.is_file() else "")
    out = (stdout or "") + (stderr or "")
    rc = proc.returncode
    if timed_out:
        rc = -9
        out += f"\n[AgentCode] TIMEOUT after {timeout}s；已终止进程树（含 codex 子进程）"
    # 诊断留痕：每次 codex 调用的完整输出落盘（V2.2 §12 观测原则）
    log_path = (Path(log_dir) if log_dir
                else Path(workdir).parent / "AgentCode" / "projects" / "river" / "logs")
    log_path.mkdir(parents=True, exist_ok=True)
    (log_path / f"codex-{time.time_ns()}.log").write_text(out, encoding="utf-8", errors="replace")
    m = _TOKENS_RE.search(out)
    tokens = int(m.group(1).replace(",", "")) if m else None
    report = final_message.strip()[:1500]
    if rc and not report:
        report = (stderr or stdout or "").strip()[-1500:]
    return WorkerResult(rc, out, tokens, report, final_message=final_message)


_GATE_DIAGNOSTIC = re.compile(r"\b(?:error|fail(?:ed)?|exception|traceback|\w+Error|\w+Exception)\b", re.I)
_GATE_ENVIRONMENT = re.compile(
    r"command not found|is not recognized as an internal or external command|不是内部或外部命令"
    r"|access is denied|permission denied|spawn \S+ (?:ENOENT|EPERM)", re.I)
_GATE_TRANSIENT = re.compile(r"\b(?:ECONNRESET|EAGAIN)\b|resource temporarily unavailable", re.I)
_GATE_SECRET = re.compile(
    r"(?im)(\b(?:authorization|api[_-]?key|access[_-]?token|password)\b\s*[:=]\s*)(?:bearer\s+)?[^\s,;]+"
    r"|\b(https?://)[^\s/@]+:[^\s/@]+@")


def safe_gate_text(text: str) -> str:
    return _GATE_SECRET.sub(lambda m: f"{m[1] or m[2]}<REDACTED>", text)


@dataclass(frozen=True)
class GateResult:
    kind: str
    returncode: int | None
    seconds: float
    diagnostic: str
    tail: str

    @property
    def ok(self) -> bool:
        return self.kind == "passed"

    @property
    def detail(self) -> str:
        return (f"{self.kind}, exit={self.returncode}, {self.seconds:.2f}s\n"
                f"首段诊断:\n{self.diagnostic or '（无可用诊断）'}\n输出尾部:\n{self.tail}")

    def __iter__(self):
        # Existing test, deploy and CLI callers use the two-item result.
        yield self.ok
        yield self.tail if self.ok else self.detail


def gate_result(returncode: int, stdout: str, stderr: str = "", seconds: float = 0) -> GateResult:
    """Retain early diagnostic lines and tail from both streams before truncation."""
    streams = [safe_gate_text(stream) for stream in (stdout, stderr) if stream.strip()]
    excerpts = []
    classifications = []
    has_diagnostic = False
    for stream in streams:
        lines = [line.strip() for line in stream.splitlines() if line.strip()]
        tagged = [line for line in lines if _GATE_ENVIRONMENT.search(line) or _GATE_TRANSIENT.search(line)]
        relevant = [line for line in lines if _GATE_DIAGNOSTIC.search(line)]
        has_diagnostic = has_diagnostic or bool(relevant)
        excerpts.extend((*tagged[:1], *(relevant or lines)[:6]))
        classifications.extend(tagged)
    diagnostic = "\n".join(dict.fromkeys(line[:240] for line in excerpts))
    tail = "\n".join(('[前部已截断]\n' if len(stream) > 650 else '') + stream[-650:] for stream in streams)
    if returncode == 0:
        kind = "passed"
    elif returncode in (126, 127, 9009) or any(_GATE_ENVIRONMENT.search(line) for line in classifications):
        kind = "environment"
    elif classifications:
        kind = "transient"
    else:
        kind = "failed" if has_diagnostic else "unknown"
    return GateResult(kind, returncode, seconds, diagnostic, tail)


def _gate_stream(data: bytes | None) -> str:
    if not data:
        return ""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("oem" if os.name == "nt" else "utf-8", errors="replace")


def run_gate(command: str, workdir: str, timeout: int = 900) -> GateResult:
    """Run outside the Worker sandbox and retain the process outcome separately from output."""
    start = time.monotonic()
    if command.startswith("{project-run} "):
        command = f'"{sys.executable}" "{Path(__file__).with_name("project_runs.py")}" ' + command[len("{project-run} "):]
    try:
        proc = subprocess.Popen(
            command, shell=True, cwd=str(workdir), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            start_new_session=(os.name != "nt"),
        )
    except OSError as exc:
        return GateResult("environment", None, time.monotonic() - start, safe_gate_text(str(exc)), "")
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(proc.pid)
        try:
            out, err = proc.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            out, err = None, None
        partial = gate_result(1, _gate_stream(out), _gate_stream(err), time.monotonic() - start)
        return GateResult("timeout", None, partial.seconds,
                          f"GATE TIMEOUT after {timeout}s\n{partial.diagnostic}", partial.tail)
    return gate_result(proc.returncode, _gate_stream(out), _gate_stream(err), time.monotonic() - start)


def git(workdir: str, *args: str) -> tuple[int, str]:
    proc = subprocess.run(["git", "-C", str(workdir), *args], capture_output=True,
                          text=True, encoding="utf-8", errors="replace")
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
