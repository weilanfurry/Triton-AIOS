"""Python 运行时 API —— 桌面「Python 运行时」窗口 + 小 AI 的 Python 工具共用后端。

提供四件事：
1. 脚本目录（默认与系统工作区共用）的浏览 / 读取 / 新建 / 保存 / 重命名 / 删除
2. 运行时环境信息：解释器版本、可执行文件、平台、已安装第三方库
3. 真实执行：运行 .py 文件（支持 NDJSON 流式输出、超时、手动停止）
4. 一次性代码片段执行（窗口控制台里的快速试算）

可选配置（写进 config.json，或用环境变量）：
    python_dir      / PYTHON_SCRIPTS_DIR   脚本目录，默认与工作区共用
    python_bin      / PYTHON_BIN           解释器路径，默认当前进程的 sys.executable
    python_timeout  / PYTHON_TIMEOUT       单次运行超时秒数，默认 60
"""

import importlib.metadata as metadata
import codecs
import json
import os
import platform
import queue
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Iterator

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app_registry import app_abs_path, find_app
from openai_api import BASE_DIR

router = APIRouter(prefix="/api/python", tags=["python"])

MAX_READ = 400 * 1024
MAX_OUTPUT = 60_000
DEFAULT_TIMEOUT = 60
TEXT_EXTS = {".py", ".pyw", ".txt", ".json", ".csv", ".md", ".log", ".ini", ".cfg", ".yaml", ".yml", ".tsv"}
SKIP_DIRS = {"__pycache__", ".git", ".venv", "venv", "node_modules", ".idea", ".vscode", ".pytest_cache"}
HIGHLIGHTS = ["fastapi", "uvicorn", "httpx", "requests", "numpy", "pandas", "matplotlib", "pillow", "pyyaml"]

# 正在运行的子进程（run_id -> Popen），供「停止」接口使用
RUNNING: dict[str, subprocess.Popen] = {}


# ---------------------------------------------------------------- 基础工具

def _read_config() -> dict:
    cfg_file = BASE_DIR / "config.json"
    if cfg_file.exists():
        try:
            data = json.loads(cfg_file.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except (OSError, json.JSONDecodeError):
            pass
    return {}


def python_manifest() -> dict | None:
    """注册表里 kind=python 的应用（apps/ 下的 Python 运行时文件夹）。"""
    return find_app(kind="python")


def scripts_dir() -> Path:
    """脚本目录：环境变量 / config 优先，其次 Python 应用的 workdir，最后工作区兜底。"""
    raw = os.environ.get("PYTHON_SCRIPTS_DIR") or str(_read_config().get("python_dir") or "")
    raw = raw.strip()
    if raw:
        p = Path(raw).expanduser()
        target = p if p.is_absolute() else (BASE_DIR / p)
    else:
        app = python_manifest()
        if app is not None:
            target = app_abs_path(app, app.get("workdir") or "workspace")
        else:
            target = BASE_DIR / "workspace"
    target.mkdir(parents=True, exist_ok=True)
    return target.resolve()


def python_exe() -> str:
    raw = os.environ.get("PYTHON_BIN") or str(_read_config().get("python_bin") or "")
    return raw.strip() or sys.executable


def run_timeout() -> float:
    raw = os.environ.get("PYTHON_TIMEOUT") or _read_config().get("python_timeout") or DEFAULT_TIMEOUT
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return float(DEFAULT_TIMEOUT)
    if value == 0:
        return 0.0  # 0 = 不限时（适合 GUI 窗口 / 长任务，靠「停止」手动结束）
    return value if 0 < value <= 3600 else float(DEFAULT_TIMEOUT)


def resolve_script(rel: str, base: Path | None = None) -> Path | None:
    """把相对路径安全解析到脚本目录内；越界返回 None。"""
    root = (base or scripts_dir()).resolve()
    clean = str(rel or "").strip().replace("\\", "/")
    target = (root / clean).resolve()
    if target != root and root not in target.parents:
        return None
    return target


def installed_packages() -> list[dict]:
    """当前解释器里已安装的第三方库（名字 + 版本）。"""
    found: dict[str, str] = {}
    try:
        for dist in metadata.distributions():
            name = str(dist.metadata.get("Name") or "").strip()
            if name:
                found[name] = str(dist.version or "")
    except Exception:  # 元数据损坏时不让窗口挂掉
        pass
    items = [{"name": n, "version": v} for n, v in found.items()]
    items.sort(key=lambda d: d["name"].lower())
    return items


def runtime_info() -> dict:
    """运行时环境快照：版本、解释器、平台、脚本目录、已安装库。"""
    packages = installed_packages()
    versions = {p["name"].lower(): p["version"] for p in packages}
    return {
        "version": sys.version.split()[0],
        "version_full": sys.version.replace("\n", " "),
        "implementation": platform.python_implementation(),
        "executable": python_exe(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "prefix": sys.prefix,
        "dir": str(scripts_dir()),
        "timeout": run_timeout(),
        "packages": packages,
        "package_count": len(packages),
        "highlights": [f"{n} {versions[n.lower()]}" for n in HIGHLIGHTS if n.lower() in versions],
    }


def env_summary() -> dict:
    """给主控提示词用的轻量运行时摘要。"""
    info = runtime_info()
    return {
        "version": info["version"],
        "executable": info["executable"],
        "dir": info["dir"],
        "timeout": info["timeout"],
        "package_count": info["package_count"],
        "highlights": info["highlights"],
    }


def file_tree(base: Path, node: Path | None = None) -> list[dict]:
    """脚本目录树（跳过缓存目录与隐藏文件）。"""
    current = node or base
    items: list[dict] = []
    if not current.is_dir():
        return items
    for child in sorted(current.iterdir(), key=lambda p: (p.is_file(), p.name.lower())):
        if child.name.startswith("."):
            continue
        if child.is_dir():
            if child.name in SKIP_DIRS:
                continue
            items.append({
                "name": child.name,
                "type": "dir",
                "path": child.relative_to(base).as_posix(),
                "children": file_tree(base, child),
            })
        elif child.is_file():
            ext = child.suffix.lower()
            items.append({
                "name": child.name,
                "type": "file",
                "path": child.relative_to(base).as_posix(),
                "size": child.stat().st_size,
                "ext": ext.lstrip("."),
                "python": ext in {".py", ".pyw"},
                "text": ext in TEXT_EXTS,
            })
    return items


# ---------------------------------------------------------------- 执行引擎

def _child_env() -> dict:
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["PYTHONUNBUFFERED"] = "1"
    return env


def _popen(cmd: list[str], cwd: Path, want_stdin: bool = False, text_mode: bool = True) -> subprocess.Popen:
    kwargs: dict = {}
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    streams = {
        "cwd": str(cwd),
        "stdin": subprocess.PIPE if want_stdin else subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "env": _child_env(),
        **kwargs,
    }
    if text_mode:
        return subprocess.Popen(cmd, text=True, encoding="utf-8", errors="replace", bufsize=1, **streams)
    # 二进制 + 无缓冲：读的时候按块拿，不换行的片段（比如 input() 的提示符）也能立刻到达
    return subprocess.Popen(cmd, bufsize=0, **streams)


def _kill(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    try:
        proc.kill()
    except OSError:
        pass


def _cmd_for(target: Path, argv: list[str] | None = None) -> list[str]:
    # -u：强制无缓冲，保证窗口里的输出能实时刷出来
    return [python_exe(), "-u", str(target), *(argv or [])]


def _trim(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n…（输出超过 {limit} 字符，已截断）"


def _result(ok: bool, code: int | None, out: str, err: str, started: float, **extra) -> dict:
    data = {
        "ok": ok,
        "exit_code": code,
        "stdout": _trim(out or "", MAX_OUTPUT),
        "stderr": _trim(err or "", MAX_OUTPUT),
        "duration": round(time.time() - started, 2),
        "python": python_exe(),
    }
    data.update(extra)
    return data


def run_script(
    path: str,
    base: Path | None = None,
    args: list[str] | None = None,
    stdin: str | None = None,
    timeout: float | None = None,
) -> dict:
    """真实运行脚本目录里的一个 .py 文件（同步阻塞版，供小 AI 工具与接口共用）。"""
    root = (base or scripts_dir()).resolve()
    target = resolve_script(path, root)
    if target is None:
        return {"ok": False, "error": "路径越界：只能运行脚本目录内的文件"}
    if not target.is_file():
        return {"ok": False, "error": f"文件不存在：{path}"}
    if target.suffix.lower() not in {".py", ".pyw"}:
        return {"ok": False, "error": "只能运行 .py 文件"}
    limit = float(timeout if timeout is not None else run_timeout())
    started = time.time()
    wait = limit if limit > 0 else None
    try:
        proc = _popen(_cmd_for(target, args), root, want_stdin=stdin is not None)
    except OSError as exc:
        return {"ok": False, "error": f"启动失败：{exc}"}
    try:
        out, err = proc.communicate(input=stdin, timeout=wait)
    except subprocess.TimeoutExpired:
        _kill(proc)
        try:
            out, err = proc.communicate(timeout=5)
        except (subprocess.TimeoutExpired, OSError):
            out, err = "", ""
        return _result(False, None, out, err, started, error=f"运行超时（超过 {limit:g} 秒）", timeout=True)
    return _result(proc.returncode == 0, proc.returncode, out, err, started, cwd=str(root), path=target.relative_to(root).as_posix())


def _line(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False) + "\n"


def _stream_run(target: Path, root: Path, argv: list[str], stdin_text: str | None, limit: float) -> Iterator[str]:
    """NDJSON 流式执行：逐行推送 stdout / stderr，最后推退出码。"""
    run_id = uuid.uuid4().hex[:8]
    cmd = _cmd_for(target, argv)
    started = time.time()
    yield _line({
        "type": "start",
        "run_id": run_id,
        "cmd": cmd,
        "cwd": str(root),
        "python": python_exe(),
        "timeout": limit,
    })
    try:
        proc = _popen(cmd, root, want_stdin=stdin_text is not None, text_mode=False)
    except OSError as exc:
        yield _line({"type": "error", "text": f"启动失败：{exc}"})
        yield _line({"type": "exit", "code": -1, "duration": 0.0})
        return

    RUNNING[run_id] = proc
    events: "queue.Queue" = queue.Queue()
    sentinel = object()

    def pump(stream, tag: str) -> None:
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        try:
            while True:
                chunk = stream.read(4096)
                if not chunk:
                    break
                text = decoder.decode(chunk)
                if text:
                    events.put((tag, text))
            tail = decoder.decode(b"", True)
            if tail:
                events.put((tag, tail))
        except (OSError, ValueError):
            pass
        finally:
            events.put(sentinel)

    for stream, tag in ((proc.stdout, "stdout"), (proc.stderr, "stderr")):
        threading.Thread(target=pump, args=(stream, tag), daemon=True).start()

    if stdin_text is not None:
        try:
            proc.stdin.write((stdin_text if stdin_text.endswith("\n") else stdin_text + "\n").encode("utf-8"))
            proc.stdin.flush()
        except (OSError, ValueError):
            pass
        finally:
            try:
                proc.stdin.close()
            except (OSError, ValueError):
                pass

    deadline = (started + limit) if limit > 0 else None
    pending = 2
    timed_out = False
    try:
        while pending > 0:
            try:
                item = events.get(timeout=0.2)
            except queue.Empty:
                if proc.poll() is not None and events.empty():
                    break
                if deadline and time.time() > deadline:
                    timed_out = True
                    _kill(proc)
                    break
                continue
            if item is sentinel:
                pending -= 1
                continue
            tag, text = item
            yield _line({"type": tag, "text": text})
            if deadline and time.time() > deadline and proc.poll() is None:
                timed_out = True
                _kill(proc)
                break
    finally:
        # 注意：这里只做清理，不能再 yield —— 客户端中断（点「停止」）时
        # 生成器会被 close() 抛出 GeneratorExit，在 finally 里 yield 会报
        # "generator ignored GeneratorExit"。
        if proc.poll() is None:
            _kill(proc)
        try:
            proc.wait(timeout=5)
        except (subprocess.TimeoutExpired, OSError):
            pass
        RUNNING.pop(run_id, None)
    if timed_out:
        yield _line({"type": "error", "text": f"运行超过 {limit:g} 秒，已自动停止"})
    yield _line({"type": "exit", "code": proc.returncode, "duration": round(time.time() - started, 2)})


# ---------------------------------------------------------------- 交互式终端会话

SESSION_HISTORY = 4000      # 每个会话保留的最大输出事件数
SESSION_MAX = 12            # 同时保留的会话数上限
SESSION_KEEP = 3600         # 已退出会话保留时长（秒），便于窗口回看输出

# 终端会话用自带驱动脚本启动，而不是 `python -i`：
# - `python -i` 的提示符写进 stderr 且不带换行，要等进程退出才刷出来，界面上会看到一串迟到的 ">>>"；
# - 这里用 code.InteractiveConsole 自己驱动，提示符留空、结果走 stdout（-u 无缓冲），输出就是实时的；
# - 脚本先执行、之后进入同一个全局命名空间继续交互，所以脚本里的变量在终端里还能接着用。
# 用法：python -u -c <DRIVER> [--repl|--once] [脚本路径|-] [脚本参数...]
SESSION_DRIVER = (
    "import code, sys\n"
    "\n"
    "\n"
    "def _show(exc):\n"
    "    import traceback\n"
    "    tb = exc.__traceback__\n"
    "    if tb is not None and tb.tb_next is not None:\n"
    "        tb = tb.tb_next\n"
    "    traceback.print_exception(type(exc), exc, tb)\n"
    "\n"
    "\n"
    "argv = sys.argv[1:]\n"
    "mode = argv[0] if argv else '--repl'\n"
    "rest = argv[1:]\n"
    "target = rest[0] if rest and rest[0] != '-' else ''\n"
    "ns = {'__name__': '__main__'}\n"
    "if target:\n"
    "    ns['__file__'] = target\n"
    "    sys.argv = [target] + rest[1:]\n"
    "    import os\n"
    # 跟 `python 脚本路径` 一样，把脚本所在目录放到 sys.path[0]，
    # 否则脚本里 `from fib import ...` 这种同目录导入会 ModuleNotFoundError
    "    _dir = os.path.dirname(os.path.abspath(target))\n"
    "    if _dir:\n"
    "        sys.path.insert(0, _dir)\n"
    "    with open(target, encoding='utf-8') as fh:\n"
    "        _src = fh.read()\n"
    "    try:\n"
    "        exec(compile(_src, target, 'exec'), ns)\n"
    "    except SystemExit as exc:\n"
    # `python 脚本.py` 遇到 SystemExit 是静默退出并带上退出码；
    # `python -i 脚本.py` 是打印出来、继续留在 REPL —— 这里按模式区分。
    # 退出码 0（脚本正常跑完，很多脚本结尾写 raise SystemExit(main())）只给一句提示，
    # 免得用户把 SystemExit 的 traceback 当成报错。
    "        if mode == '--once':\n"
    "            raise\n"
    "        _code = getattr(exc, 'code', None)\n"
    "        if _code in (None, 0):\n"
    "            print()\n"
    "            print('[脚本执行结束，退出码 0 · 下面是交互模式]')\n"
    "        else:\n"
    "            _show(exc)\n"
    "    except BaseException as exc:\n"
    "        _show(exc)\n"
    "        if mode == '--once':\n"
    "            sys.exit(1)\n"
    "else:\n"
    "    sys.argv = ['']\n"
    "if mode != '--once':\n"
    "    sys.ps1 = sys.ps2 = ''\n"
    "    code.InteractiveConsole(ns).interact(banner='', exitmsg='')\n"
)


class PySession:
    """一个长驻的交互式 Python 进程：stdin 可写、stdout/stderr 增量可读。

    与 run_script 的一次性执行不同，会话没有超时，变量与状态在多次输入之间保留，
    适合「终端」窗口里的连续输入输出。
    """

    def __init__(self, sid: str, cmd: list[str], cwd: Path, label: str, mode: str):
        self.id = sid
        self.cmd = cmd
        self.cwd = str(cwd)
        self.label = label
        self.mode = mode
        self.started = time.time()
        self.lock = threading.Lock()
        self.events: list[dict] = []
        self.seq = 0
        self.exit_code: int | None = None
        self.proc = _popen(cmd, cwd, want_stdin=True, text_mode=False)
        for stream, tag in ((self.proc.stdout, "out"), (self.proc.stderr, "err")):
            threading.Thread(target=self._pump, args=(stream, tag), daemon=True).start()
        threading.Thread(target=self._watch, daemon=True).start()

    def _push(self, kind: str, text: str) -> None:
        with self.lock:
            self.seq += 1
            self.events.append({"seq": self.seq, "type": kind, "text": text})
            if len(self.events) > SESSION_HISTORY:
                del self.events[:1000]

    def _pump(self, stream, tag: str) -> None:
        # 按块读 + 增量解码：程序没换行就把提示符写出来时（input 的提示），也能马上送到窗口
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        try:
            while True:
                chunk = stream.read(4096)
                if not chunk:
                    break
                text = decoder.decode(chunk)
                if text:
                    self._push(tag, text)
            tail = decoder.decode(b"", True)
            if tail:
                self._push(tag, tail)
        except (OSError, ValueError):
            pass

    def _watch(self) -> None:
        try:
            code = self.proc.wait()
        except OSError:
            code = -1
        self.exit_code = code
        self._push("exit", f"[会话已结束，退出码 {code}]")

    def alive(self) -> bool:
        return self.proc.poll() is None

    def write(self, text: str) -> dict:
        if not self.alive():
            return {"ok": False, "message": "会话已结束，请重新启动"}
        data = (text if text.endswith("\n") else text + "\n").encode("utf-8")
        try:
            self.proc.stdin.write(data)
            self.proc.stdin.flush()
        except (OSError, ValueError) as exc:
            return {"ok": False, "message": f"写入失败：{exc}"}
        return {"ok": True}

    def stop(self) -> dict:
        _kill(self.proc)
        return {"ok": True}

    def poll(self, after: int = 0) -> dict:
        with self.lock:
            events = [e for e in self.events if e["seq"] > after]
            seq = self.seq
        return {
            "events": events,
            "seq": seq,
            "alive": self.alive(),
            "exit_code": self.exit_code,
            "info": self.info(),
        }

    def info(self) -> dict:
        # 驱动脚本很长，回给前端时用占位符代替，避免响应里塞一大段源码
        shown = ["<repl-driver>" if item == SESSION_DRIVER else item for item in self.cmd]
        return {
            "session_id": self.id,
            "label": self.label,
            "mode": self.mode,
            "cmd": shown,
            "dir": self.cwd,
            "started": self.started,
            "alive": self.alive(),
            "exit_code": self.exit_code,
        }


SESSIONS: dict[str, PySession] = {}


def _reap_sessions() -> None:
    """清掉过期/超量的已退出会话，避免长跑的服务堆积进程句柄。"""
    now = time.time()
    for sid, sess in list(SESSIONS.items()):
        if sess.exit_code is not None and now - sess.started > SESSION_KEEP:
            SESSIONS.pop(sid, None)
    if len(SESSIONS) <= SESSION_MAX:
        return
    finished = sorted(
        (s for s in SESSIONS.values() if s.exit_code is not None),
        key=lambda s: s.started,
    )
    for sess in finished[: max(0, len(SESSIONS) - SESSION_MAX)]:
        SESSIONS.pop(sess.id, None)


def stop_all_sessions() -> None:
    """服务退出时收掉所有会话进程。"""
    for sess in list(SESSIONS.values()):
        try:
            sess.stop()
        except Exception:
            pass

# ---------------------------------------------------------------- 接口模型

class FileIn(BaseModel):
    path: str
    content: str = ""


class NewIn(BaseModel):
    path: str
    content: str = ""
    kind: str = "file"


class RenameIn(BaseModel):
    path: str
    new_name: str


class DeleteIn(BaseModel):
    path: str


class RunIn(BaseModel):
    path: str
    args: list[str] = []
    stdin: str | None = None


class CodeIn(BaseModel):
    code: str
    args: list[str] = []
    stdin: str | None = None


class StopIn(BaseModel):
    run_id: str


class SessionStart(BaseModel):
    path: str = ""
    args: list[str] = []
    mode: str = "script-repl"   # repl | script | script-repl


class SessionText(BaseModel):
    session_id: str
    text: str = ""


class SessionRef(BaseModel):
    session_id: str


# ---------------------------------------------------------------- 接口

@router.get("/info")
async def python_info() -> dict:
    """运行时环境：解释器、平台、脚本目录、已安装库。"""
    return runtime_info()


@router.get("/files")
async def python_files() -> dict:
    root = scripts_dir()
    tree = file_tree(root)
    count = 0
    stack = list(tree)
    while stack:
        node = stack.pop()
        if node["type"] == "file":
            count += 1
        else:
            stack.extend(node.get("children") or [])
    return {
        "dir": str(root),
        "name": root.name,
        "files": tree,
        "count": count,
        "python": {
            "version": sys.version.split()[0],
            "executable": python_exe(),
            "timeout": run_timeout(),
        },
    }


@router.get("/file")
async def python_read(path: str) -> dict:
    target = resolve_script(path)
    if target is None:
        raise HTTPException(status_code=400, detail="路径越界")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    text = target.read_text(encoding="utf-8", errors="replace")
    return {
        "path": path,
        "content": text[:MAX_READ],
        "size": target.stat().st_size,
        "truncated": len(text) > MAX_READ,
    }


@router.put("/file")
async def python_write(body: FileIn) -> dict:
    target = resolve_script(body.path)
    if target is None:
        raise HTTPException(status_code=400, detail="路径越界")
    if target == scripts_dir():
        raise HTTPException(status_code=400, detail="缺少文件名")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body.content, encoding="utf-8", newline="")
    return {"ok": True, "path": body.path, "size": target.stat().st_size}


@router.post("/new")
async def python_new(body: NewIn) -> dict:
    rel = str(body.path or "").strip().replace("\\", "/").strip("/")
    if not rel:
        raise HTTPException(status_code=400, detail="请提供名称")
    target = resolve_script(rel)
    if target is None or target == scripts_dir():
        raise HTTPException(status_code=400, detail="路径越界")
    if body.kind == "dir":
        if target.exists():
            raise HTTPException(status_code=400, detail="已存在同名文件或文件夹")
        target.mkdir(parents=True, exist_ok=True)
        return {"ok": True, "path": rel, "type": "dir"}
    if target.exists():
        raise HTTPException(status_code=400, detail="已存在同名文件")
    if target.suffix == "":
        target = target.with_suffix(".py")
        rel = target.relative_to(scripts_dir()).as_posix()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body.content, encoding="utf-8", newline="")
    return {"ok": True, "path": rel, "type": "file"}


@router.post("/rename")
async def python_rename(body: RenameIn) -> dict:
    target = resolve_script(body.path)
    if target is None or not target.exists():
        raise HTTPException(status_code=404, detail="文件不存在")
    name = str(body.new_name or "").strip().replace("\\", "/").strip("/")
    if not name or "/" in name:
        raise HTTPException(status_code=400, detail="新名称不合法")
    new = target.with_name(name)
    if new.exists():
        raise HTTPException(status_code=400, detail="目标已存在")
    target.rename(new)
    return {"ok": True, "path": body.path, "new_path": new.relative_to(scripts_dir()).as_posix()}


@router.post("/delete")
async def python_delete(body: DeleteIn) -> dict:
    target = resolve_script(body.path)
    if target is None or target == scripts_dir():
        raise HTTPException(status_code=400, detail="路径越界")
    if not target.exists():
        raise HTTPException(status_code=404, detail="文件不存在")
    if target.is_dir():
        if any(target.iterdir()):
            raise HTTPException(status_code=400, detail="目录非空，禁止删除")
        target.rmdir()
    else:
        target.unlink()
    return {"ok": True, "path": body.path}


@router.post("/stop")
async def python_stop(body: StopIn) -> dict:
    """停止一个正在运行的脚本。"""
    proc = RUNNING.get(body.run_id)
    if proc is None:
        return {"ok": False, "message": "该进程已结束"}
    _kill(proc)
    return {"ok": True, "message": "已停止"}


@router.post("/run")
async def python_run(body: RunIn) -> dict:
    """同步运行一个 .py 文件，返回完整 stdout / stderr。"""
    return run_script(body.path, args=body.args, stdin=body.stdin)


@router.post("/run/stream")
async def python_run_stream(body: RunIn) -> StreamingResponse:
    """流式运行 .py 文件：NDJSON，每行一个事件（start/stdout/stderr/exit）。"""
    root = scripts_dir()
    target = resolve_script(body.path, root)
    if target is None:
        raise HTTPException(status_code=400, detail="路径越界")
    if not target.is_file():
        raise HTTPException(status_code=404, detail=f"文件不存在：{body.path}")
    if target.suffix.lower() not in {".py", ".pyw"}:
        raise HTTPException(status_code=400, detail="只能运行 .py 文件")
    return StreamingResponse(
        _stream_run(target, root, list(body.args or []), body.stdin, run_timeout()),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@router.post("/eval")
async def python_eval(body: CodeIn) -> dict:
    """执行一段代码片段（等价 `python -u -c`），用于窗口控制台里快速试算。"""
    code = str(body.code or "")
    if not code.strip():
        return {"ok": False, "error": "请输入要执行的代码"}
    root = scripts_dir()
    started = time.time()
    limit = run_timeout()
    wait = limit if limit > 0 else None
    cmd = [python_exe(), "-u", "-c", code, *(body.args or [])]
    try:
        proc = _popen(cmd, root, want_stdin=body.stdin is not None)
    except OSError as exc:
        return {"ok": False, "error": f"启动失败：{exc}"}
    try:
        out, err = proc.communicate(input=body.stdin, timeout=wait)
    except subprocess.TimeoutExpired:
        _kill(proc)
        try:
            out, err = proc.communicate(timeout=5)
        except (subprocess.TimeoutExpired, OSError):
            out, err = "", ""
        return _result(False, None, out, err, started, error=f"运行超时（超过 {limit:g} 秒）", timeout=True)
    return _result(proc.returncode == 0, proc.returncode, out, err, started, cwd=str(root))


# ---------------------------------------------------------------- 终端会话接口

SESSION_MODES = {
    "repl": "交互 REPL",
    "script": "运行脚本",
    "script-repl": "运行脚本 + 交互",
}


@router.get("/sessions")
async def python_sessions() -> dict:
    """当前保留的终端会话（新的在前），窗口重开时可以重新接上。"""
    _reap_sessions()
    items = sorted(SESSIONS.values(), key=lambda s: s.started, reverse=True)
    return {"sessions": [s.info() for s in items], "python": python_exe()}


@router.post("/sessions/start")
async def python_session_start(body: SessionStart) -> dict:
    """启动一个长驻交互会话：repl / script / script-repl。"""
    root = scripts_dir()
    mode = str(body.mode or "script-repl").strip().lower()
    if mode not in SESSION_MODES:
        mode = "script-repl"
    cmd = [python_exe(), "-u", "-c", SESSION_DRIVER, "--once" if mode == "script" else "--repl"]
    target: Path | None = None
    label = "REPL"
    if mode in {"script", "script-repl"}:
        raw = str(body.path or "").strip()
        if not raw:
            raise HTTPException(status_code=400, detail="请选择要运行的脚本")
        target = resolve_script(raw, root)
        if target is None:
            raise HTTPException(status_code=400, detail="路径越界")
        if not target.is_file():
            raise HTTPException(status_code=404, detail=f"文件不存在：{raw}")
        if target.suffix.lower() not in {".py", ".pyw"}:
            raise HTTPException(status_code=400, detail="只能运行 .py 文件")
        label = target.relative_to(root).as_posix()
    cmd.append(str(target) if target is not None else "-")
    if target is not None:
        cmd += [str(a) for a in (body.args or [])]
    sid = uuid.uuid4().hex[:8]
    try:
        sess = PySession(sid, cmd, root, label, mode)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"启动失败：{exc}")
    SESSIONS[sid] = sess
    _reap_sessions()
    return {
        "ok": True,
        **sess.info(),
        "banner": f"Python {sys.version.split()[0]} · {label} · {SESSION_MODES[mode]}",
    }


@router.get("/sessions/poll")
async def python_session_poll(session_id: str, after: int = 0) -> dict:
    """增量拉取会话输出（after 之后的事件），供终端窗口轮询。"""
    sess = SESSIONS.get(session_id)
    if sess is None:
        raise HTTPException(status_code=404, detail="会话不存在或已被清理")
    return sess.poll(int(after or 0))


@router.post("/sessions/input")
async def python_session_input(body: SessionText) -> dict:
    """把一行输入写进会话的 stdin（末尾自动补换行）。"""
    sess = SESSIONS.get(body.session_id)
    if sess is None:
        raise HTTPException(status_code=404, detail="会话不存在或已被清理")
    return sess.write(str(body.text or ""))


@router.post("/sessions/stop")
async def python_session_stop(body: SessionRef) -> dict:
    """结束一个会话（相当于关掉这个终端进程）。"""
    sess = SESSIONS.get(body.session_id)
    if sess is None:
        return {"ok": False, "message": "会话已结束"}
    return sess.stop()


@router.post("/sessions/stop_all")
async def python_sessions_stop_all() -> dict:
    """退出前收掉所有会话进程（也便于用户一键清场）。"""
    stop_all_sessions()
    return {"ok": True, "sessions": len(SESSIONS)}
