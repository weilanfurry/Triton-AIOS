"""技能②：系统界面（直接改 HTML / CSS 源码）。

和 ui_api 的 ui_apply 不同：ui_apply 是运行时改元素样式（刷新就没了），
本技能改的是 static/index.html、static/styles.css 源码，是持久的界面改造。

安全措施：写前自动备份 → 结构校验 → 校验不过拒绝写入 → 成功后让前端 reload。
"""

import json
import re
import time
from pathlib import Path
from typing import Any

from .base import Skill, register

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"
BACKUP_DIR = STATIC_DIR / ".ui-backups"

WRITABLE = {"index.html", "styles.css", "veli.css"}
READABLE = {"index.html", "styles.css", "veli.css", "app.js"}
MAX_BYTES = 400 * 1024

# index.html 必须保留的关键节点（改坏了系统就起不来了）
REQUIRED_IN_HTML = [
    ('id="triton-desktop"', '桌面根容器 div#triton-desktop'),
    ('id="chat-input"', '对话输入框 #chat-input'),
    ('id="send-btn"', '发送按钮 #send-btn'),
    ('/static/app.js', '主脚本引用 /static/app.js'),
]


def _err(message: str, **extra: Any) -> str:
    payload = {"ok": False, "error": message}
    payload.update(extra)
    return json.dumps(payload, ensure_ascii=False)


def _target(name: str, writable: bool) -> Path | None:
    base = str(name or "").strip().lstrip("/")
    allowed = WRITABLE if writable else READABLE
    if base not in allowed:
        return None
    return STATIC_DIR / base


def _stamp() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


def _backup(path: Path) -> str:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = _stamp()
    while (BACKUP_DIR / (path.name + "." + stamp + ".bak")).exists():
        time.sleep(1.0)
        stamp = _stamp()
    (BACKUP_DIR / (path.name + "." + stamp + ".bak")).write_text(
        path.read_text(encoding="utf-8", errors="replace"), encoding="utf-8"
    )
    return stamp


def _validate(name: str, content: str) -> list[str]:
    """写入前的结构校验，返回缺失项列表（空 = 通过）。"""
    missing: list[str] = []
    if name == "index.html":
        for needle, label in REQUIRED_IN_HTML:
            if needle not in content:
                missing.append(label)
        if "</html>" not in content.lower():
            missing.append("结束标签 </html>")
    elif name in {"styles.css", "veli.css"}:
        if "#triton-desktop" not in content:
            missing.append("#triton-desktop 作用域（整个桌面样式都挂在它下面）")
        if content.count("{") != content.count("}"):
            missing.append("花括号不配平（{ 与 } 数量不一致）")
    return missing


def _bump_asset_version(asset: str = "styles.css") -> str:
    """改完样式文件后把 index.html 里的 ?v= 版本号顶一下，避免浏览器拿缓存。"""
    index = STATIC_DIR / "index.html"
    try:
        html = index.read_text(encoding="utf-8")
    except OSError:
        return ""
    stamp = time.strftime("%Y%m%d%H%M%S")
    pattern = r"(/static/" + re.escape(asset) + r"\?v=)[A-Za-z0-9._-]+"
    new_html, count = re.subn(pattern, r"\g<1>" + stamp, html)
    if count:
        index.write_text(new_html, encoding="utf-8")
        return stamp
    return ""


# ===== 工具 =====

def tool_html_read(args: dict) -> str:
    name = str(args.get("file") or "index.html")
    path = _target(name, writable=False)
    if path is None or not path.is_file():
        return _err("只能读这些文件：" + ", ".join(sorted(READABLE)) + "（没有 " + name + "）")
    text = path.read_text(encoding="utf-8", errors="replace")
    limit = 60000
    return json.dumps({
        "ok": True,
        "file": name,
        "bytes": len(text.encode("utf-8")),
        "lines": text.count("\n") + 1,
        "truncated": len(text) > limit,
        "content": text[:limit],
    }, ensure_ascii=False)


def _write(name: str, content: str) -> str:
    path = _target(name, writable=True)
    if path is None:
        return _err("只能改这些文件：" + ", ".join(sorted(WRITABLE)) + "（不能改 " + str(name) + "）")
    if len(content.encode("utf-8")) > MAX_BYTES:
        return _err(name + " 超过 " + str(MAX_BYTES // 1024) + "KB，拒绝写入")
    missing = _validate(name, content)
    if missing:
        return _err(
            name + " 缺少关键结构，已拒绝写入（原文件保持不变）",
            missing=missing,
            hint="改界面时不要删掉这些节点；先 html_read 看现状，再用 html_patch 做小步修改。",
        )
    if not path.is_file():
        return _err("文件不存在：" + name)
    stamp = _backup(path)
    path.write_text(content, encoding="utf-8")
    bumped = _bump_asset_version(name) if name in {"styles.css", "veli.css"} else ""
    return json.dumps({
        "ok": True,
        "file": name,
        "bytes": len(content.encode("utf-8")),
        "backup": "static/.ui-backups/" + name + "." + stamp + ".bak",
        "cache_bust": bumped,
        "note": "已写入。请调用 ui_reload 让桌面刷新，然后描述你改了什么。",
    }, ensure_ascii=False)


def tool_html_write(args: dict) -> str:
    return _write(str(args.get("file") or ""), str(args.get("content") or ""))


def tool_html_patch(args: dict) -> str:
    name = str(args.get("file") or "")
    path = _target(name, writable=True)
    if path is None or not path.is_file():
        return _err("只能改这些文件：" + ", ".join(sorted(WRITABLE)) + "（没有 " + name + "）")
    find = str(args.get("find") or "")
    replace = str(args.get("replace") or "")
    if not find:
        return _err("find 不能为空")
    old = path.read_text(encoding="utf-8", errors="replace")
    count = old.count(find)
    if count == 0:
        return _err("在文件里没找到 find 的内容（原文没变）",
                    hint="先用 html_read 看真实文本，注意空格和引号要完全一致。")
    if count > 1 and not args.get("all"):
        return _err("find 在文件里出现 " + str(count) + " 次，会造成误改；请给更长的上下文，或加 all=true 全部替换")
    new = old.replace(find, replace) if args.get("all") else old.replace(find, replace, 1)
    return _write(name, new)


def tool_html_backups(args: dict) -> str:
    if not BACKUP_DIR.is_dir():
        return json.dumps({"ok": True, "backups": []}, ensure_ascii=False)
    items = sorted(BACKUP_DIR.glob("*.bak"), key=lambda p: p.stat().st_mtime, reverse=True)[:30]
    return json.dumps({
        "ok": True,
        "backups": [{"file": p.name, "bytes": p.stat().st_size} for p in items],
    }, ensure_ascii=False)


def tool_html_restore(args: dict) -> str:
    file_name = str(args.get("file") or "")
    path = _target(file_name, writable=True)
    if path is None:
        return _err("只能还原这些文件：" + ", ".join(sorted(WRITABLE)))
    stamp = str(args.get("stamp") or args.get("backup") or "").strip()
    candidates = sorted(BACKUP_DIR.glob(file_name + ".*.bak"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidates:
        return _err("没有 " + file_name + " 的备份")
    chosen = None
    if stamp:
        for cand in candidates:
            if stamp in cand.name:
                chosen = cand
                break
        if chosen is None:
            return _err("没找到匹配 " + stamp + " 的备份", backups=[c.name for c in candidates[:10]])
    else:
        chosen = candidates[0]
    content = chosen.read_text(encoding="utf-8", errors="replace")
    missing = _validate(file_name, content)
    if missing:
        return _err("备份本身缺结构，没有还原", missing=missing)
    _backup(path)
    path.write_text(content, encoding="utf-8")
    return json.dumps({
        "ok": True,
        "file": file_name,
        "restored_from": chosen.name,
        "note": "已还原，请调用 ui_reload 让桌面刷新。",
    }, ensure_ascii=False)


def tool_ui_reload(args: dict) -> str:
    return json.dumps({
        "ok": True,
        "note": "已通知桌面刷新页面；刷新后新的 HTML / CSS 生效。",
    }, ensure_ascii=False)


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "html_read",
            "description": "读取界面源码（static/index.html、static/styles.css、static/veli.css，也可读 app.js）。改之前必须先读真实内容。",
            "parameters": {
                "type": "object",
                "properties": {"file": {"type": "string", "description": "index.html | styles.css | veli.css | app.js"}},
                "required": ["file"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "html_patch",
            "description": "在源码里做精确替换（推荐的小步修改方式）。find 必须与文件里的真实文本完全一致。",
            "parameters": {
                "type": "object",
                "properties": {
                    "file": {"type": "string", "description": "index.html | styles.css | veli.css"},
                    "find": {"type": "string", "description": "要被替换的原文（含足够上下文）"},
                    "replace": {"type": "string", "description": "替换成的新内容"},
                    "all": {"type": "boolean", "description": "true = 全部替换，默认只替换第一处"},
                },
                "required": ["file", "find", "replace"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "html_write",
            "description": "整体重写源码（大改版式时用）。写前会自动备份并做结构校验，缺关键节点会被拒绝。",
            "parameters": {
                "type": "object",
                "properties": {
                    "file": {"type": "string", "description": "index.html | styles.css | veli.css"},
                    "content": {"type": "string", "description": "完整的新文件内容"},
                },
                "required": ["file", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "html_backups",
            "description": "列出界面源码的历史备份（想还原时先看有哪些版本）。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "html_restore",
            "description": "把源码还原到某个备份版本（不给 stamp 就还原最近一次）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "file": {"type": "string", "description": "index.html | styles.css | veli.css"},
                    "stamp": {"type": "string", "description": "备份文件名里的时间戳，例如 20261012-204501"},
                },
                "required": ["file"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ui_reload",
            "description": "让桌面刷新页面，使刚才的 HTML / CSS 改动生效（改完必须调用一次）。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]


register(Skill(
    id="ui_html",
    name="系统界面",
    icon="layout-template",
    description="改 HTML / CSS 源码来换桌面外观（持久生效，可回滚）",
    hint="说清想把界面改成什么样，例如：把顶部状态栏改成胶囊风格",
    order=20,
    triggers=("改html", "改 html", "改界面源码", "改系统界面", "换界面", "换个界面", "界面改成", "改前端源码", "改样式表"),
    tools=TOOLS,
    executors={
        "html_read": tool_html_read,
        "html_patch": tool_html_patch,
        "html_write": tool_html_write,
        "html_backups": tool_html_backups,
        "html_restore": tool_html_restore,
        "ui_reload": tool_ui_reload,
    },
    core_scope="none",
    prompt=(
        "你是 AIOS 的「界面工程师」，通过改 static/index.html、static/styles.css 与 static/veli.css 的源码来改变系统界面"
        "（不是运行时改样式，是真正改文件，刷新后依然生效）。\n"
        "工作流程：\n"
        "1. 先 html_read 读真实源码：index.html 看结构；veli.css 是 VeliUI 主题层"
        "（颜色 / 圆角 / 阴影 token 都在这里，换配色、改质感优先改它）；styles.css 是各组件的基础布局与样式。\n"
        "2. 优先 html_patch 小步修改；只有整块重构时才 html_write 整文件。\n"
        "3. 改完调用 ui_reload，让桌面刷新；然后告诉用户改了什么、改坏了可以用 html_restore 回滚。\n"
        "硬性约束：\n"
        "- 必须保留 #triton-desktop、#chat-input、#send-btn、/static/app.js 这些关键节点，否则会被拒绝写入。\n"
        "- 不要删掉别人的功能：只动与需求相关的部分，其它 DOM / 样式原样保留。\n"
        "- 颜色尽量用现有 CSS 变量（--accent / --surface / --text 等），深浅色主题都要能看。\n"
        "- 不要引入外部 CDN，也不要往 index.html 里塞大段 inline script（逻辑留在 app.js，本技能不改 app.js）。"
    ),
))
