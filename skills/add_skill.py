"""技能④：添加技能。

两条路径：
  1. 上传文件（前端直接调 /api/skills/upload）：txt 全文当提示词，json 按字段解析
  2. 对话创建（本技能的工具）：skill_create / skill_update / skill_delete

存到 skills/custom/<id>.json，注册后立刻出现在技能面板里（无需重启）。
自定义技能可以用 tool_refs 复用内置工具，例如 ["web_search", "python_run"]。
"""

import json
import re
import time
from pathlib import Path
from typing import Any

from . import base
from .base import Skill, register

BASE_DIR = Path(__file__).resolve().parent.parent
CUSTOM_DIR = Path(__file__).resolve().parent / "custom"

MAX_PROMPT_CHARS = 20000
MAX_TRIGGERS = 12


def _err(message: str, **extra: Any) -> str:
    payload = {"ok": False, "error": message}
    payload.update(extra)
    return json.dumps(payload, ensure_ascii=False)


def _slug(name: str, content: str) -> str:
    """给技能生成一个稳定 id：优先英文名，中文名就用内容哈希。"""
    raw = str(name or "").strip().lower()
    ascii_part = re.sub(r"[^a-z0-9]+", "_", raw).strip("_")
    if len(ascii_part) >= 3:
        return "custom_" + ascii_part[:32]
    import hashlib
    digest = hashlib.md5((raw + str(content)[:200]).encode("utf-8")).hexdigest()[:8]
    return "custom_" + digest


def _split_keywords(text: str) -> list[str]:
    parts = re.split(r"[,，、;；/|\s]+", str(text or ""))
    out = []
    for part in parts:
        item = part.strip()
        if 1 < len(item) <= 20 and item not in out:
            out.append(item)
    return out[:MAX_TRIGGERS]


def _first_line(text: str) -> str:
    for line in str(text or "").splitlines():
        item = line.strip().strip("#").strip()
        if item:
            return item
    return ""


def parse_upload(filename: str, content: str) -> dict:
    """把上传的 txt / json 解析成技能定义（不写盘）。"""
    name_file = Path(str(filename or "")).name
    stem = Path(name_file).stem.strip() or "自定义技能"
    text = str(content or "")
    if not text.strip():
        raise ValueError("文件内容为空")
    if len(text) > MAX_PROMPT_CHARS:
        text = text[:MAX_PROMPT_CHARS]

    spec: dict[str, Any] = {}
    is_json = name_file.lower().endswith(".json") or text.lstrip().startswith(("{", "["))
    if is_json:
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError("json 解析失败：" + exc.msg) from exc
        if isinstance(data, dict):
            spec = data
            spec_name = spec.get("name") or spec.get("title") or spec.get("技能名") or stem
            spec_prompt = (
                spec.get("prompt") or spec.get("instructions") or spec.get("system_prompt")
                or spec.get("提示词") or spec.get("content") or spec.get("description") or ""
            )
            if not str(spec_prompt).strip():
                raise ValueError("json 里没有找到提示词字段（prompt / instructions / system_prompt / 提示词）")
            spec = {
                "name": str(spec_name)[:40],
                "description": str(spec.get("description") or spec.get("说明") or "")[:120],
                "prompt": str(spec_prompt)[:MAX_PROMPT_CHARS],
                "keywords": [str(k) for k in (spec.get("keywords") or spec.get("triggers") or spec.get("触发词") or [])],
                "icon": str(spec.get("icon") or "sparkles")[:40],
                "tools": [str(t) for t in (spec.get("tools") or spec.get("工具") or [])],
            }
        else:
            spec = {"name": stem, "description": "", "prompt": text, "keywords": [], "icon": "sparkles", "tools": []}
    else:
        keywords: list[str] = []
        name = stem
        for line in text.splitlines()[:6]:
            head = line.strip().lstrip("#").strip()
            m = re.match(r"^(名称|技能名|name)\s*[:：]\s*(.+)$", head, re.I)
            if m:
                name = m.group(2).strip()[:40]
            m = re.match(r"^(关键词|触发词|keywords?)\s*[:：]\s*(.+)$", head, re.I)
            if m:
                keywords = _split_keywords(m.group(2))
        spec = {
            "name": name,
            "description": _first_line(text)[:120],
            "prompt": text,
            "keywords": keywords,
            "icon": "sparkles",
            "tools": [],
        }

    spec["keywords"] = [k for k in (spec.get("keywords") or []) if str(k).strip()][:MAX_TRIGGERS]
    spec["tools"] = [t for t in (spec.get("tools") or []) if str(t).strip()][:12]
    spec["source_file"] = name_file
    return spec


def _skill_from_spec(spec: dict, skill_id: str | None = None) -> Skill:
    name = str(spec.get("name") or "自定义技能")[:40]
    prompt = str(spec.get("prompt") or "").strip()
    sid = skill_id or _slug(name, prompt)
    refs = [str(t) for t in (spec.get("tools") or [])]
    executors = {}
    for ref in refs:
        fn = base.find_executor(ref)
        if fn:
            executors[ref] = fn
    return Skill(
        id=sid,
        name=name,
        icon=str(spec.get("icon") or "sparkles")[:40],
        description=str(spec.get("description") or "")[:120] or "自定义提示词技能",
        hint="（" + name + "）" + (str(spec.get("description") or "")[:24] or "说点什么…"),
        order=200,
        triggers=tuple(str(k) for k in (spec.get("keywords") or [])),
        tool_refs=refs,
        executors=executors,
        core_scope="base",
        source="custom",
        prompt=(
            prompt
            + "\n（本技能由用户上传的提示词文件扩展而来"
            + (f"，来源：{spec.get('source_file')}" if spec.get("source_file") else "")
            + "。严格按上面的角色与要求回答。）"
        ),
    )


def _dump(skill: Skill, spec: dict | None = None) -> None:
    CUSTOM_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "id": skill.id,
        "name": skill.name,
        "description": skill.description,
        "icon": skill.icon,
        "prompt": spec.get("prompt") if spec else skill.prompt,
        "keywords": list(skill.triggers),
        "tools": list(skill.tool_refs),
        "source_file": (spec or {}).get("source_file", ""),
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    (CUSTOM_DIR / (skill.id + ".json")).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def register_upload(filename: str, content: str) -> Skill:
    """上传文件 → 解析 → 落盘 → 注册，返回新技能。"""
    spec = parse_upload(filename, content)
    sid = _slug(spec["name"], spec["prompt"])
    if sid in {s.id for s in base.all_skills() if s.source == "builtin"}:
        sid = sid + "_user"
    skill = _skill_from_spec(spec, sid)
    register(skill)
    _dump(skill, spec)
    return skill


def delete_custom(skill_id: str) -> bool:
    skill = base.get(skill_id)
    if not skill or skill.source != "custom":
        return False
    path = CUSTOM_DIR / (skill.id + ".json")
    if path.is_file():
        path.unlink()
    return base.unregister(skill.id)


def load_custom() -> list[Skill]:
    """启动时把 skills/custom/*.json 全部注册进来。"""
    loaded: list[Skill] = []
    if not CUSTOM_DIR.is_dir():
        return loaded
    for path in sorted(CUSTOM_DIR.glob("*.json")):
        try:
            spec = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(spec, dict) or not str(spec.get("prompt") or "").strip():
            continue
        skill = _skill_from_spec(spec, str(spec.get("id") or path.stem))
        register(skill)
        loaded.append(skill)
    return loaded


# ===== 工具 =====

def tool_skill_list(args: dict) -> str:
    return json.dumps({"ok": True, "skills": base.public_list()}, ensure_ascii=False)


def tool_skill_create(args: dict) -> str:
    name = str(args.get("name") or "").strip()
    prompt = str(args.get("prompt") or "").strip()
    if not name or not prompt:
        return _err("name 和 prompt 都必须提供")
    spec = {
        "name": name[:40],
        "description": str(args.get("description") or "")[:120],
        "prompt": prompt[:MAX_PROMPT_CHARS],
        "keywords": [str(k) for k in (args.get("keywords") or [])],
        "icon": str(args.get("icon") or "sparkles"),
        "tools": [str(t) for t in (args.get("tools") or [])],
    }
    skill = _skill_from_spec(spec)
    register(skill)
    _dump(skill, spec)
    return json.dumps({
        "ok": True,
        "skill": skill.public(),
        "note": "技能已创建，用户在输入框的 Skill 面板里可以立刻选到它。",
    }, ensure_ascii=False)


def tool_skill_update(args: dict) -> str:
    skill_id = str(args.get("id") or "").strip()
    skill = base.get(skill_id)
    if not skill or skill.source != "custom":
        return _err("只能修改自定义技能（id 不存在或属于内置技能）：" + skill_id)
    spec = {
        "name": str(args.get("name") or skill.name)[:40],
        "description": str(args.get("description") or skill.description)[:120],
        "prompt": str(args.get("prompt") or skill.prompt)[:MAX_PROMPT_CHARS],
        "keywords": [str(k) for k in (args.get("keywords") or skill.triggers)],
        "icon": str(args.get("icon") or skill.icon),
        "tools": [str(t) for t in (args.get("tools") or skill.tool_refs)],
        "source_file": "",
    }
    updated = _skill_from_spec(spec, skill.id)
    register(updated)
    _dump(updated, spec)
    return json.dumps({"ok": True, "skill": updated.public()}, ensure_ascii=False)


def tool_skill_delete(args: dict) -> str:
    skill_id = str(args.get("id") or "").strip()
    if not delete_custom(skill_id):
        return _err("只能删除自定义技能：" + skill_id)
    return json.dumps({"ok": True, "deleted": skill_id}, ensure_ascii=False)


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "skill_list",
            "description": "列出当前所有技能（内置 + 用户上传的），含 id / 名称 / 说明 / 可用工具。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "skill_create",
            "description": "新建一个提示词技能：用户描述想要的能力，你把它写成规范的技能提示词落盘。",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "技能名，例如 论文润色"},
                    "description": {"type": "string", "description": "一句话说明这个技能干什么"},
                    "prompt": {"type": "string", "description": "完整的技能提示词（角色 + 规则 + 输出要求）"},
                    "keywords": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "自动触发词（用户不选技能、消息里出现这些词时自动启用）",
                    },
                    "icon": {"type": "string", "description": "lucide 图标名，默认 sparkles"},
                    "tools": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "允许复用的现成工具名，例如 web_search / python_run / file_write",
                    },
                },
                "required": ["name", "prompt"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "skill_update",
            "description": "修改一个自定义技能（改提示词 / 触发词 / 工具）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "技能 id，先 skill_list 查"},
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "prompt": {"type": "string"},
                    "keywords": {"type": "array", "items": {"type": "string"}},
                    "tools": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "skill_delete",
            "description": "删除一个自定义技能（内置技能删不了）。",
            "parameters": {
                "type": "object",
                "properties": {"id": {"type": "string", "description": "技能 id"}},
                "required": ["id"],
            },
        },
    },
]


register(Skill(
    id="add_skill",
    name="添加技能",
    icon="puzzle",
    description="上传 txt / json 提示词，或直接对话创建新技能",
    hint="描述你想要的新技能，例如：帮我加一个把中文改写成学术风格的能力",
    order=40,
    triggers=("添加技能", "新增技能", "加个技能", "上传技能", "新建技能", "自定义技能"),
    tools=TOOLS,
    executors={
        "skill_list": tool_skill_list,
        "skill_create": tool_skill_create,
        "skill_update": tool_skill_update,
        "skill_delete": tool_skill_delete,
    },
    core_scope="none",
    prompt=(
        "你是 AIOS 的「技能管理员」。用户上传提示词文件或让你新建技能时，你要把它变成规范的可复用技能。\n"
        "工作流程：\n"
        "1. 先 skill_list 看看已经有哪些技能，避免重复。\n"
        "2. 用 skill_create 建技能：prompt 要写成完整的角色 + 规则 + 输出格式；\n"
        "   如果这个技能需要现成能力（联网、跑 Python、读写文件），在 tools 里填 web_search / web_fetch / python_run / file_read / file_write 等名字。\n"
        "3. 触发词 keywords 用 2-6 个用户真会说的话。\n"
        "4. 建完告诉用户：技能名、什么时候会自动触发、在 Skill 面板里怎么选到它。\n"
        "注意：内置技能（AI 写应用 / 系统界面 / 联网搜索 / 添加技能）是系统自带的，不要试图修改或删除它们。"
    ),
))


load_custom()
