"""应用注册表：apps/*/manifest.json 即应用，支持插入 / 移除 / 转移。

系统启动与每次查询都重新扫描 apps/ 目录：有 manifest.json 的子文件夹就是一个应用。
"""

import json
import mimetypes
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

router = APIRouter(prefix="/api", tags=["apps"])

BASE_DIR = Path(__file__).resolve().parent
APPS_DIR = BASE_DIR / "apps"


def read_manifest(folder: Path) -> dict | None:
    mf = folder / "manifest.json"
    if not mf.is_file():
        return None
    try:
        data = json.loads(mf.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    data["id"] = folder.name
    data["folder"] = str(folder)
    return data


def list_apps() -> list[dict]:
    """扫描 apps/ 下所有带 manifest.json 的子文件夹。"""
    apps = []
    if APPS_DIR.is_dir():
        for folder in sorted(APPS_DIR.iterdir()):
            if folder.is_dir():
                manifest = read_manifest(folder)
                if manifest:
                    apps.append(manifest)
    return apps


def get_app(app_id: str) -> dict | None:
    for app in list_apps():
        if app["id"] == app_id:
            return app
    return None


def find_app(kind: str | None = None) -> dict | None:
    """按 kind 找第一个应用（kind：music / workspace / ...）。"""
    for app in list_apps():
        if kind is None or str(app.get("kind", "")).lower() == kind.lower():
            return app
    return None


def app_abs_path(app: dict, rel: str | None = None) -> Path:
    """应用目录内的路径；rel 为空则返回应用根目录。"""
    folder = Path(app["folder"])
    return (folder / rel).resolve() if rel else folder.resolve()


@router.get("/apps")
async def apps_endpoint() -> dict:
    apps = []
    for app in list_apps():
        apps.append({
            "id": app.get("id"),
            "name": app.get("name", app.get("id")),
            "icon": app.get("icon", "package"),
            "description": app.get("description", ""),
            "kind": app.get("kind", "generic"),
            "tools": app.get("tools", []),
            "workdir": app.get("workdir", "workspace"),
            "entry": app.get("entry", ""),
            "window": app.get("window", {}),
            "version": app.get("version", ""),
            "author": app.get("author", ""),
        })
    return {"apps": apps}


@router.get("/apps/{app_id}/asset/{rel:path}")
async def app_asset(app_id: str, rel: str) -> FileResponse:
    """把应用目录里的静态文件发给浏览器（生成的应用在 iframe 窗口里直接跑）。

    用路径式 URL（而不是 ?path=）是为了让应用内部的 ./app.js、./style.css
    这类相对引用能自然解析。
    """
    app = get_app(app_id)
    if app is None:
        raise HTTPException(status_code=404, detail=f"应用不存在：{app_id}")
    folder = Path(app["folder"]).resolve()
    target = (folder / rel).resolve()
    if target != folder and folder not in target.parents:
        raise HTTPException(status_code=400, detail="路径越界")
    if not target.is_file():
        raise HTTPException(status_code=404, detail=f"文件不存在：{rel}")
    media = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    if target.suffix.lower() in {".html", ".htm"}:
        media = "text/html; charset=utf-8"
    elif target.suffix.lower() in {".js", ".mjs"}:
        media = "text/javascript; charset=utf-8"
    elif target.suffix.lower() == ".css":
        media = "text/css; charset=utf-8"
    elif target.suffix.lower() in {".json", ".md", ".txt", ".csv", ".svg"}:
        media = media.split(";")[0] + "; charset=utf-8"
    return FileResponse(target, media_type=media, headers={"Cache-Control": "no-store"})
