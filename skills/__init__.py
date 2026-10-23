"""Triton AIOS 技能包。

导入本包即完成所有技能注册（内置 4 个 + skills/custom/ 里的自定义技能）。
主控（agents.py）只依赖这里暴露的几个函数，所以新增技能不用改主控。
"""

from .base import (  # noqa: F401
    Skill,
    all_skills,
    auto_match,
    find_executor,
    find_tool_schema,
    get,
    prompt_for,
    public_list,
    register,
    unregister,
)
from . import app_writer  # noqa: F401
from . import ui_html  # noqa: F401
from . import web_search  # noqa: F401
from . import add_skill  # noqa: F401   （导入时会 load_custom()）

__all__ = [
    "Skill",
    "all_skills",
    "auto_match",
    "find_executor",
    "find_tool_schema",
    "get",
    "prompt_for",
    "public_list",
    "register",
    "unregister",
    "resolve_executors",
    "resolve_tools",
    "reload_custom",
]

# 核心工具里允许技能按名字引用的"基础能力"（core_scope="base"），
# 其余（音乐 / QomoChat / 界面指令）只在 core_scope="all" 时暴露。
BASE_TOOL_NAMES = {
    "file_list", "file_read", "file_write", "python_env", "python_run", "open_window",
}


def core_tools_for(skill: "Skill | None", core_schemas: list[dict]) -> list[dict]:
    """按技能的 core_scope 裁剪核心工具。"""
    if skill is None or skill.core_scope == "all":
        return list(core_schemas)
    if skill.core_scope == "none":
        return []
    return [s for s in core_schemas if s["function"]["name"] in BASE_TOOL_NAMES]


def tools_for(skill: "Skill | None", core_schemas: list[dict]) -> list[dict]:
    """一次请求最终暴露给模型的工具表：核心工具（按范围）+ 技能工具。"""
    out = core_tools_for(skill, core_schemas)
    if skill is None:
        return out
    known = {s["function"]["name"] for s in out}
    core_by_name = {s["function"]["name"]: s for s in core_schemas}
    for schema in skill.tools:
        name = ((schema or {}).get("function") or {}).get("name")
        if name and name not in known:
            out.append(schema)
            known.add(name)
    for name in skill.tool_refs:
        if name in known:
            continue
        schema = core_by_name.get(name) or find_tool_schema(name)
        if schema:
            out.append(schema)
            known.add(name)
    return out


def resolve_executors(skill: "Skill | None") -> dict:
    """技能自带的执行器（核心工具的执行器在主控里兜底）。"""
    return dict(skill.executors) if skill else {}


def reload_custom() -> list:
    """重新扫描 skills/custom/（前端上传技能后调用）。"""
    return add_skill.load_custom()
