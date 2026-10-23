"""OpenAI 兼容 API（/v1/*）。

对外暴露 OpenAI 格式的接口，转发到配置的 LLM 后端（默认 DeepSeek，
也兼容任何 OpenAI 格式的服务）。配置优先级：环境变量 > config.json：

    DEEPSEEK_API_KEY / TRITON_API_KEY / OPENAI_API_KEY   # API Key
    LLM_BASE_URL                                         # 后端地址
    LLM_MODEL                                            # 默认模型
"""

import json
import os
from pathlib import Path
from typing import Any, AsyncIterator

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

import config_store

router = APIRouter(prefix="/v1", tags=["openai"])

BASE_DIR = Path(__file__).resolve().parent
CONFIG_FILE = BASE_DIR / "config.json"

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"
UPSTREAM_TIMEOUT = httpx.Timeout(180.0, connect=10.0)


def _read_raw() -> dict:
    """公开 config.json + 本机私密 config.local.json（后者优先）。"""
    return config_store.read_raw()


def load_config() -> dict:
    """读取 LLM 配置：环境变量优先，其次 config.json。"""
    cfg: dict[str, Any] = _read_raw()

    api_key = (
        os.environ.get("TRITON_API_KEY")
        or os.environ.get("DEEPSEEK_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
        or str(cfg.get("deepseek_api_key", "") or "")
    )
    base_url = os.environ.get("LLM_BASE_URL") or str(cfg.get("llm_base_url") or DEFAULT_BASE_URL)
    model = os.environ.get("LLM_MODEL") or str(cfg.get("llm_model") or DEFAULT_MODEL)
    return {"api_key": api_key.strip(), "base_url": base_url.rstrip("/"), "model": model.strip()}


def save_config(overrides: dict) -> dict:
    """保存 LLM 配置：API Key 写本机私密文件，其余写公开配置（None 表示不修改）。"""
    if overrides.get("api_key") is not None:
        config_store.write_local({"deepseek_api_key": str(overrides["api_key"]).strip()})
    public: dict[str, Any] = {}
    if overrides.get("llm_base_url"):
        public["llm_base_url"] = str(overrides["llm_base_url"]).strip()
    if overrides.get("llm_model"):
        public["llm_model"] = str(overrides["llm_model"]).strip()
    if public:
        config_store.write_public(public)
    return _read_raw()


def openai_error(status: int, message: str, err_type: str = "invalid_request_error", code: str | None = None) -> JSONResponse:
    """按 OpenAI 错误格式返回。"""
    return JSONResponse(
        status_code=status,
        content={"error": {"message": message, "type": err_type, "param": None, "code": code}},
    )


@router.get("/models")
async def list_models() -> dict:
    """列出可用模型（OpenAI 格式）。"""
    cfg = load_config()
    model = cfg["model"]
    known = ["deepseek-chat", "deepseek-reasoner"]
    models = [model]
    if model in known:
        for m in known:
            if m not in models:
                models.append(m)
    data = [
        {"id": m, "object": "model", "created": 0, "owned_by": "triton-aios"}
        for m in models
    ]
    return {"object": "list", "data": data}


@router.post("/chat/completions")
async def chat_completions(request: Request):
    """OpenAI 兼容的对话补全接口，支持 stream（SSE）。"""
    cfg = load_config()
    if not cfg["api_key"]:
        return openai_error(
            401,
            "未配置 LLM API Key：请在 config.json 中填写 deepseek_api_key，"
            "或设置环境变量 DEEPSEEK_API_KEY / TRITON_API_KEY / OPENAI_API_KEY。",
            err_type="authentication_error",
            code="missing_api_key",
        )

    try:
        payload: dict[str, Any] = await request.json()
    except Exception:
        return openai_error(400, "请求体不是合法的 JSON。")

    if not isinstance(payload, dict):
        return openai_error(400, "请求体必须是一个 JSON 对象。")
    messages = payload.get("messages")
    if not isinstance(messages, list) or not messages:
        return openai_error(400, "messages 必须是非空数组。")

    stream = bool(payload.get("stream", False))
    upstream_url = cfg["base_url"] + "/chat/completions"
    upstream_payload = dict(payload)
    upstream_payload.setdefault("model", cfg["model"])
    headers = {
        "Authorization": f"Bearer {cfg['api_key']}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream" if stream else "application/json",
    }

    if not stream:
        try:
            async with httpx.AsyncClient(timeout=UPSTREAM_TIMEOUT) as client:
                resp = await client.post(upstream_url, headers=headers, json=upstream_payload)
        except httpx.HTTPError as exc:
            return openai_error(502, f"上游 LLM 请求失败：{exc}", err_type="api_error", code="upstream_error")
        if resp.status_code >= 400:
            return openai_error(
                resp.status_code,
                f"上游 LLM 返回错误：{resp.text[:500]}",
                err_type="api_error",
                code=f"upstream_{resp.status_code}",
            )
        return JSONResponse(status_code=resp.status_code, content=resp.json())

    async def event_stream() -> AsyncIterator[bytes]:
        client = httpx.AsyncClient(timeout=UPSTREAM_TIMEOUT)
        try:
            async with client.stream("POST", upstream_url, headers=headers, json=upstream_payload) as resp:
                if resp.status_code >= 400:
                    body = (await resp.aread()).decode("utf-8", errors="replace")
                    event = {"error": {"message": f"上游 LLM 返回错误：{body[:300]}", "type": "api_error", "param": None, "code": f"upstream_{resp.status_code}"}}
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode("utf-8")
                    return
                async for chunk in resp.aiter_bytes():
                    yield chunk
        except httpx.HTTPError as exc:
            event = {"error": {"message": f"上游 LLM 请求失败：{exc}", "type": "api_error", "param": None, "code": "upstream_error"}}
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode("utf-8")
        finally:
            await client.aclose()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )
