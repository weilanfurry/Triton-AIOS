"""Triton AIOS 桌面 UI —— FastAPI 入口。

启动方式（项目根目录下）：
    uvicorn desktop_app:app --reload --port 8000
或直接：
    python desktop_app.py
"""

from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app_registry import router as apps_router
from agents import router as chat_router
from conversations_api import router as conversations_router
from music_api import router as music_router
from openai_api import load_config, router as openai_router, save_config
from python_api import router as python_router
from qomochat_api import router as qomochat_router
from skills_api import router as skills_router
from ui_api import router as ui_router
from workspace_api import router as workspace_router

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

app = FastAPI(title="Triton AIOS", version="0.1.0")

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.include_router(openai_router)
app.include_router(music_router)
app.include_router(chat_router)
app.include_router(conversations_router)
app.include_router(workspace_router)
app.include_router(apps_router)
app.include_router(qomochat_router)
app.include_router(python_router)
app.include_router(ui_router)
app.include_router(skills_router)


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    """桌面主界面。"""
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok", "service": "triton-aios", "version": "0.1.0"}


@app.get("/favicon.ico", include_in_schema=False)
async def favicon() -> Response:
    """浏览器会自动请求 favicon，这里直接返回空响应避免 404。"""
    return Response(status_code=204)


class LLMConfigIn(BaseModel):
    api_key: str | None = None
    llm_base_url: str | None = None
    llm_model: str | None = None


@app.get("/api/config/llm")
async def get_llm_config() -> dict:
    """返回当前 LLM 配置（不回传 Key 明文，只给是否已配置）。"""
    cfg = load_config()
    return {
        "llm_base_url": cfg["base_url"],
        "llm_model": cfg["model"],
        "has_api_key": bool(cfg["api_key"]),
    }


@app.put("/api/config/llm")
async def set_llm_config(body: LLMConfigIn) -> dict:
    """保存 LLM 配置到 config.json。"""
    saved = save_config(body.model_dump())
    cfg = load_config()
    return {
        "status": "ok",
        "llm_base_url": cfg["base_url"],
        "llm_model": cfg["model"],
        "has_api_key": bool(saved.get("deepseek_api_key")),
    }


@app.post("/api/config/llm/test")
async def test_llm_config(body: LLMConfigIn) -> dict:
    """用传入（或已保存）的配置测试上游连接。"""
    cfg = load_config()
    api_key = (body.api_key if body.api_key is not None else cfg["api_key"]).strip()
    base_url = (body.llm_base_url or cfg["base_url"]).rstrip("/")
    model = body.llm_model or cfg["model"]
    if not api_key:
        return {"ok": False, "message": "请先填写 API Key"}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=8.0)) as client:
            resp = await client.get(
                base_url + "/models",
                headers={"Authorization": f"Bearer {api_key}"},
            )
    except httpx.HTTPError as exc:
        return {"ok": False, "message": f"连接失败：{exc}"}
    if resp.status_code >= 400:
        return {"ok": False, "message": f"接口返回 {resp.status_code}：{resp.text[:200]}"}
    return {"ok": True, "message": f"连接成功（{base_url}），模型 {model}"}


if __name__ == "__main__":
    uvicorn.run("desktop_app:app", host="127.0.0.1", port=8000, reload=True)
