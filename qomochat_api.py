"""QomoChat 客户端的本地代理 API（Triton 桌面窗口使用）。

浏览器只和本机 TritonAIOS 通信，由这里带着服务端保存的会话 Token
去访问 QomoChat 服务，避免把 QomoChat Token 暴露给前端页面。
"""

from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel

import qomochat_client as qc

router = APIRouter(prefix="/api/qomochat", tags=["qomochat"])


def _fail(exc: Exception) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


@router.get("/status")
async def status() -> dict:
    return qc.status()


class ServerIn(BaseModel):
    base_url: str


@router.post("/server")
async def set_server(body: ServerIn) -> dict:
    """切换 QomoChat 服务器地址（会清空旧服务器上的登录态）。"""
    try:
        return qc.set_base_url(body.base_url)
    except qc.QomoError as exc:
        raise _fail(exc) from exc


class LoginIn(BaseModel):
    username: str
    password: str


@router.post("/login")
async def login(body: LoginIn) -> dict:
    try:
        return qc.login(body.username.strip(), body.password)
    except qc.QomoError as exc:
        raise _fail(exc) from exc


@router.post("/logout")
async def logout() -> dict:
    return qc.logout()


@router.get("/friends")
async def friends() -> dict:
    try:
        return {"friends": qc.friends()}
    except qc.QomoError as exc:
        raise _fail(exc) from exc


@router.get("/search")
async def search(q: str = "") -> dict:
    try:
        return {"users": qc.search_users(q.strip())}
    except qc.QomoError as exc:
        raise _fail(exc) from exc


class UidIn(BaseModel):
    uid: str


@router.post("/friends")
async def add_friend(body: UidIn) -> dict:
    try:
        return qc.add_friend(body.uid.strip())
    except qc.QomoError as exc:
        raise _fail(exc) from exc


@router.delete("/friends/{uid}")
async def delete_friend(uid: str) -> dict:
    try:
        return qc.delete_friend(uid.strip())
    except qc.QomoError as exc:
        raise _fail(exc) from exc


@router.get("/conversation/{uid}/messages")
async def messages(uid: str, after_seq: int = 0, limit: int = 50) -> dict:
    try:
        return qc.messages(uid.strip(), after_seq, limit)
    except qc.QomoError as exc:
        raise _fail(exc) from exc


class SendIn(BaseModel):
    text: str


@router.post("/conversation/{uid}/messages")
async def send(uid: str, body: SendIn) -> dict:
    try:
        return qc.send(uid.strip(), body.text)
    except qc.QomoError as exc:
        raise _fail(exc) from exc


@router.post("/conversation/{uid}/clear")
async def clear(uid: str) -> dict:
    try:
        return qc.clear_history(uid.strip())
    except qc.QomoError as exc:
        raise _fail(exc) from exc


@router.get("/download")
async def download(url: str, name: str = "") -> Response:
    """代理下载 QomoChat 服务器上的文件，强制以附件形式返回。"""
    try:
        content, stored_name = qc.fetch_file(url)
    except qc.QomoError as exc:
        raise _fail(exc) from exc
    filename = (name or stored_name or "download").strip() or "download"
    return Response(
        content=content,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": "attachment; filename*=UTF-8''" + quote(filename),
            "Content-Length": str(len(content)),
        },
    )


@router.get("/admin/users")
async def admin_users(status: str = "") -> dict:
    try:
        return {"users": qc.admin_users(status.strip() or None)}
    except qc.QomoError as exc:
        raise _fail(exc) from exc
