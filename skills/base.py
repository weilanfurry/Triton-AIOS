"""技能（Skill）注册表。

一个技能 = 一段提示词 + 一组工具 + 一组执行器 + 触发词。
主控（agents.py）只认这个注册表，所以新增技能不用改主控代码：

    from .base import Skill, register
    register(Skill(id="xxx", name="某某技能", prompt="你是……", tools=[...], executors={...}))
"""

from dataclasses import dataclass, field
import re
from typing import Any, Callable

# 核心工具暴露范围：
#   all  = 全部核心工具（音乐 / 文件 / Python / QomoChat / 界面）
#   base = 基础工具（文件 / Python / 打开窗口）
#   none = 只用技能自己的工具
CORE_SCOPES = ("all", "base", "none")


@dataclass
class Skill:
    id: str
    name: str
    description: str = ""
    prompt: str = ""
    icon: str = "puzzle"
    triggers: tuple[str, ...] = ()
    tools: list[dict] = field(default_factory=list)
    tool_refs: list[str] = field(default_factory=list)   # 按名字引用别的技能 / 核心工具
    executors: dict[str, Callable[[dict], str]] = field(default_factory=dict)
    core_scope: str = "base"
    shows_workspace: bool = False
    hint: str = ""
    source: str = "builtin"          # builtin | custom
    order: int = 100                 # 弹窗里的排序（小的在前）

    def tool_names(self) -> list[str]:
        names = []
        for schema in self.tools:
            name = ((schema or {}).get("function") or {}).get("name")
            if name and name not in names:
                names.append(name)
        for name in self.tool_refs:
            if name not in names:
                names.append(name)
        return names

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "icon": self.icon,
            "hint": self.hint,
            "source": self.source,
            "triggers": list(self.triggers),
            "tools": self.tool_names(),
            "shows_workspace": self.shows_workspace,
        }


_REGISTRY: dict[str, Skill] = {}


def register(skill: Skill) -> Skill:
    """注册（或覆盖）一个技能。"""
    if skill.core_scope not in CORE_SCOPES:
        skill.core_scope = "base"
    _REGISTRY[skill.id] = skill
    return skill


def unregister(skill_id: str) -> bool:
    return _REGISTRY.pop(skill_id, None) is not None


def get(skill_id: str | None) -> Skill | None:
    if not skill_id:
        return None
    return _REGISTRY.get(str(skill_id).strip())


def all_skills() -> list[Skill]:
    return sorted(_REGISTRY.values(), key=lambda s: (s.order, s.id))


def public_list() -> list[dict]:
    return [s.public() for s in all_skills()]


def find_tool_schema(name: str) -> dict | None:
    """在已注册技能里找一个工具的 schema。"""
    for skill in all_skills():
        for schema in skill.tools:
            if ((schema or {}).get("function") or {}).get("name") == name:
                return schema
    return None


def find_executor(name: str) -> Callable[[dict], str] | None:
    """在已注册技能里找一个工具的执行器。"""
    for skill in all_skills():
        fn = skill.executors.get(name)
        if fn:
            return fn
    return None


def auto_match(text: str) -> Skill | None:
    """没有显式选技能时，用触发词猜一个（命中最长触发词的优先）。

    触发词支持两种写法：普通子串，或 're:正则'。
    """
    t = str(text or "").strip().lower()
    if not t:
        return None
    best: tuple[int, Skill] | None = None
    for skill in all_skills():
        for trigger in skill.triggers:
            key = str(trigger or "").strip().lower()
            if not key:
                continue
            if key.startswith("re:"):
                try:
                    match = re.search(key[3:], t, re.I)
                except re.error:
                    continue
                hit = len(match.group(0)) if match else 0
            else:
                hit = len(key) if key in t else 0
            if hit and (best is None or hit > best[0]):
                best = (hit, skill)
    return best[1] if best else None


def prompt_for(skill: Skill | None) -> str:
    if not skill:
        return ""
    lines = [
        "",
        f"当前技能：「{skill.name}」（{skill.id}）",
        skill.prompt.strip(),
    ]
    if skill.tool_names():
        lines.append("本技能可用工具：" + "、".join(skill.tool_names()))
    if skill.core_scope == "none":
        lines.append("本轮只允许使用上面这些技能工具，不要调用其它工具。")
    lines.append("完成用户请求后，用中文简要说明你真实做了什么（不要臆造结果）。")
    return "\n".join(line for line in lines if line)
