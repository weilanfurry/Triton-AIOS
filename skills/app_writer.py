"""技能①：AI 写应用。

AI 先读 SDK（docs/APP_SDK.md），再用 app_create 把 manifest.json + index.html
（可带 app.js / style.css）一次性写进 apps/<app_id>/。
生成完立刻能被 AIOS 当系统应用打开（kind=html → 前端用 iframe 窗口承载）。
"""

import json
import re
from pathlib import Path
from typing import Any

from .base import Skill, register

BASE_DIR = Path(__file__).resolve().parent.parent
APPS_DIR = BASE_DIR / "apps"
SDK_FILE = BASE_DIR / "docs" / "APP_SDK.md"

APP_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,40}$")
ALLOWED_EXT = {".html", ".htm", ".css", ".js", ".mjs", ".json", ".md", ".txt", ".svg", ".csv"}
MAX_FILES = 40
MAX_FILE_BYTES = 200 * 1024
SDK_MAX_CHARS = 9000


def _err(message: str, **extra: Any) -> str:
    payload = {"ok": False, "error": message}
    payload.update(extra)
    return json.dumps(payload, ensure_ascii=False)


def _app_dir(app_id: str) -> Path | None:
    """把 app_id 解析成 apps/<id>；非法 / 越界返回 None。"""
    aid = str(app_id or "").strip().lower()
    if not APP_ID_RE.match(aid):
        return None
    target = (APPS_DIR / aid).resolve()
    if APPS_DIR.resolve() not in target.parents:
        return None
    return target


def _safe_rel(path: str) -> str | None:
    """规整应用内的相对文件路径；越界 / 扩展名不允许返回 None。"""
    rel = str(path or "").replace("\\", "/").strip().lstrip("/")
    if not rel or ".." in rel.split("/"):
        return None
    if Path(rel).suffix.lower() not in ALLOWED_EXT:
        return None
    return rel


def _normalize_files(raw: Any) -> tuple[dict[str, str], list[str]]:
    """files 允许是 {path: content} 或 [{path, content}]；返回 (文件表, 错误说明)。"""
    files: dict[str, str] = {}
    problems: list[str] = []
    items: list[tuple[str, Any]] = []
    if isinstance(raw, dict):
        items = [(str(k), v) for k, v in raw.items()]
    elif isinstance(raw, list):
        for entry in raw:
            if isinstance(entry, dict):
                items.append((str(entry.get("path") or entry.get("name") or ""), entry.get("content", "")))
    for path, content in items:
        rel = _safe_rel(path)
        if not rel:
            problems.append(f"跳过非法路径：{path}")
            continue
        text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False, indent=2)
        if len(text.encode("utf-8")) > MAX_FILE_BYTES:
            problems.append(f"{rel} 超过 {MAX_FILE_BYTES // 1024}KB，已跳过")
            continue
        files[rel] = text
    return files, problems


def _existing_apps() -> list[dict]:
    out = []
    if not APPS_DIR.is_dir():
        return out
    for folder in sorted(APPS_DIR.iterdir()):
        manifest = folder / "manifest.json"
        if not manifest.is_file():
            continue
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        out.append({
            "id": folder.name,
            "name": data.get("name") or folder.name,
            "kind": data.get("kind") or "generic",
            "entry": data.get("entry") or "",
        })
    return out


# ===== 工具 =====

def tool_app_sdk(args: dict) -> str:
    """把 SDK 文档 + 现有应用清单交给模型。"""
    try:
        text = SDK_FILE.read_text(encoding="utf-8")
    except OSError:
        text = "（SDK 文档缺失，请按 manifest.json + index.html 的最小结构生成。）"
    return json.dumps({
        "ok": True,
        "sdk": text[:SDK_MAX_CHARS],
        "existing_apps": _existing_apps(),
        "rules": [
            "用 app_create 一次写完，不要逐个文件写",
            "kind 必须是 html，entry 必须是 index.html",
            "生成后用 open_window(app='<app_id>') 打开给用户看",
        ],
    }, ensure_ascii=False)


def tool_app_list(args: dict) -> str:
    return json.dumps({"ok": True, "apps": _existing_apps()}, ensure_ascii=False)


def tool_app_create(args: dict) -> str:
    app_id = str(args.get("id") or args.get("app_id") or "").strip().lower()
    folder = _app_dir(app_id)
    if folder is None:
        return _err("应用 id 不合法：只能用小写字母 / 数字 / 下划线 / 减号，2-41 位，例如 ai_pomodoro")

    files, problems = _normalize_files(args.get("files"))
    if len(files) > MAX_FILES:
        return _err(f"文件太多了（{len(files)} 个，上限 {MAX_FILES}）")
    entry = str(args.get("entry") or "index.html").replace("\\", "/").lstrip("/")
    if entry not in files:
        return _err(f"files 里必须有入口文件 {entry}（通常是完整的 index.html）", files=sorted(files))

    name = str(args.get("name") or app_id).strip()[:40] or app_id
    icon = str(args.get("icon") or "app-window").strip()[:40] or "app-window"
    description = str(args.get("description") or "").strip()[:200]

    manifest: dict[str, Any] = {
        "name": name,
        "icon": icon,
        "kind": "html",
        "description": description,
        "entry": entry,
        "version": "1.0.0",
        "author": "AI (Triton Skill)",
    }
    # 允许模型自己写 manifest.json，但关键字段以工具参数为准
    raw_manifest = files.pop("manifest.json", "")
    if raw_manifest:
        try:
            extra = json.loads(raw_manifest)
            if isinstance(extra, dict):
                manifest.update({k: v for k, v in extra.items() if k in {"window", "description", "version", "tags"}})
        except json.JSONDecodeError:
            pass
    if isinstance(args.get("window"), dict):
        manifest["window"] = args["window"]

    folder.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    for rel, text in sorted(files.items()):
        target = (folder / rel).resolve()
        if folder.resolve() not in target.parents:
            problems.append(f"跳过越界路径：{rel}")
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        written.append(rel)
    (folder / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    return json.dumps({
        "ok": True,
        "app_id": app_id,
        "name": name,
        "dir": f"apps/{app_id}",
        "files": ["manifest.json"] + written,
        "problems": problems,
        "open_hint": f"open_window(app='{app_id}')",
        "url": f"/api/apps/{app_id}/asset/{entry}",
    }, ensure_ascii=False)


def tool_app_write(args: dict) -> str:
    app_id = str(args.get("id") or args.get("app_id") or "").strip().lower()
    folder = _app_dir(app_id)
    if folder is None or not (folder / "manifest.json").is_file():
        return _err(f"应用不存在或 id 非法：{app_id}（先用 app_create 建）")
    rel = _safe_rel(str(args.get("path") or ""))
    if not rel or rel == "manifest.json":
        return _err("path 不合法（不能写 manifest.json，改应用信息请用 app_create 覆盖）")
    content = args.get("content")
    text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False, indent=2)
    if len(text.encode("utf-8")) > MAX_FILE_BYTES:
        return _err(f"文件超过 {MAX_FILE_BYTES // 1024}KB")
    target = (folder / rel).resolve()
    if folder.resolve() not in target.parents:
        return _err("路径越界")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return json.dumps({"ok": True, "app_id": app_id, "path": rel, "bytes": len(text.encode("utf-8"))}, ensure_ascii=False)


def tool_app_read(args: dict) -> str:
    app_id = str(args.get("id") or args.get("app_id") or "").strip().lower()
    folder = _app_dir(app_id)
    rel = _safe_rel(str(args.get("path") or "index.html"))
    if folder is None or not rel:
        return _err("参数不合法")
    target = (folder / rel).resolve()
    if folder.resolve() not in target.parents or not target.is_file():
        return _err(f"文件不存在：apps/{app_id}/{rel}")
    text = target.read_text(encoding="utf-8", errors="replace")
    return json.dumps({"ok": True, "path": f"apps/{app_id}/{rel}", "content": text[:40000]}, ensure_ascii=False)


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "app_sdk",
            "description": "读取 AIOS 应用 SDK（manifest 规范、可用宿主 API、样式约定、硬性规则）和已装应用清单。写应用前必须先调用它。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "app_create",
            "description": (
                "创建（或覆盖）一个可在 AIOS 里直接打开的网页应用。"
                "files 是 {相对路径: 文件内容} 的对象，必须包含 index.html 的完整内容；"
                "可同时写入 app.js / style.css / 其它静态文件。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "应用 id，小写字母/数字/下划线，例如 ai_pomodoro"},
                    "name": {"type": "string", "description": "应用显示名，例如 番茄钟"},
                    "description": {"type": "string", "description": "一句话说明"},
                    "icon": {"type": "string", "description": "lucide 图标名，例如 timer / calculator / sticky-note"},
                    "entry": {"type": "string", "description": "入口文件，默认 index.html"},
                    "window": {"type": "object", "description": "建议窗口尺寸，例如 {\"width\":420,\"height\":380}"},
                    "files": {
                        "type": "object",
                        "description": "{文件相对路径: 文本内容}，例如 {\"index.html\": \"<!doctype html>...\"}",
                    },
                },
                "required": ["id", "name", "files"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "app_write",
            "description": "改写已生成应用里的单个文件（改 bug / 加功能时用），path 相对应用目录。",
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "应用 id"},
                    "path": {"type": "string", "description": "相对路径，例如 index.html"},
                    "content": {"type": "string", "description": "完整的新内容"},
                },
                "required": ["id", "path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "app_read",
            "description": "读回已生成应用里的文件内容（自查 / 改代码前确认现状）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "应用 id"},
                    "path": {"type": "string", "description": "相对路径，默认 index.html"},
                },
                "required": ["id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "app_list",
            "description": "列出 apps/ 里现有的应用（id / 名称 / 类型 / 入口）。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]


register(Skill(
    id="app_writer",
    name="AI 写应用",
    icon="wand-sparkles",
    description="用一句话生成可运行的小应用，直接出现在桌面任务栏里",
    hint="描述你想要的应用，例如：做个番茄钟小工具",
    order=10,
    triggers=(
        "写个应用", "写一个应用", "做个应用", "做一个应用", "生成应用",
        "写个小工具", "做个小工具", "写个网页应用", "做个网页应用",
        "帮我做个app", "帮我写个app", "做个app",
        r"re:(写|做|生成|开发)(个|一个|一款)?[^\s，。]{0,8}(小工具|小应用|应用|工具|app)",
    ),
    tools=TOOLS,
    executors={
        "app_sdk": tool_app_sdk,
        "app_list": tool_app_list,
        "app_create": tool_app_create,
        "app_write": tool_app_write,
        "app_read": tool_app_read,
    },
    core_scope="base",
    shows_workspace=True,
    prompt=(
        "你是 AIOS 的「应用工坊」。用户会用一句话描述想要的小工具，你要直接产出可运行的应用。\n"
        "工作流程（必须遵守）：\n"
        "1. 先调用 app_sdk 读 SDK 与现有应用清单（不要凭记忆写 manifest）。\n"
        "2. 想清楚功能后用 app_create 一次性写完 files（index.html 必须是完整可直接打开的页面）。\n"
        "3. 需要多文件就用相对路径引用；需要存数据就按 SDK 写工作区文件。\n"
        "4. 生成完调用 open_window(app='<app_id>')，让桌面立刻弹出这个应用。\n"
        "5. 回复里给出：应用名、它能做什么、刚才真的创建了哪些文件（用工具返回的真实路径）。\n"
        "禁止：要求用户自己建文件 / 自己装依赖 / 自己跑构建；禁止引用外部 CDN；禁止只给代码不落盘。"
    ),
))
