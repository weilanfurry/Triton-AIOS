"""界面控制 API：桌面把 UI 快照上报给后端，小 AI 通过 ui_apply 下发改动指令。

数据流：
    前端 collectUiSnapshot()  →  POST /api/ui/snapshot    （后端保存最新一份快照）
    小 AI ui_list             →  读最新快照（真实的元素 key / 位置 / 大小 / 可见性）
    小 AI ui_apply            →  SSE 推 {"type":"ui_command","commands":[...]}
                                 → 前端真的改样式 → 改完立刻重新上报快照

这样小 AI 看到的是"改完之后"的真实状态，不会凭空想象。
"""

import time
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/api/ui", tags=["ui"])

MAX_ELEMENTS = 500          # 单次快照最多保留的元素数
MAX_HISTORY = 50            # 修改指令历史条数

ALLOWED_ACTIONS = {"move", "resize", "hide", "show", "color", "text", "style", "reset"}

# 允许 AI 改的 CSS 属性（够用且不至于把界面改坏）
ALLOWED_STYLE_PROPS = {
    "background", "background-color", "background-image", "color", "border-color", "border-width",
    "border-style", "border-radius", "opacity", "font-size", "font-weight", "font-family",
    "letter-spacing", "text-shadow", "box-shadow", "padding", "margin", "filter", "transform",
    "backdrop-filter", "outline", "outline-color", "z-index",
}

SNAPSHOT: dict[str, Any] = {}
HISTORY: list[dict] = []


class SnapshotIn(BaseModel):
    client: str | None = None
    viewport: dict | None = None
    windows: list[dict] | None = None
    elements: list[dict] = []
    last_applied: list[dict] = []


class CommandsIn(BaseModel):
    client: str | None = None
    commands: list[dict] = []


@router.post("/snapshot")
async def post_snapshot(body: SnapshotIn) -> dict:
    """前端上报当前界面状态（元素清单 + 视口 + 已打开的窗口）。"""
    global SNAPSHOT
    SNAPSHOT = {
        "at": time.time(),
        "client": body.client or "desktop",
        "viewport": body.viewport or {},
        "windows": body.windows or [],
        "elements": body.elements[:MAX_ELEMENTS],
        "last_applied": body.last_applied or [],
    }
    return {"ok": True, "count": len(SNAPSHOT["elements"]), "at": SNAPSHOT["at"]}


@router.get("/snapshot")
async def get_snapshot() -> dict:
    """最近一份界面快照（调试 / 其它窗口也可以直接用）。"""
    return SNAPSHOT or {"at": 0, "elements": [], "windows": [], "viewport": {}}


@router.post("/applied")
async def post_applied(body: CommandsIn) -> dict:
    """前端执行完指令后回报结果，便于排查"AI 以为改了、其实没改"的情况。"""
    if body.commands:
        HISTORY.append({"at": time.time(), "items": body.commands[:20]})
        del HISTORY[:-MAX_HISTORY]
    return {"ok": True, "history": len(HISTORY)}


def latest() -> dict:
    return SNAPSHOT


def history() -> list[dict]:
    return list(HISTORY)


def compact(snapshot: dict | None = None, limit: int = 80) -> dict:
    """给小 AI 看的精简快照：只留 key / 类型 / 文字 / 位置 / 可见性。"""
    snap = snapshot if snapshot is not None else SNAPSHOT
    if not snap:
        return {
            "ready": False,
            "message": "桌面还没有上报界面快照（让用户刷新一下页面即可）",
            "elements": [],
        }
    items = []
    for el in (snap.get("elements") or [])[:limit]:
        item = {
            "key": el.get("key"),
            "type": el.get("type"),
            "text": (el.get("text") or "")[:40],
            "rect": el.get("rect"),
            "visible": el.get("visible"),
        }
        if el.get("app"):
            item["app"] = el.get("app")
        if el.get("window"):
            item["window"] = el.get("window")
        items.append(item)
    return {
        "ready": True,
        "client": snap.get("client"),
        "age_seconds": round(max(0.0, time.time() - float(snap.get("at") or 0)), 1),
        "viewport": snap.get("viewport") or {},
        "windows": snap.get("windows") or [],
        "total": len(snap.get("elements") or []),
        "shown": len(items),
        "elements": items,
        "last_applied": snap.get("last_applied") or [],
    }


def normalize_commands(raw: list[dict]) -> tuple[list[dict], list[str]]:
    """校验并规整 AI 下发的指令；返回 (可用指令, 错误说明)。"""
    commands: list[dict] = []
    errors: list[str] = []
    for i, item in enumerate(raw or []):
        if not isinstance(item, dict):
            errors.append(f"第 {i + 1} 条不是对象，已跳过")
            continue
        target = str(item.get("target") or item.get("key") or "").strip()
        action = str(item.get("action") or "").strip().lower()
        if not target:
            errors.append(f"第 {i + 1} 条缺少 target，已跳过")
            continue
        if action not in ALLOWED_ACTIONS:
            errors.append(f"第 {i + 1} 条的 action「{action}」不支持（可用：{'、'.join(sorted(ALLOWED_ACTIONS))}）")
            continue
        cmd: dict[str, Any] = {"target": target, "action": action}
        if action == "move":
            try:
                cmd["x"] = float(item.get("x"))
                cmd["y"] = float(item.get("y"))
            except (TypeError, ValueError):
                errors.append(f"第 {i + 1} 条 move 需要数字 x / y")
                continue
        elif action == "resize":
            for field in ("width", "height"):
                value = item.get(field)
                if value is None:
                    continue
                try:
                    cmd[field] = max(16.0, min(4000.0, float(value)))
                except (TypeError, ValueError):
                    errors.append(f"第 {i + 1} 条 resize 的 {field} 不是数字")
                    break
            else:
                if "width" not in cmd and "height" not in cmd:
                    errors.append(f"第 {i + 1} 条 resize 至少要给 width 或 height")
                    continue
        elif action == "color":
            value = str(item.get("value") or "").strip()
            if not value:
                errors.append(f"第 {i + 1} 条 color 缺少 value")
                continue
            prop = str(item.get("property") or "background").strip().lower()
            if prop not in {"background", "background-color", "color", "border-color"}:
                prop = "background"
            cmd["property"] = prop
            cmd["value"] = value[:60]
        elif action == "text":
            cmd["value"] = str(item.get("value") if item.get("value") is not None else "")[:200]
        elif action == "style":
            props = item.get("properties")
            if not isinstance(props, dict) or not props:
                errors.append(f"第 {i + 1} 条 style 需要 properties 对象")
                continue
            clean = {}
            for k, v in props.items():
                name = str(k).strip().lower()
                if name not in ALLOWED_STYLE_PROPS:
                    errors.append(f"style 属性 {name} 不在允许列表里，已忽略")
                    continue
                clean[name] = str(v)[:120]
            if not clean:
                errors.append(f"第 {i + 1} 条 style 没有可用属性")
                continue
            cmd["properties"] = clean
        commands.append(cmd)
    return commands, errors
