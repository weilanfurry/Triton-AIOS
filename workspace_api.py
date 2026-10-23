"""工作区文件 API：前端工作区面板 + 小 AI 的原子文件工具共用。

运行 .py 的能力复用 python_api 里的 Python 运行时（同一套超时 / 输出截断 / 编码处理）。
附件（粘贴的图片 / 按钮选择的文件）通过 /upload 直接落盘到 workspace/uploads/。
"""

import base64
import binascii
import mimetypes
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app_registry import app_abs_path, find_app
from python_api import run_script

router = APIRouter(prefix="/api/workspace", tags=["workspace"])

BASE_DIR = Path(__file__).resolve().parent

MAX_READ = 200 * 1024
MAX_OUTPUT = 20000
MAX_UPLOAD = 25 * 1024 * 1024              # 单个附件上限 25 MB
UPLOAD_DIRNAME = "uploads"
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".ico", ".avif", ".heic"}


def workspace_root() -> Path:
    """工作区根目录：来自注册表里 kind=workspace 的应用自述目录。"""
    app = find_app(kind="workspace")
    if app is not None:
        p = app_abs_path(app, app.get("workdir") or "workspace")
    else:
        p = BASE_DIR / "apps" / "system_agent" / "workspace"
    p.mkdir(parents=True, exist_ok=True)
    return p.resolve()


def root() -> Path:
    return workspace_root()


def resolve_in_root(rel: str) -> Path | None:
    """把相对路径安全解析到工作区内；越界返回 None。"""
    r = root()
    target = (r / rel).resolve()
    if target != r and r not in target.parents:
        return None
    return target


def list_tree(p: Path, base: Path) -> list:
    items = []
    for child in sorted(p.iterdir()):
        rel = child.relative_to(base)
        if child.is_dir():
            items.append({
                "name": child.name,
                "type": "dir",
                "path": rel.as_posix(),
                "children": list_tree(child, base),
            })
        else:
            items.append({
                "name": child.name,
                "type": "file",
                "path": rel.as_posix(),
                "size": child.stat().st_size,
                "ext": child.suffix.lower().lstrip("."),
                "mtime": int(child.stat().st_mtime),
            })
    return items


@router.get("/list")
async def list_files() -> dict:
    r = root()
    return {"root": str(r), "files": list_tree(r, r)}


@router.get("/read")
async def read_file(path: str) -> dict:
    target = resolve_in_root(path)
    if target is None:
        raise HTTPException(status_code=400, detail="路径越界")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    content = target.read_text(encoding="utf-8", errors="replace")[:MAX_READ]
    return {"path": path, "content": content, "size": target.stat().st_size}


def safe_filename(name: str) -> str:
    """只保留文件名本身，去掉目录成分与非法字符。"""
    base = Path(str(name or "").replace("\\", "/")).name.strip()
    base = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", base).strip(" .")
    if not base:
        base = "attachment"
    if len(base) > 120:
        stem, dot, ext = base.rpartition(".")
        base = (stem[:108] + dot + ext[:10]) if dot else base[:120]
    return base


def unique_path(folder: Path, name: str) -> Path:
    """同名文件自动加 -1 / -2 后缀，避免覆盖已有附件。"""
    target = folder / name
    if not target.exists():
        return target
    stem, suffix = target.stem, target.suffix
    for i in range(1, 1000):
        candidate = folder / f"{stem}-{i}{suffix}"
        if not candidate.exists():
            return candidate
    return folder / f"{stem}-{int(datetime.now().timestamp())}{suffix}"


class UploadIn(BaseModel):
    name: str = "attachment"
    data: str = ""          # base64，允许带 data:...;base64, 前缀


@router.post("/upload")
async def upload_file(body: UploadIn) -> dict:
    """接收输入框里粘贴 / 选择的附件，落盘到 workspace/uploads/YYYYMMDD/。"""
    raw = (body.data or "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail="附件内容为空")
    if raw.startswith("data:"):
        comma = raw.find(",")
        if comma < 0:
            raise HTTPException(status_code=400, detail="data URL 格式不正确")
        raw = raw[comma + 1:]
    raw = "".join(raw.split())
    try:
        blob = base64.b64decode(raw + "=" * (-len(raw) % 4))
    except (binascii.Error, ValueError):
        raise HTTPException(status_code=400, detail="附件不是合法的 base64 数据")
    if not blob:
        raise HTTPException(status_code=400, detail="附件内容为空")
    if len(blob) > MAX_UPLOAD:
        raise HTTPException(
            status_code=413,
            detail=f"附件过大：{len(blob) // 1024 // 1024}MB，上限 {MAX_UPLOAD // 1024 // 1024}MB",
        )

    folder = root() / UPLOAD_DIRNAME / datetime.now().strftime("%Y%m%d")
    folder.mkdir(parents=True, exist_ok=True)
    target = unique_path(folder, safe_filename(body.name))
    target.write_bytes(blob)

    rel = target.relative_to(root()).as_posix()
    mime = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    return {
        "ok": True,
        "name": target.name,
        "path": rel,
        "size": len(blob),
        "mime": mime,
        "kind": "image" if target.suffix.lower() in IMAGE_EXTS else "file",
        "url": "/api/workspace/raw?path=" + quote(rel),
    }


@router.get("/raw")
async def raw_file(path: str) -> FileResponse:
    """按原始字节返回工作区里的文件（会话里的图片缩略图 / 预览用）。"""
    target = resolve_in_root(path)
    if target is None:
        raise HTTPException(status_code=400, detail="路径越界")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    media = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    return FileResponse(target, media_type=media, headers={"Cache-Control": "private, max-age=3600"})


class WriteIn(BaseModel):
    path: str
    content: str = ""


@router.put("/write")
async def write_file(body: WriteIn) -> dict:
    target = resolve_in_root(body.path)
    if target is None:
        raise HTTPException(status_code=400, detail="路径越界")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body.content, encoding="utf-8")
    return {"ok": True, "path": body.path, "size": target.stat().st_size}


class DeleteIn(BaseModel):
    path: str


@router.post("/delete")
async def delete_file(body: DeleteIn) -> dict:
    target = resolve_in_root(body.path)
    if target is None:
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


class RenameIn(BaseModel):
    path: str
    new_name: str


@router.post("/rename")
async def rename_file(body: RenameIn) -> dict:
    target = resolve_in_root(body.path)
    if target is None:
        raise HTTPException(status_code=400, detail="路径越界")
    if not target.exists():
        raise HTTPException(status_code=404, detail="文件不存在")
    new = target.with_name(body.new_name)
    if new.exists():
        raise HTTPException(status_code=400, detail="目标已存在")
    target.rename(new)
    return {"ok": True, "path": body.path, "new_path": new.relative_to(root()).as_posix()}


class MkdirIn(BaseModel):
    path: str


@router.post("/mkdir")
async def make_dir(body: MkdirIn) -> dict:
    target = resolve_in_root(body.path)
    if target is None:
        raise HTTPException(status_code=400, detail="路径越界")
    if target.exists():
        raise HTTPException(status_code=400, detail="已存在同名文件或文件夹")
    target.mkdir(parents=True, exist_ok=True)
    return {"ok": True, "path": body.path}


def run_python(path: str) -> dict:
    """在工作区内运行一个 .py 文件（沙箱：cwd 锁定工作区 + 超时 + 输出截断）。

    超时默认取 Python 运行时的配置（config.json 的 python_timeout，默认 60 秒）。
    """
    result = run_script(path, base=root())
    for key in ("stdout", "stderr"):
        if isinstance(result.get(key), str):
            result[key] = result[key][:MAX_OUTPUT]
    return result


class RunIn(BaseModel):
    path: str


@router.post("/run")
async def run_file(body: RunIn) -> dict:
    return run_python(body.path)
