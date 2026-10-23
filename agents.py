"""大-小 AI 设计（Beta 2.5 落地）。

- 大 AI（主控）：意图理解、Agent 匹配、上下文注入、结果汇总
- 小 AI（应用）：角色提示词 + 工作目录 + 自主规划（工具调用）
- 系统执行器：执行小 AI 规划的工具调用，把真实结果回传给小 AI

当前已注册音乐小 AI（music_list / music_play）、系统工作区小 AI（file_list / file_read / file_write）
与 Python 运行时小 AI（python_env / python_run）。
"""

import base64
import inspect
import json
import mimetypes
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
from fastapi import APIRouter
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from app_registry import find_app, get_app as find_app_by_id
from conversations_api import read_conversations as read_saved_conversations
from music_api import AUDIO_EXTS, get_music_dir, record_recent
from openai_api import load_config
from python_api import env_summary as python_env_summary
from python_api import runtime_info as python_runtime_info
from workspace_api import list_tree, resolve_in_root, run_python, workspace_root

import qomochat_client
import skills
import ui_api
from config_store import read_raw as read_app_config

router = APIRouter(prefix="/api", tags=["chat"])

# 工具调用轮数安全阀：只用于防止极端情况下的死循环，正常任务不会触发。
MAX_TOOL_ROUNDS = 200
# 触发安全阀时的兜底：不再中断任务，而是让模型基于已有结果直接收尾。
FORCE_ANSWER_HINT = "（系统提示：请基于以上工具结果直接给出最终回答，不要再调用工具。）"


def build_system_prompt(skill: "skills.Skill | None" = None) -> str:
    """从注册表里的应用 manifest 动态生成主控提示词；选中技能时追加技能提示词。"""
    lines = [
        "你是 Triton AIOS 的主控（大 AI），负责理解用户意图、拆解任务、匹配合适的小 AI、注入上下文并汇总结果。",
        "",
        "当前注册的小 AI：",
    ]
    idx = 0
    music = find_app(kind="music")
    if music:
        idx += 1
        lines.append(f"{idx}. music_agent（{music.get('name', '音乐小 AI')}）")
        lines.append(f"   角色：{music.get('role_prompt', '你是音乐小 AI，负责读取和播放音乐库。')}")
        lines.append(f"   工作目录：{get_music_dir()}")
        lines.append("   可用工具：music_list（读取音乐列表）、music_play（播放音乐库中的歌）")
    workspace_app = find_app(kind="workspace")
    if workspace_app:
        idx += 1
        lines.append(f"{idx}. workspace_agent（{workspace_app.get('name', '工作区小 AI')}）")
        lines.append(f"   角色：{workspace_app.get('role_prompt', '你负责在工作区文件夹中读取、创建和修改文件。')}")
        lines.append(f"   工作目录：{workspace_root()}")
        lines.append("   可用工具：file_list（列出文件）、file_read（读取文件）、file_write（写入文件）、python_run（运行工作区里的 .py 文件）")
    qomo_app = find_app(kind="qomochat")
    if qomo_app:
        idx += 1
        lines.append(f"{idx}. qomochat_agent（{qomo_app.get('name', 'QomoChat 小 AI')}）")
        lines.append(
            f"   角色：{qomo_app.get('role_prompt', '你负责代表用户操作 QomoChat 聊天服务。')}"
        )
        lines.append(f"   服务地址：{qomochat_client.base_url()}")
        lines.append("   可用工具：" + "、".join(qomochat_client.TOOL_NAMES))
    python_app = find_app(kind="python")
    if python_app:
        idx += 1
        info = python_env_summary()
        lines.append(f"{idx}. python_agent（{python_app.get('name', 'Python 运行时')}）")
        lines.append(
            f"   角色：{python_app.get('role_prompt', '你负责在脚本目录里编写并真实运行 Python 代码。')}"
        )
        lines.append(f"   脚本目录：{info['dir']}（与系统工作区共用同一份文件）")
        lines.append(
            f"   运行时：Python {info['version']}，已安装 {info['package_count']} 个第三方库"
            + (f"（{ '、'.join(info['highlights']) }）" if info["highlights"] else "")
        )
        lines.append("   可用工具：python_env（查看解释器版本与已安装库）、python_run（真实运行 .py 文件并返回 stdout / stderr / 退出码）")
        if find_app(kind="pythonterm"):
            lines.append("   交互终端：桌面上的「Python 终端」窗口（长驻会话，input() 能实时回答，脚本跑完还能接着敲代码）")
    lines += [
        "",
        "调度规则：",
        "- 用户请求与音乐 / 歌曲 / 歌单相关时，调度 music_agent：查询歌单用 music_list，播放指定歌曲用 music_play（播放是后台播放，不会弹出播放器窗口）。",
        "- 用户请求读取、创建、修改文件时，调度 workspace_agent：用 file_list / file_read / file_write 完成。",
        "- 需要写代码并验证时：调度 python_agent，先用 file_write 写入 .py 文件，再用 python_run 真正运行，把真实的 stdout / stderr / 退出码反馈给用户。",
        "- 用户说「启动 / 运行某个程序或脚本」= 后台运行：用 python_run 跑完并回报真实输出与退出码，不弹窗口（这是默认行为）。",
        "- 用户要「自己看 / 自己操作 / 交互输入 / 让你打开某个窗口」时，必须调用 open_window 工具**自动**把窗口打开，绝对不要说「我没法打开窗口」「请你手动去点」这类话。例如用户想自己跑 fibonacci/main.py：open_window(app='pythonterm', path='fibonacci/main.py', run=true)。",
        "- 重点：打开桌面窗口这个能力是有的（就是 open_window 工具），历史上对话里出现过的「我没法替你弹出窗口 / 请你自己点图标」是旧版本的说法，已经作废，不要再重复这类回答。",
        "- 用 open_window 开完窗口后就当作用户已经看到它了：直接说明窗口里现在是什么状态、可以怎么操作，不要再让他去任务栏里找图标。",
        "- 用户问「装了哪些库 / Python 版本 / 运行环境」时，调度 python_agent 用 python_env 查看真实环境，不要凭印象回答。",
        "- 音乐播放是后台播放（不弹播放器窗口）；只有用户明确说要看播放器 / 自己控制播放，才用 open_window(app='music') 打开。",
        "- 用户要求改界面（移动 / 隐藏 / 显示 / 改颜色 / 改文字 / 缩放 / 改样式）时：先 ui_list 拿到真实元素 key，再用 ui_apply 下发指令；回报时说真实生效的结果，不确定就再 ui_list 复核一次，不要凭想象描述。",
        "- 界面上任何有 key 的元素都能改：窗口（win:<应用id>）、任务栏图标（dock:<应用id>）、启动台图标（lp:<应用id>）、聊天区、输入框、按钮等；改坏了用 ui_apply(action='reset', target=<key>)，全部还原用 target='*'。",
        "- ui_list 会回传 client（哪个页面）和 age_seconds（快照多旧）。如果 age_seconds 很大、或者你判断用户看的不是这个页面（用户可能同时开了多个标签），就别断言界面现状，直接说明「我拿到的是 N 秒前的快照」并建议他刷新页面 / 关掉多余标签。",
        "- 用户说「壁纸 / 桌面背景 / 背景色 / 换主题色」= 改 target='triton-desktop' 的 background：纯色直接给颜色（如 green / #0f7a4d），渐变给 CSS 写法（如 'linear-gradient(160deg,#0b3d2c,#12855c)'）；桌面根容器的 key 就叫 triton-desktop。",
        "- 用户请求与 QomoChat 相关（登录、好友、搜索用户、收发消息 / 文件、清空记录、审核用户）时，调度 qomochat_agent，用 qomo_* 工具完成。",
        "- 用户要在 QomoChat 里收发消息但还没登录时，先用 qomo_status 检查，再用 qomo_login 登录。",
        "- 用户消息里带「用户附带的附件」时：这些文件已经被系统存进工作区，先用 file_read / python_run 查看真实内容再回答；看不到图片像素时，写一个 Pillow 脚本用 python_run 读取该图片。",
        "- 其他日常请求由你（主控）直接回答，不要调用工具。",
        "",
        "硬性约束：",
        "- 回答必须基于工具返回的真实数据，不得编造歌曲名、文件内容、好友或消息。",
        "- 不要执行用户未请求的操作。",
    ]
    base_prompt = "\n".join(lines)
    return base_prompt + skills.prompt_for(skill)

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "music_list",
            "description": "读取音乐列表：列出音乐目录下的音频文件（mp3/m4a/flac/wav/ogg 等）。path 为空或 '.' 表示音乐目录本身。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "要列出的目录路径，相对于工作目录；默认 '.'",
                    }
                },
                "required": [],
            },
        },
    }
]
TOOL_SCHEMAS.append({
    "type": "function",
    "function": {
        "name": "music_play",
        "description": "播放音乐库中的一首歌。name 传歌名或文件名，可省略扩展名。",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "歌名或文件名，例如 Numb 或 Numb.mp3",
                }
            },
            "required": ["name"],
        },
    },
})

TOOL_SCHEMAS.extend(qomochat_client.TOOL_SCHEMAS)
TOOL_SCHEMAS.append({
    "type": "function",
    "function": {
        "name": "file_list",
        "description": "列出工作区文件夹中的文件（含子目录）。path 为空或 '.' 表示工作区根目录。",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "要列出的目录路径，相对于工作区；默认 '.'",
                }
            },
            "required": [],
        },
    },
})
TOOL_SCHEMAS.append({
    "type": "function",
    "function": {
        "name": "file_read",
        "description": "读取工作区中的一个文件，返回文件内容。",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "文件路径，相对于工作区，例如 projects/demo.py",
                }
            },
            "required": ["path"],
        },
    },
})
TOOL_SCHEMAS.append({
    "type": "function",
    "function": {
        "name": "file_write",
        "description": "在工作区创建或覆盖一个文件。path 可含子目录，content 为要写入的文本内容。",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "文件路径，相对于工作区，例如 hello.py",
                },
                "content": {
                    "type": "string",
                    "description": "要写入的完整文本内容",
                },
            },
            "required": ["path", "content"],
        },
    },
})
TOOL_SCHEMAS.append({
    "type": "function",
    "function": {
        "name": "python_run",
            "description": "运行工作区里的一个 .py 文件并返回真实输出（stdout/stderr）与退出码。运行目录锁定在工作区，默认超时 60 秒。",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "要运行的 Python 文件路径，相对于工作区，例如 hello.py",
                }
            },
            "required": ["path"],
        },
    },
})
TOOL_SCHEMAS.append({
    "type": "function",
    "function": {
        "name": "python_env",
        "description": "查看 Python 运行时的真实环境：解释器版本、可执行文件路径、脚本目录、已安装的第三方库列表。回答「装了哪些库」「Python 版本」这类问题时先调用它。",
        "parameters": {
            "type": "object",
            "properties": {
                "packages_only": {
                    "type": "boolean",
                    "description": "为 true 时只返回已安装库列表；默认 false 返回完整环境信息",
                }
            },
            "required": [],
        },
    },
})
TOOL_SCHEMAS.append({
    "type": "function",
    "function": {
        "name": "open_window",
        "description": (
            "在桌面上自动替用户打开应用窗口（不要说让他自己手动点，直接调用本工具即可）。"
            "app 取值：python=Python 运行时（看/改脚本）、pythonterm=Python 终端（可交互输入）、"
            "music=音乐播放器、files=文件、qomochat=QomoChat、control=系统控制（手动改界面 / 还原）、settings=设置。"
            "app 也可以直接填 apps/ 里的应用 id（例如「AI 写应用」刚生成的 ai_pomodoro）。"
            "可选 path 指定要打开的脚本；run=true 表示打开后直接在 Python 终端里运行这个脚本。"
            "只在用户想看界面、想自己操作或需要交互输入时使用；单纯「跑个程序看结果」用 python_run 后台跑就行。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "app": {
                    "type": "string",
                    "description": "要打开的窗口：python | pythonterm | music | files | qomochat",
                },
                "path": {
                    "type": "string",
                    "description": "可选：要打开的脚本路径（相对于工作区），例如 fibonacci/main.py",
                },
                "run": {
                    "type": "boolean",
                    "description": "可选：true = 打开后在 Python 终端里直接启动该脚本（需要 path）",
                },
                "args": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "可选：传给脚本的命令行参数",
                },
            },
            "required": ["app"],
        },
    },
})

# open_window 的 app 别名 → 前端 window kind
WINDOW_KINDS = {
    "python": "python", "runtime": "python", "py": "python", "运行时": "python", "python运行": "python",
    "pythonterm": "pythonterm", "terminal": "pythonterm", "term": "pythonterm", "终端": "pythonterm",
    "music": "music", "player": "music", "音乐": "music",
    "files": "workspace", "file": "workspace", "workspace": "workspace", "文件": "workspace",
    "qomochat": "qomochat", "qomo": "qomochat", "聊天": "qomochat",
    "control": "control", "系统控制": "control", "界面控制": "control",
    "settings": "settings", "设置": "settings",
}

# 系统自带窗口（不在 apps/ 目录里，find_app 扫不到）
SYSTEM_WINDOWS = {"control": "系统控制", "settings": "设置"}

TOOL_SCHEMAS.append({
    "type": "function",
    "function": {
        "name": "ui_list",
        "description": (
            "读取桌面当前的界面元素清单（真实数据）：每个元素的 key、类型、文字、位置大小、是否可见。"
            "用户要求改界面（移动 / 隐藏 / 改颜色 / 改文字 / 缩放 / 改样式）时，先调用它拿真实的 key，再 ui_apply。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "最多返回多少个元素，默认 80"},
            },
            "required": [],
        },
    },
})
TOOL_SCHEMAS.append({
    "type": "function",
    "function": {
        "name": "ui_apply",
        "description": (
            "直接修改桌面上的任意 UI 元素（真实生效）。commands 是数组，每条格式："
            "{target: 元素 key（'*' 表示所有被改过的元素）, action: move|resize|hide|show|color|text|style|reset, ...}。"
            "move 需要 x/y（元素左上角在桌面中的坐标，px）；resize 需要 width/height；"
            "color 需要 value（可带 property: background | background-color | color | border-color）；"
            "text 需要 value（改元素文字）；style 需要 properties 对象（支持的属性见后端白名单，如 background、color、border-radius、opacity、font-size…）；"
            "reset 表示还原该元素（配 target='*' 可一次还原全部）。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "commands": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "要执行的 UI 修改指令列表",
                }
            },
            "required": ["commands"],
        },
    },
})

SESSIONS: dict[str, list[dict[str, Any]]] = {}


def history_from_saved(session_id: str) -> list[dict]:
    """会话记忆丢失（刷新/重启）时，从已保存的对话里恢复历史。"""
    for conv in read_saved_conversations():
        if str(conv.get("id")) != str(session_id):
            continue
        history: list[dict] = []
        for m in (conv.get("msgs") or [])[-20:]:
            text = str(m.get("text") or "").strip()
            if not text:
                continue
            history.append({
                "role": "user" if m.get("role") == "user" else "assistant",
                "content": text,
            })
        return history
    return []


def session_history(session_id: str) -> list[dict]:
    history = SESSIONS.get(session_id)
    if history is None:
        history = history_from_saved(session_id)
        SESSIONS[session_id] = history
    return history


class ChatError(Exception):
    def __init__(self, status: int, message: str):
        self.status = status
        self.message = message


def execute_tool(name: str, args: dict) -> str:
    """系统执行器：执行小 AI 规划的工具调用。"""
    if name.startswith("qomo_"):
        return qomochat_client.execute_tool(name, args)
    if name == "music_list":
        workdir = get_music_dir()
        root = workdir.resolve()
        if not root.is_dir():
            return json.dumps({"error": f"工作目录不存在：{root}"}, ensure_ascii=False)
        path = str(args.get("path") or ".").strip() or "."
        target = (root / path).resolve()
        if target != root and root not in target.parents:
            return json.dumps({"error": "路径越界：禁止访问工作目录之外的文件"}, ensure_ascii=False)
        if not target.is_dir():
            return json.dumps({"error": f"目录不存在：{path}"}, ensure_ascii=False)
        entries = []
        for p in sorted(target.iterdir()):
            if p.is_file() and p.suffix.lower() in AUDIO_EXTS:
                entries.append({
                    "name": p.name,
                    "type": "file",
                    "size": p.stat().st_size,
                    "ext": p.suffix.lower().lstrip("."),
                })
        return json.dumps({"path": str(target), "entries": entries}, ensure_ascii=False)
    if name == "music_play":
        workdir = get_music_dir()
        root = workdir.resolve()
        if not root.is_dir():
            return json.dumps({"error": f"工作目录不存在：{root}"}, ensure_ascii=False)
        query = str(args.get("name") or "").strip()
        if not query:
            return json.dumps({"error": "请提供要播放的歌名，例如 music_play(name='Numb')"}, ensure_ascii=False)
        files = [p for p in root.iterdir() if p.is_file() and p.suffix.lower() in AUDIO_EXTS]
        q = query.lower()
        candidates = [p for p in files if p.name.lower() == q or p.stem.lower() == q]
        if not candidates:
            candidates = [p for p in files if p.stem.lower().startswith(q)]
        if not candidates:
            candidates = [p for p in files if q in p.stem.lower()]
        if not candidates:
            names = "、".join(p.stem for p in files)
            return json.dumps({"error": f"没有找到《{query}》。音乐库现有：{names}"}, ensure_ascii=False)
        chosen = candidates[0]
        record_recent(chosen.name)
        return json.dumps({
            "ok": True,
            "name": chosen.name,
            "title": chosen.stem,
            "url": f"/api/music/file?name={quote(chosen.name)}",
            "matches": [p.stem for p in candidates[:5]],
        }, ensure_ascii=False)
    if name == "file_list":
        r = workspace_root()
        path = str(args.get("path") or ".").strip() or "."
        target = resolve_in_root(path)
        if target is None:
            return json.dumps({"error": "路径越界：禁止访问工作区之外的文件"}, ensure_ascii=False)
        if not target.is_dir():
            return json.dumps({"error": f"目录不存在：{path}"}, ensure_ascii=False)
        return json.dumps({"path": str(target), "entries": list_tree(target, target)}, ensure_ascii=False)
    if name == "file_read":
        path = str(args.get("path") or "").strip()
        if not path:
            return json.dumps({"error": "请提供要读取的文件路径"}, ensure_ascii=False)
        target = resolve_in_root(path)
        if target is None:
            return json.dumps({"error": "路径越界：禁止访问工作区之外的文件"}, ensure_ascii=False)
        if not target.is_file():
            return json.dumps({"error": f"文件不存在：{path}"}, ensure_ascii=False)
        content = target.read_text(encoding="utf-8", errors="replace")[:200000]
        return json.dumps({"ok": True, "path": path, "content": content}, ensure_ascii=False)
    if name == "file_write":
        path = str(args.get("path") or "").strip()
        if not path:
            return json.dumps({"error": "请提供要写入的文件路径"}, ensure_ascii=False)
        target = resolve_in_root(path)
        if target is None:
            return json.dumps({"error": "路径越界：禁止访问工作区之外的文件"}, ensure_ascii=False)
        content = str(args.get("content") or "")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return json.dumps({"ok": True, "path": path, "size": target.stat().st_size}, ensure_ascii=False)
    if name == "python_run":
        path = str(args.get("path") or "").strip()
        if not path:
            return json.dumps({"error": "请提供要运行的 .py 文件路径"}, ensure_ascii=False)
        # 走工作区沙箱（cwd 锁定工作区），与文件工具同一份目录
        return json.dumps(run_python(path), ensure_ascii=False)
    if name == "python_env":
        info = python_runtime_info()
        if args.get("packages_only"):
            payload = {"package_count": info["package_count"], "packages": info["packages"]}
        else:
            payload = {k: v for k, v in info.items() if k != "packages"}
            payload["highlights"] = info["highlights"]
        return json.dumps(payload, ensure_ascii=False)
    if name == "open_window":
        raw_app = str(args.get("app") or "").strip()
        kind = WINDOW_KINDS.get(raw_app.lower(), WINDOW_KINDS.get(raw_app, ""))
        app = None
        if kind:
            app = find_app(kind=kind)
        else:
            # 允许直接按应用 id 打开（例如「AI 写应用」刚生成的应用）
            app = find_app_by_id(raw_app)
            if app is not None:
                kind = str(app.get("kind") or "generic").strip().lower() or "generic"
        if not kind:
            return json.dumps(
                {"error": f"不支持的窗口：{raw_app}（可用：python / pythonterm / music / files / qomochat / 应用 id）"},
                ensure_ascii=False,
            )
        if app is None and kind in SYSTEM_WINDOWS:
            app = {"name": SYSTEM_WINDOWS[kind], "kind": kind}
        if app is None:
            return json.dumps({"error": f"桌面没有安装 kind={kind} 的应用，无法打开"}, ensure_ascii=False)
        path = str(args.get("path") or "").strip()
        if path:
            target = resolve_in_root(path)
            if target is None:
                return json.dumps({"error": "路径越界：禁止访问工作区之外的文件"}, ensure_ascii=False)
            if not target.is_file():
                return json.dumps({"error": f"文件不存在：{path}"}, ensure_ascii=False)
        run_it = bool(args.get("run")) and bool(path)
        if run_it and kind != "pythonterm":
            kind = "pythonterm"          # 要交互运行就落到终端窗口
            app = find_app(kind="pythonterm") or app
        note = f"已让桌面自动打开「{app.get('name') or kind}」窗口"
        if path:
            note += f"，并载入 {path}"
        if run_it:
            note += "（已在终端里启动运行）"
        return json.dumps(
            {
                "ok": True,
                "app": kind,
                "app_id": app.get("id") or "",
                "app_name": app.get("name") or kind,
                "path": path or None,
                "run": run_it,
                "args": [str(a) for a in (args.get("args") or [])],
                "message": note,
            },
            ensure_ascii=False,
        )
    if name == "ui_list":
        try:
            limit = int(args.get("limit") or 80)
        except (TypeError, ValueError):
            limit = 80
        return json.dumps(ui_api.compact(limit=max(10, min(200, limit))), ensure_ascii=False)
    if name == "ui_apply":
        raw = args.get("commands")
        commands, errors = ui_api.normalize_commands(raw if isinstance(raw, list) else [])
        if not commands:
            return json.dumps(
                {"ok": False, "error": "没有可执行的界面修改指令", "details": errors},
                ensure_ascii=False,
            )
        payload: dict[str, Any] = {
            "ok": True,
            "count": len(commands),
            "commands": commands,
            "message": f"已下发 {len(commands)} 条界面修改指令，桌面上会立即生效（可再调 ui_list 复核真实结果）",
        }
        if errors:
            payload["ignored"] = errors
        return json.dumps(payload, ensure_ascii=False)
    return json.dumps({"error": f"未知工具：{name}"}, ensure_ascii=False)


# ===== 技能（Skill）=====

def tools_for(skill: "skills.Skill | None") -> list[dict]:
    """本轮暴露给模型的工具表：核心工具（按技能范围裁剪）+ 技能自带工具。"""
    return skills.tools_for(skill, TOOL_SCHEMAS)


async def run_tool(name: str, args: dict, executors: dict | None = None) -> str:
    """执行一次工具调用：技能执行器优先（支持 async），核心工具链兜底。"""
    fn = (executors or {}).get(name)
    if fn is None:
        return execute_tool(name, args)
    try:
        result = fn(args)
        if inspect.isawaitable(result):
            result = await result
    except Exception as exc:            # noqa: BLE001 - 把失败原因如实回报给模型
        return json.dumps(
            {"error": f"技能工具 {name} 执行失败：{type(exc).__name__} {exc}"}, ensure_ascii=False
        )
    return result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)


def tool_events(name: str, result: str) -> list[dict]:
    """工具执行完需要额外推给前端的事件（真实副作用，让界面跟着动）。"""
    text = (result or "").strip()
    if not text.startswith("{"):
        return []
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, dict) or not parsed.get("ok"):
        return []
    if name in {"app_create", "app_write"}:
        return [{"type": "app_changed", "app_id": parsed.get("app_id"), "action": name}]
    if name == "ui_reload":
        return [{"type": "ui_reload"}]
    if name in {"skill_create", "skill_update", "skill_delete"}:
        return [{"type": "skill_changed", "action": name, "skill": parsed.get("skill")}]
    return []


def resolve_skill(skill_id: str | None, message: str) -> tuple["skills.Skill | None", bool]:
    """决定本轮用哪个技能：显式选中的优先，其次按关键词自动匹配。

    返回 (技能, 是否自动匹配)。
    """
    skill = skills.get(skill_id)
    if skill is not None:
        return skill, False
    conf = _skill_conf()
    if conf.get("auto_match") is False:
        return None, False
    matched = skills.auto_match(message)
    return matched, matched is not None


def _skill_conf() -> dict:
    raw = read_app_config()
    conf = raw.get("skill") if isinstance(raw, dict) else None
    return conf if isinstance(conf, dict) else {}


# ===== 输入框附件（粘贴的图片 / 选择的文件）=====

# 模型名里出现这些关键字就认为支持图片输入；也可在 config.json 用 llm_vision 显式指定
VISION_HINTS = (
    "vl", "vision", "omni", "4o", "gpt-4.1", "gpt-5", "gemini", "claude",
    "doubao", "internvl", "llava", "glm-4v", "minicpm-v", "moondream",
)


def vision_enabled() -> bool:
    """是否把图片当作 image_url 直接交给模型。"""
    raw: dict[str, Any] = read_app_config()
    override = raw.get("llm_vision")
    if isinstance(override, bool):
        return override
    model = (load_config().get("model") or "").lower()
    return any(hint in model for hint in VISION_HINTS)


def attachment_block(attachments: list[dict] | None, image_hint: bool = True) -> str:
    """把附件清单写成模型能读懂的文本块。image_hint：是否提示用 Python 读图。"""
    atts = attachments or []
    if not atts:
        return ""
    lines = ["", "【用户附带的附件（已保存到工作区）】"]
    has_image = False
    for a in atts:
        kind = "图片" if str(a.get("kind")) == "image" else "文件"
        has_image = has_image or kind == "图片"
        size = a.get("size")
        size_txt = f"，{size} 字节" if isinstance(size, int) and size >= 0 else ""
        lines.append(f"- {kind}：{a.get('name') or '附件'} → 工作区路径 {a.get('path') or '（未知）'}{size_txt}")
    if has_image and image_hint:
        lines.append("（如果需要读取图片像素，可用 python_run 运行 Pillow 脚本。）")
    return "\n".join(lines)


def user_content(message: str, attachments: list[dict] | None, with_images: bool):
    """构造 user 消息 content：支持视觉时用多模态数组，否则纯文本。"""
    atts = attachments or []
    prompt = (message or "").strip()
    if not with_images or not atts:
        return prompt + attachment_block(atts, image_hint=True)
    parts: list[dict] = [{"type": "text", "text": prompt + attachment_block(atts, image_hint=False)}]
    for a in atts:
        if str(a.get("kind")) != "image":
            continue
        target = resolve_in_root(str(a.get("path") or ""))
        if target is None or not target.is_file():
            continue
        try:
            blob = target.read_bytes()
        except OSError:
            continue
        if not blob or len(blob) > 12 * 1024 * 1024:
            continue
        mime = str(a.get("mime") or "") or (mimetypes.guess_type(target.name)[0] or "image/png")
        parts.append({
            "type": "image_url",
            "image_url": {"url": f"data:{mime};base64,{base64.b64encode(blob).decode('ascii')}"},
        })
    if len(parts) == 1:
        # 图片没能塞进去（路径失效 / 文件过大），退回纯文本并提示用 Python 读图
        return prompt + attachment_block(atts, image_hint=True)
    return parts


def has_image_parts(messages: list[dict]) -> bool:
    for m in messages:
        content = m.get("content")
        if isinstance(content, list) and any(
            isinstance(p, dict) and p.get("type") == "image_url" for p in content
        ):
            return True
    return False


def downgrade_images(messages: list[dict]) -> None:
    """上游不接受图片输入时，把多模态 content 压回纯文本（保留附件路径）。"""
    for m in messages:
        content = m.get("content")
        if not isinstance(content, list):
            continue
        text = "\n".join(
            str(p.get("text") or "") for p in content
            if isinstance(p, dict) and p.get("type") == "text"
        ).strip()
        m["content"] = (
            text
            + "\n（系统提示：当前模型不支持直接看图，请改用上面的工作区路径，"
              "必要时用 python_run + Pillow 读取图片。）"
        )


async def stream_llm(messages: list[dict], tools: list[dict] | None):
    """流式调用上游；若上游拒绝图片输入，自动降级成纯文本重试一次。"""
    try:
        async for item in call_llm_stream(messages, tools):
            yield item
        return
    except ChatError as exc:
        if exc.status != 400 or not has_image_parts(messages):
            raise
        downgrade_images(messages)
        async for item in call_llm_stream(messages, tools):
            yield item


async def call_llm(messages: list[dict], tools: list[dict] | None) -> dict:
    cfg = load_config()
    if not cfg["api_key"]:
        raise ChatError(401, "未配置 LLM API Key，请先在设置里填写")
    url = cfg["base_url"] + "/chat/completions"
    payload: dict[str, Any] = {
        "model": cfg["model"],
        "messages": messages,
        "temperature": 0.6,
        "stream": False,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=10.0)) as client:
            resp = await client.post(
                url,
                headers={
                    "Authorization": f"Bearer {cfg['api_key']}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
    except httpx.HTTPError as exc:
        raise ChatError(502, f"LLM 请求失败：{exc}") from exc
    if resp.status_code >= 400:
        raise ChatError(resp.status_code, f"LLM 返回错误：{resp.text[:300]}")
    data = resp.json()
    try:
        return data["choices"][0]["message"]
    except (KeyError, IndexError) as exc:
        raise ChatError(502, "LLM 返回结构异常") from exc


async def run_chat(
    session_id: str,
    message: str,
    attachments: list[dict] | None = None,
    skill_id: str | None = None,
) -> dict:
    """主控调度：注入上下文（含技能提示词）→ 规划 → 执行器执行 → 汇总回复。"""
    skill = skills.get(skill_id)
    schemas = tools_for(skill)
    executors = skills.resolve_executors(skill)
    system = build_system_prompt(skill)
    user_text = (message or "").strip() + attachment_block(attachments)

    history = session_history(session_id)
    messages: list[dict] = [{"role": "system", "content": system}]
    messages.extend(history)
    messages.append({"role": "user", "content": user_content(message, attachments, vision_enabled())})

    tool_calls_log: list[dict] = []
    for _ in range(MAX_TOOL_ROUNDS):
        try:
            msg = await call_llm(messages, schemas)
        except ChatError as exc:
            if exc.status != 400 or not has_image_parts(messages):
                raise
            downgrade_images(messages)
            msg = await call_llm(messages, schemas)
        calls = msg.get("tool_calls")
        if not calls:
            content = msg.get("content") or "（无回复）"
            history.append({"role": "user", "content": user_text})
            history.append({"role": "assistant", "content": content})
            if len(history) > 20:
                del history[: len(history) - 20]
            return {
                "reply": content,
                "agent": "music_agent" if tool_calls_log else "main",
                "tool_calls": tool_calls_log,
            }
        messages.append(msg)
        for call in calls:
            call_id = call.get("id") or f"call_{len(tool_calls_log)}"
            func = call.get("function") or {}
            fname = func.get("name") or "unknown"
            try:
                args = json.loads(func.get("arguments") or "{}")
                if not isinstance(args, dict):
                    args = {}
            except json.JSONDecodeError:
                args = {}
            result = await run_tool(fname, args, executors)
            tool_calls_log.append({
                "step_id": len(tool_calls_log) + 1,
                "tool": fname,
                "args": args,
                "result": result[:200],
            })
            messages.append({"role": "tool", "tool_call_id": call_id, "content": result})

    messages.append({"role": "user", "content": FORCE_ANSWER_HINT})
    msg = await call_llm(messages, None)
    content = msg.get("content") or "（无回复）"
    history.append({"role": "user", "content": user_text})
    history.append({"role": "assistant", "content": content})
    if len(history) > 20:
        del history[: len(history) - 20]
    return {"reply": content, "agent": "main", "tool_calls": tool_calls_log}


async def call_llm_stream(messages: list[dict], tools: list[dict] | None):
    """流式调用 LLM，逐段产出 (kind, data)：
    delta / tool_delta / finish。
    """
    cfg = load_config()
    if not cfg["api_key"]:
        raise ChatError(401, "未配置 LLM API Key，请先在设置里填写")
    url = cfg["base_url"] + "/chat/completions"
    payload: dict[str, Any] = {
        "model": cfg["model"],
        "messages": messages,
        "temperature": 0.6,
        "stream": True,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    client = httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=10.0))
    try:
        async with client.stream(
            "POST",
            url,
            headers={
                "Authorization": f"Bearer {cfg['api_key']}",
                "Content-Type": "application/json",
            },
            json=payload,
        ) as resp:
            if resp.status_code >= 400:
                body = (await resp.aread()).decode("utf-8", errors="replace")
                raise ChatError(resp.status_code, f"LLM 返回错误：{body[:300]}")
            async for line in resp.aiter_lines():
                line = line.strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    return
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                content = delta.get("content")
                if content:
                    yield ("delta", content)
                tool_calls = delta.get("tool_calls")
                if tool_calls:
                    yield ("tool_delta", tool_calls)
                if choices[0].get("finish_reason"):
                    yield ("finish", choices[0].get("finish_reason"))
    except httpx.HTTPError as exc:
        raise ChatError(502, f"LLM 请求失败：{exc}") from exc
    finally:
        await client.aclose()


async def stream_chat(
    session_id: str,
    message: str,
    attachments: list[dict] | None = None,
    skill_id: str | None = None,
    auto_skill: "skills.Skill | None" = None,
):
    """流式主控调度：逐段产出 SSE 事件 dict。

    skill_id：用户显式选中的技能；auto_skill：按关键词自动命中的技能（二选一）。
    """
    skill = skills.get(skill_id)
    auto_used = False
    if skill is None and auto_skill is not None:
        skill = auto_skill
        auto_used = True
    schemas = tools_for(skill)
    executors = skills.resolve_executors(skill)
    if auto_used:
        yield {
            "type": "skill_auto",
            "skill": skill.public() if skill else None,
            "message": f"已自动启用技能：{skill.name}" if skill else "",
        }
    system = build_system_prompt(skill)
    user_text = (message or "").strip() + attachment_block(attachments)
    history = session_history(session_id)
    messages: list[dict] = [{"role": "system", "content": system}]
    messages.extend(history)
    messages.append({"role": "user", "content": user_content(message, attachments, vision_enabled())})

    tool_calls_log: list[dict] = []
    for _ in range(MAX_TOOL_ROUNDS):
        content_parts: list[str] = []
        tool_acc: dict[int, dict] = {}
        async for kind, data in stream_llm(messages, schemas):
            if kind == "delta":
                content_parts.append(data)
                yield {"type": "delta", "text": data}
            elif kind == "tool_delta":
                for tc in data:
                    idx = tc.get("index", 0)
                    acc = tool_acc.setdefault(idx, {"id": None, "name": None, "args": ""})
                    if tc.get("id"):
                        acc["id"] = tc["id"]
                    fn = tc.get("function") or {}
                    if fn.get("name"):
                        acc["name"] = fn["name"]
                    if fn.get("arguments"):
                        acc["args"] += fn["arguments"]
        content = "".join(content_parts)
        if not tool_acc:
            final = content or "（无回复）"
            history.append({"role": "user", "content": user_text})
            history.append({"role": "assistant", "content": final})
            if len(history) > 20:
                del history[: len(history) - 20]
            yield {"type": "done", "reply": final, "tool_calls": tool_calls_log}
            return

        assistant_msg: dict = {"role": "assistant", "content": content or None, "tool_calls": []}
        tool_results: list[tuple[str, str]] = []
        for idx in sorted(tool_acc):
            tc = tool_acc[idx]
            call_id = tc["id"] or f"call_{len(tool_calls_log)}"
            fname = tc["name"] or "unknown"
            try:
                args = json.loads(tc["args"] or "{}")
                if not isinstance(args, dict):
                    args = {}
            except json.JSONDecodeError:
                args = {}
            assistant_msg["tool_calls"].append({
                "id": call_id,
                "type": "function",
                "function": {"name": fname, "arguments": json.dumps(args, ensure_ascii=False)},
            })
            result = await run_tool(fname, args, executors)
            tool_calls_log.append({
                "step_id": len(tool_calls_log) + 1,
                "tool": fname,
                "args": args,
                "result": result[:200],
            })
            yield {"type": "tool", "step_id": len(tool_calls_log), "tool": fname, "args": args}
            for extra in tool_events(fname, result):
                yield extra
            if fname == "music_play":
                try:
                    parsed = json.loads(result)
                    if parsed.get("ok"):
                        yield {
                            "type": "play",
                            "name": parsed.get("name"),
                            "title": parsed.get("title"),
                            "url": parsed.get("url"),
                        }
                except Exception:
                    pass
            if fname == "open_window":
                # 把「打开窗口」这件事交给前端真正执行（后端只负责校验）
                try:
                    parsed = json.loads(result)
                    if parsed.get("ok"):
                        yield {
                            "type": "open_window",
                            "app": parsed.get("app"),
                            "app_id": parsed.get("app_id"),
                            "app_name": parsed.get("app_name"),
                            "path": parsed.get("path"),
                            "run": parsed.get("run"),
                            "args": parsed.get("args") or [],
                        }
                except Exception:
                    pass
            if fname == "ui_apply":
                # 界面修改交给前端真的去改（后端只做校验与规整）
                try:
                    parsed = json.loads(result)
                    if parsed.get("ok"):
                        yield {"type": "ui_command", "commands": parsed.get("commands") or []}
                except Exception:
                    pass
            tool_results.append((call_id, result))
        messages.append(assistant_msg)
        for call_id, result in tool_results:
            messages.append({"role": "tool", "tool_call_id": call_id, "content": result})

    messages.append({"role": "user", "content": FORCE_ANSWER_HINT})
    content_parts: list[str] = []
    async for kind, data in stream_llm(messages, None):
        if kind == "delta":
            content_parts.append(data)
            yield {"type": "delta", "text": data}
    final = "".join(content_parts) or "（无回复）"
    history.append({"role": "user", "content": user_text})
    history.append({"role": "assistant", "content": final})
    if len(history) > 20:
        del history[: len(history) - 20]
    yield {"type": "done", "reply": final, "tool_calls": tool_calls_log}


class ChatIn(BaseModel):
    message: str = ""
    session_id: str | None = None
    attachments: list[dict[str, Any]] | None = None
    skill: str | None = None          # 输入框 Skill 面板选中的技能 id


@router.post("/chat")
async def chat_endpoint(body: ChatIn):
    message = (body.message or "").strip()
    attachments = body.attachments or []
    if not message and not attachments:
        return JSONResponse(status_code=400, content={"error": {"message": "消息不能为空"}})
    session_id = body.session_id or "default"
    skill, _auto = resolve_skill(body.skill, message)
    try:
        return await run_chat(session_id, message, attachments, skill.id if skill else None)
    except ChatError as exc:
        return JSONResponse(status_code=exc.status, content={"error": {"message": exc.message}})


@router.post("/chat/stream")
async def chat_stream_endpoint(body: ChatIn):
    message = (body.message or "").strip()
    attachments = body.attachments or []
    if not message and not attachments:
        return JSONResponse(status_code=400, content={"error": {"message": "消息不能为空"}})
    session_id = body.session_id or "default"
    cfg = load_config()
    if not cfg["api_key"]:
        return JSONResponse(status_code=401, content={"error": {"message": "未配置 LLM API Key，请先在设置里填写"}})

    skill, auto_matched = resolve_skill(body.skill, message)
    if auto_matched:
        explicit_id, auto_skill = None, skill
    else:
        explicit_id, auto_skill = (skill.id if skill else None), None

    async def gen():
        try:
            async for ev in stream_chat(session_id, message, attachments, explicit_id, auto_skill):
                yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
        except ChatError as exc:
            yield f"data: {json.dumps({'type': 'error', 'message': exc.message}, ensure_ascii=False)}\n\n"
        except Exception:
            yield f"data: {json.dumps({'type': 'error', 'message': '主控内部错误'}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
