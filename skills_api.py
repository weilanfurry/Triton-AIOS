"""技能 API：技能列表 / 上传提示词文件扩展技能 / 删除自定义技能。

前端「添加技能」走这里；上传内容是纯提示词（txt）或结构化技能（json），
注册后立刻出现在输入框的 Skill 面板里，不需要重启服务。
"""

import base64
import binascii
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import skills
from skills import add_skill

router = APIRouter(prefix="/api/skills", tags=["skills"])

MAX_UPLOAD_CHARS = 60000
ALLOWED_EXT = {".txt", ".json", ".md", ".text", ".prompt"}


class UploadIn(BaseModel):
    filename: str = "skill.txt"
    content: str = ""
    data: str | None = None          # 可选：base64（前端按二进制上传时用）


def _decode(body: UploadIn) -> str:
    text = str(body.content or "")
    if text.strip():
        return text
    raw = str(body.data or "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail="文件内容为空")
    if raw.startswith("data:"):
        comma = raw.find(",")
        if comma < 0:
            raise HTTPException(status_code=400, detail="data URL 格式不正确")
        raw = raw[comma + 1:]
    raw = "".join(raw.split())
    try:
        blob = base64.b64decode(raw + "=" * (-len(raw) % 4))
    except (binascii.Error, ValueError):
        raise HTTPException(status_code=400, detail="不是合法的 base64 内容")
    try:
        return blob.decode("utf-8")
    except UnicodeDecodeError:
        try:
            return blob.decode("gbk")
        except UnicodeDecodeError:
            raise HTTPException(status_code=400, detail="文件编码无法识别，请用 UTF-8 保存的 txt / json")


@router.get("")
async def list_skills() -> dict:
    """所有可用技能（内置 + 用户上传）。"""
    items = skills.public_list()
    return {"skills": items, "count": len(items), "custom": sum(1 for s in items if s["source"] == "custom")}


@router.post("/upload")
async def upload_skill(body: UploadIn) -> dict:
    """上传 txt / json 提示词文件 → 变成一个新技能。"""
    name = Path(str(body.filename or "")).name or "skill.txt"
    ext = Path(name).suffix.lower()
    if ext and ext not in ALLOWED_EXT:
        raise HTTPException(
            status_code=400,
            detail=f"只支持 {', '.join(sorted(ALLOWED_EXT))} 文件，收到的是 {ext}",
        )
    text = _decode(body)
    if len(text) > MAX_UPLOAD_CHARS:
        raise HTTPException(status_code=413, detail=f"文件太长了（上限 {MAX_UPLOAD_CHARS} 字符）")
    try:
        skill = add_skill.register_upload(name, text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "ok": True,
        "skill": skill.public(),
        "skills": skills.public_list(),
        "message": f"技能「{skill.name}」已添加，在输入框的 Skill 面板里可以选到它",
    }


@router.delete("/{skill_id}")
async def delete_skill(skill_id: str) -> dict:
    """删除一个自定义技能（内置技能不可删）。"""
    if not add_skill.delete_custom(skill_id):
        raise HTTPException(status_code=400, detail="只能删除自定义技能，或该技能不存在")
    return {"ok": True, "deleted": skill_id, "skills": skills.public_list()}
