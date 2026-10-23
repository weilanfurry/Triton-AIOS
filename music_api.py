"""音乐库 API：读取本地音乐目录并流式提供音频文件。"""

import json
import os
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app_registry import app_abs_path, find_app
from openai_api import BASE_DIR

router = APIRouter(prefix="/api/music", tags=["music"])

AUDIO_EXTS = {".mp3", ".m4a", ".flac", ".wav", ".ogg", ".aac", ".opus"}
MIME = {
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".flac": "audio/flac",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
    ".aac": "audio/aac",
    ".opus": "audio/ogg",
}


def _read_cfg() -> dict:
    cfg: dict = {}
    config_file = BASE_DIR / "config.json"
    if config_file.exists():
        try:
            data = json.loads(config_file.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                cfg = data
        except (OSError, json.JSONDecodeError):
            cfg = {}
    return cfg


def music_manifest() -> dict | None:
    """当前注册的音乐应用（apps/ 下 kind=music 的文件夹）。"""
    return find_app(kind="music")


def music_workspace_dir() -> Path | None:
    """音乐应用的 workspace（放 playlist.json / recent.json 等数据）。"""
    app = music_manifest()
    if app is None:
        return None
    return app_abs_path(app, app.get("workdir") or "workspace")


def recent_file() -> Path:
    ws = music_workspace_dir()
    return (ws / "recent.json") if ws else (BASE_DIR / "apps" / "music_app" / "workspace" / "recent.json")


def get_music_dir() -> Path:
    """音乐文件目录：环境变量 / config 优先，其次注册表中的音乐应用自述目录。"""
    cfg = _read_cfg()
    raw = os.environ.get("MUSIC_DIR") or cfg.get("music_dir") or ""
    if raw:
        p = Path(raw).expanduser()
        if not p.is_absolute():
            p = BASE_DIR / p
        return p
    app = music_manifest()
    if app is not None:
        rel = app.get("music_dir") or app.get("workdir") or "workspace"
        return app_abs_path(app, rel)
    return BASE_DIR / "apps" / "music_app" / "workspace" / "music"


@router.get("/list")
async def music_list() -> dict:
    d = get_music_dir()
    if not d.is_dir():
        return {"status": "ok", "dir": str(d), "songs": [], "error": f"音乐目录不存在：{d}"}
    songs = []
    for p in sorted(d.iterdir()):
        if p.is_file() and p.suffix.lower() in AUDIO_EXTS:
            songs.append({
                "name": p.name,
                "title": p.stem,
                "ext": p.suffix.lower().lstrip("."),
                "size": p.stat().st_size,
                "url": f"/api/music/file?name={quote(p.name)}",
            })
    return {"status": "ok", "dir": str(d), "songs": songs}


@router.get("/file")
async def music_file(name: str):
    d = get_music_dir().resolve()
    if not d.is_dir():
        raise HTTPException(status_code=404, detail="音乐目录不存在")
    path = (d / name).resolve()
    if path.parent != d or not path.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    ext = path.suffix.lower()
    if ext not in AUDIO_EXTS:
        raise HTTPException(status_code=400, detail="不支持的音频格式")
    return FileResponse(path, media_type=MIME.get(ext, "application/octet-stream"))


def read_recent() -> list:
    recent = recent_file()
    if recent.exists():
        try:
            data = json.loads(recent.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
        except (OSError, json.JSONDecodeError):
            pass
    return []


class RecentIn(BaseModel):
    name: str


@router.get("/recent")
async def get_recent() -> dict:
    return {"recent": read_recent()}


@router.post("/recent")
async def add_recent(body: RecentIn) -> dict:
    """记录最近播放，写入应用文件夹下的 recent.json。"""
    return {"status": "ok", "recent": record_recent(body.name)}


def record_recent(name: str) -> list:
    """把一首歌写入最近播放（供接口与工具执行器共用）。"""
    name = (name or "").strip()
    if not name:
        return read_recent()
    data = [d for d in read_recent() if d.get("name") != name]
    data.insert(0, {"name": name, "played_at": datetime.now().isoformat(timespec="seconds")})
    data = data[:20]
    recent = recent_file()
    recent.parent.mkdir(parents=True, exist_ok=True)
    recent.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return data
