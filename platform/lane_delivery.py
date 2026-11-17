"""Deterministic checks on the files an implementation lane will commit."""
import hashlib
from pathlib import PurePosixPath
import re


def status_paths(text: str) -> list[str]:
    """Porcelain v1 -z, with --no-renames so both endpoints are checked as add/delete."""
    entries = text.split("\0")
    if entries.pop() != "" or any(len(entry) < 4 or entry[2] != " " for entry in entries):
        raise ValueError("无法解析 Git 文件状态")
    return [entry[3:] for entry in entries]


_TEST_DIRS = {"test", "tests", "__tests__", "__test__", "spec", "specs", "checks"}
_RUNNER_FILES = {"package.json", "pyproject.toml", "pytest.ini", "tox.ini", "setup.cfg", "conftest.py",
                 "pom.xml", "build.gradle", "build.gradle.kts", ".mocharc.json", "testng.xml"}
_SKIP = re.compile(r"(?:\b(?:skip|skipif|skipUnless|xfail|expectedFailure|pending|xdescribe|xit|xtest)\s*\("
                   r"|\.(?:only|skip|skipIf|todo)\s*\(|@(?:unittest\.|pytest\.mark\.)?(?:skip|xfail)\b"
                   r"|@(?:Disabled|Ignore)\b|--passWithNoTests\b)")


def _test_runner(path: PurePosixPath) -> bool:
    name = path.name.lower()
    return (name in _RUNNER_FILES or name.startswith(("jest.config.", "vitest.config.", "playwright.config."))
            or tuple(path.parts[:2]) == (".github", "workflows"))


def test_related(path: str) -> bool:
    p = PurePosixPath(path)
    stem = p.name.lower()
    return (any(part.lower() in _TEST_DIRS for part in p.parts[:-1])
            or stem.startswith("test_")
            or ".test." in stem or ".spec." in stem or stem.endswith(("_test.py", "test.java", "test.kt"))
            or _test_runner(p))


def test_change_error(path: str, patch: str, approved_updates: list[str]) -> str:
    """Preserve tracked test content. Explicit G2 exceptions permit targeted replacements."""
    lines = patch.splitlines()
    if any(line.startswith("deleted file mode") for line in lines):
        return f"既有测试或门禁入口被删除：{path}"
    approved = path in approved_updates
    if patch and not approved and _test_runner(PurePosixPath(path)):
        return f"测试配置或门禁入口被调整，缺少 G2 对该文件的调整说明：{path}"
    if not approved and any(line.startswith(("Binary files ", "GIT binary patch")) for line in lines):
        return f"既有测试资产被调整，缺少 G2 对该文件的调整说明：{path}"
    in_hunk = False
    for line in lines:
        if line.startswith("@@"):
            in_hunk = True
        if not in_hunk:
            continue
        if line.startswith("+") and _SKIP.search(line[1:]):
            return f"测试新增跳过或仅运行标记：{path}"
        if line.startswith("-") and line[1:].strip() and not approved:
            return f"既有测试或门禁入口的行被删改，缺少 G2 对该文件的调整说明：{path}"
    return ""


def _patch(repo: str, status: str, path: str | None = None, *, staged: bool = False) -> str:
    import worker as w

    args = ("diff", "--binary", "--no-renames", "--no-ext-diff", "--unified=0")
    target = (f":(literal){path}",) if path else ()
    rc, patch = w.git(repo, *args, *(["--cached"] if staged else ["HEAD"]), "--", *target)
    if rc:
        raise ValueError(f"无法读取 lane 补丁：{path or '工作区'}")
    if not staged:
        for entry in status.split("\0"):
            if entry.startswith("?? ") and (path is None or path == entry[3:]):
                rc, extra = w.git(repo, *args, "--no-index", "--", "/dev/null", entry[3:])
                if rc != 1 or not extra.startswith("diff --git "):
                    raise ValueError(f"无法读取新增文件补丁：{entry[3:]}")
                patch += extra
    return patch


def patch_fingerprint(repo: str, status: str) -> str:
    return hashlib.sha256(_patch(repo, status).encode("utf-8")).hexdigest()


def test_patch_error(repo: str, status: str, approved_updates: list[str], *, staged: bool = False) -> str:
    try:
        for path in filter(test_related, status_paths(status)):
            if reason := test_change_error(path, _patch(repo, status, path, staged=staged), approved_updates):
                return reason
    except ValueError as exc:
        return str(exc)
    return ""
