"""QomoChat 小 AI 的工具后端。

把 QomoChat 服务端的 REST 接口封装成一组原子工具，供 Triton AIOS 的
“大 AI”调度、由系统执行器执行。默认服务地址来自：

    环境变量 QOMOCHAT_BASE_URL  >  config.json 的 qomochat_base_url
    >  apps/qomochat_app/manifest.json 的 base_url

登录成功后 Token 保存在 apps/qomochat_app/workspace/session.json，
后续工具调用自动带上，不需要每次重新登录。
"""

import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from app_registry import app_abs_path, find_app
from config_store import read_raw as read_app_config, write_public as write_public_config
from openai_api import BASE_DIR

DEFAULT_BASE_URL = "http://127.0.0.1:5000"
TIMEOUT = httpx.Timeout(25.0, connect=6.0)


class QomoError(Exception):
    """工具执行失败（连接失败 / 接口报错 / 未登录）。"""


def _config() -> dict:
    return read_app_config()


def manifest() -> dict | None:
    return find_app(kind="qomochat")


def base_url() -> str:
    raw = os.environ.get("QOMOCHAT_BASE_URL") or _config().get("qomochat_base_url")
    if not raw and manifest():
        raw = manifest().get("base_url")
    return str(raw or DEFAULT_BASE_URL).rstrip("/")


def workspace_dir() -> Path:
    app = manifest()
    if app:
        return app_abs_path(app, app.get("workdir") or "workspace")
    return BASE_DIR / "apps" / "qomochat_app" / "workspace"


def session_file() -> Path:
    return workspace_dir() / "session.json"


def load_session() -> dict:
    path = session_file()
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except (OSError, json.JSONDecodeError):
            pass
    return {}


def save_session(data: dict) -> None:
    path = session_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def clear_session() -> None:
    try:
        session_file().unlink(missing_ok=True)
    except OSError:
        pass


def _write_config(updates: dict) -> dict:
    """把公开配置写回 config.json（保留其它字段）。"""
    return write_public_config(updates)


def env_override() -> str | None:
    return os.environ.get("QOMOCHAT_BASE_URL") or None


def recent_servers() -> list:
    data = _config().get("qomochat_servers")
    return [str(x) for x in data] if isinstance(data, list) else []


def normalize_base_url(raw: str) -> str:
    """把用户输入整理成规范的服务器地址。"""
    value = (raw or "").strip()
    if not value:
        raise QomoError("请输入服务器地址，例如 http://127.0.0.1:5000")
    if any(ch.isspace() for ch in value):
        raise QomoError(f"服务器地址不能包含空格：{raw}")
    if "://" not in value:
        value = "http://" + value
    try:
        parsed = urlparse(value)
        host = parsed.hostname
        parsed.port  # 触发端口校验
    except ValueError as exc:
        raise QomoError(f"服务器地址不合法：{raw}（{exc}）") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or not host:
        raise QomoError(f"服务器地址不合法：{raw}")
    return value.rstrip("/")


def set_base_url(raw: str) -> dict:
    """切换 QomoChat 服务器：保存地址、清空旧 Token、返回最新状态。"""
    url = normalize_base_url(raw)
    servers = []
    for item in recent_servers():
        try:
            fixed = normalize_base_url(item)
        except QomoError:
            continue
        if fixed != url and fixed not in servers:
            servers.append(fixed)
    servers.insert(0, url)
    _write_config({"qomochat_base_url": url, "qomochat_servers": servers[:8]})
    clear_session()
    result = status()
    result["saved_base_url"] = url
    result["env_override"] = env_override()
    return result


def request(
    method: str,
    path: str,
    *,
    data: dict | None = None,
    files: dict | None = None,
    params: dict | None = None,
    token: str | None = None,
) -> Any:
    """调用 QomoChat 接口，失败统一抛 QomoError。"""
    headers: dict[str, str] = {}
    use_token = token if token is not None else load_session().get("token")
    if use_token:
        headers["Authorization"] = f"Bearer {use_token}"
    kwargs: dict[str, Any] = {"headers": headers, "params": params}
    if files is not None:
        kwargs["files"] = files
        if data:
            kwargs["data"] = data
    elif data is not None:
        kwargs["json"] = data
    url = base_url() + path
    try:
        with httpx.Client(timeout=TIMEOUT) as client:
            resp = client.request(method, url, **kwargs)
    except httpx.HTTPError as exc:
        raise QomoError(
            f"无法连接 QomoChat 服务 {base_url()}：{exc}。请确认服务已启动且地址正确。"
        ) from exc
    if resp.status_code >= 400:
        message = resp.text[:300]
        try:
            body = resp.json()
            message = (
                (body.get("error") or {}).get("message")
                or body.get("detail")
                or message
            )
        except (ValueError, AttributeError):
            pass
        raise QomoError(f"QomoChat 接口 {path} 返回 {resp.status_code}：{message}")
    if not resp.content:
        return {}
    try:
        return resp.json()
    except ValueError as exc:
        raise QomoError(f"QomoChat 接口 {path} 返回了非 JSON 内容") from exc


# ---------- 基础 ----------

def status() -> dict:
    session = load_session()
    out: dict[str, Any] = {
        "base_url": base_url(),
        "online": False,
        "logged_in": bool(session.get("token")),
        "user": session.get("user"),
        "recent_servers": recent_servers(),
        "env_override": env_override(),
    }
    try:
        out["health"] = request("GET", "/api/health", token="")
        out["online"] = True
    except QomoError as exc:
        out["error"] = str(exc)
        return out
    if out["logged_in"]:
        try:
            out["user"] = request("GET", "/api/auth/me").get("user")
        except QomoError as exc:
            out["session_error"] = str(exc)
    return out


def login(username: str, password: str) -> dict:
    if not username or not password:
        raise QomoError("登录需要 username 和 password")
    body = request(
        "POST",
        "/api/auth/login",
        data={"username": username, "password": password},
        token="",
    )
    save_session(
        {
            "base_url": base_url(),
            "token": body.get("token"),
            "user": body.get("user"),
        }
    )
    return {"ok": True, "user": body.get("user")}


def logout() -> dict:
    try:
        request("POST", "/api/auth/logout")
    except QomoError:
        pass
    finally:
        clear_session()
    return {"ok": True, "message": "已退出 QomoChat"}


def me() -> dict:
    return request("GET", "/api/auth/me").get("user")


# ---------- 好友 ----------

def friends() -> list:
    return request("GET", "/api/friends").get("friends", [])


def search_users(q: str) -> list:
    if not q:
        raise QomoError("搜索需要提供 q（UID / 用户名 / 昵称）")
    return request("GET", "/api/users/search", params={"q": q}).get("users", [])


def add_friend(uid: str) -> dict:
    if not uid:
        raise QomoError("添加好友需要提供对方 uid")
    return request("POST", "/api/friends", data={"uid": uid})


def delete_friend(uid: str) -> dict:
    if not uid:
        raise QomoError("删除好友需要提供对方 uid")
    return request("DELETE", f"/api/friends/{uid}")


# ---------- 会话 ----------

def open_direct(uid: str) -> dict:
    if not uid:
        raise QomoError("打开会话需要提供对方 uid")
    return request("POST", "/api/conversations/direct", data={"uid": uid}).get("conversation", {})


def messages(uid: str, after_seq: int = 0, limit: int = 50) -> dict:
    conv = open_direct(uid)
    body = request(
        "GET",
        f"/api/conversations/{conv['id']}/messages",
        params={"after_seq": max(0, int(after_seq or 0))},
    )
    rows = body.get("messages", [])
    rows = rows[-max(1, int(limit or 50)):]
    return {
        "friend": {"uid": uid, "nickname": (conv.get("friend") or {}).get("nickname")},
        "conversation_id": conv.get("id"),
        "count": len(rows),
        "messages": rows,
    }


def send(uid: str, text: str) -> dict:
    if not (text or "").strip():
        raise QomoError("发送消息需要提供 text")
    conv = open_direct(uid)
    body = request(
        "POST",
        f"/api/conversations/{conv['id']}/messages",
        data={"text": text},
    )
    return {
        "ok": True,
        "friend": {"uid": uid, "nickname": (conv.get("friend") or {}).get("nickname")},
        "conversation_id": conv.get("id"),
        "message": body.get("message"),
    }


def send_file(uid: str, path: str, text: str = "") -> dict:
    file_path = Path(path).expanduser()
    if not file_path.is_file():
        raise QomoError(f"本地文件不存在：{file_path}")
    conv = open_direct(uid)
    with file_path.open("rb") as fh:
        body = request(
            "POST",
            f"/api/conversations/{conv['id']}/files",
            data={"text": text or ""},
            files={"file": (file_path.name, fh, "application/octet-stream")},
        )
    return {
        "ok": True,
        "friend": {"uid": uid, "nickname": (conv.get("friend") or {}).get("nickname")},
        "conversation_id": conv.get("id"),
        "message": body.get("message"),
    }


def clear_history(uid: str) -> dict:
    conv = open_direct(uid)
    return request("POST", f"/api/conversations/{conv['id']}/clear")


def fetch_file(url: str) -> tuple[bytes, str]:
    """从当前 QomoChat 服务器下载一个文件，返回 (内容, 服务器上的文件名)。"""
    raw = (url or "").strip()
    if not raw:
        raise QomoError("缺少文件地址")
    base = base_url()
    if raw.startswith("http://") or raw.startswith("https://"):
        target = urlparse(raw)
        server = urlparse(base)
        if (target.scheme, target.netloc) != (server.scheme, server.netloc):
            raise QomoError("只允许下载当前 QomoChat 服务器上的文件")
        path = target.path + (f"?{target.query}" if target.query else "")
    else:
        path = raw if raw.startswith("/") else "/" + raw
    if not path.startswith("/uploads/"):
        raise QomoError(f"不支持的文件地址：{raw}")
    token = load_session().get("token")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        with httpx.Client(timeout=httpx.Timeout(180.0, connect=10.0)) as client:
            resp = client.get(base + path, headers=headers)
    except httpx.HTTPError as exc:
        raise QomoError(f"下载失败：{exc}") from exc
    if resp.status_code >= 400:
        raise QomoError(f"下载失败：服务器返回 {resp.status_code}")
    return resp.content, Path(path).name


# ---------- 管理员 ----------

def admin_users(status_filter: str | None = None) -> list:
    params = {"status": status_filter} if status_filter else None
    return request("GET", "/api/admin/users", params=params).get("users", [])


def admin_review(uid: str, action: str) -> dict:
    if action not in {"approve", "reject"}:
        raise QomoError("action 必须是 approve 或 reject")
    return request("POST", f"/api/admin/users/{uid}/review", data={"action": action})


def admin_status(uid: str, status_value: str) -> dict:
    return request("POST", f"/api/admin/users/{uid}/status", data={"status": status_value})


# ---------- 工具 schema ----------

TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "qomo_status",
            "description": "查看 QomoChat 客户端状态：服务地址、是否在线、当前登录用户。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "qomo_set_server",
            "description": "切换 QomoChat 服务器地址（换服务器时用）。会保存地址并清空旧登录 Token。",
            "parameters": {
                "type": "object",
                "properties": {
                    "base_url": {
                        "type": "string",
                        "description": "新的服务器地址，例如 http://127.0.0.1:5000",
                    }
                },
                "required": ["base_url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "qomo_login",
            "description": "登录 QomoChat 账号并保存会话 Token。",
            "parameters": {
                "type": "object",
                "properties": {
                    "username": {"type": "string", "description": "QomoChat 用户名"},
                    "password": {"type": "string", "description": "QomoChat 密码"},
                },
                "required": ["username", "password"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "qomo_friends",
            "description": "列出当前账号的好友（含 UID 与昵称）。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "qomo_search",
            "description": "按 UID / 用户名 / 昵称搜索 QomoChat 用户。",
            "parameters": {
                "type": "object",
                "properties": {"q": {"type": "string", "description": "搜索关键词"}},
                "required": ["q"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "qomo_add_friend",
            "description": "按 UID 添加 QomoChat 好友。",
            "parameters": {
                "type": "object",
                "properties": {"uid": {"type": "string", "description": "对方 UID"}},
                "required": ["uid"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "qomo_delete_friend",
            "description": "按 UID 删除 QomoChat 好友。",
            "parameters": {
                "type": "object",
                "properties": {"uid": {"type": "string", "description": "对方 UID"}},
                "required": ["uid"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "qomo_send",
            "description": "给 QomoChat 好友发送文字消息。",
            "parameters": {
                "type": "object",
                "properties": {
                    "uid": {"type": "string", "description": "对方 UID"},
                    "text": {"type": "string", "description": "消息内容"},
                },
                "required": ["uid", "text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "qomo_send_file",
            "description": "给 QomoChat 好友发送本机文件（≤50MB）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "uid": {"type": "string", "description": "对方 UID"},
                    "path": {"type": "string", "description": "本机文件路径"},
                    "text": {"type": "string", "description": "附带的文字说明（可选）"},
                },
                "required": ["uid", "path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "qomo_messages",
            "description": "查看与某位好友的聊天记录（默认最近 50 条）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "uid": {"type": "string", "description": "对方 UID"},
                    "after_seq": {"type": "integer", "description": "只取该序号之后的消息，默认 0"},
                    "limit": {"type": "integer", "description": "最多返回多少条，默认 50"},
                },
                "required": ["uid"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "qomo_clear",
            "description": "清空与某位好友的聊天记录（双方都清空后服务器会物理删除）。",
            "parameters": {
                "type": "object",
                "properties": {"uid": {"type": "string", "description": "对方 UID"}},
                "required": ["uid"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "qomo_admin_users",
            "description": "管理员查看用户列表，可按状态过滤（pending/active/rejected/disabled）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {"type": "string", "description": "用户状态，留空表示全部"}
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "qomo_admin_review",
            "description": "管理员审核注册用户：approve 通过 / reject 拒绝。",
            "parameters": {
                "type": "object",
                "properties": {
                    "uid": {"type": "string", "description": "待审核用户 UID"},
                    "action": {"type": "string", "description": "approve 或 reject"},
                },
                "required": ["uid", "action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "qomo_admin_status",
            "description": "管理员修改用户状态：active / disabled / rejected。",
            "parameters": {
                "type": "object",
                "properties": {
                    "uid": {"type": "string", "description": "目标用户 UID"},
                    "status": {"type": "string", "description": "active / disabled / rejected"},
                },
                "required": ["uid", "status"],
            },
        },
    },
]


TOOL_NAMES = [t["function"]["name"] for t in TOOL_SCHEMAS]


def execute_tool(name: str, args: dict) -> str:
    """系统执行器入口：执行一个 qomo_* 工具，返回 JSON 字符串。"""
    args = args or {}
    try:
        if name == "qomo_status":
            result = status()
        elif name == "qomo_set_server":
            result = set_base_url(str(args.get("base_url") or ""))
        elif name == "qomo_login":
            result = login(str(args.get("username") or ""), str(args.get("password") or ""))
        elif name == "qomo_friends":
            result = {"friends": friends()}
        elif name == "qomo_search":
            result = {"users": search_users(str(args.get("q") or ""))}
        elif name == "qomo_add_friend":
            result = add_friend(str(args.get("uid") or ""))
        elif name == "qomo_delete_friend":
            result = delete_friend(str(args.get("uid") or ""))
        elif name == "qomo_send":
            result = send(str(args.get("uid") or ""), str(args.get("text") or ""))
        elif name == "qomo_send_file":
            result = send_file(
                str(args.get("uid") or ""),
                str(args.get("path") or ""),
                str(args.get("text") or ""),
            )
        elif name == "qomo_messages":
            result = messages(
                str(args.get("uid") or ""),
                int(args.get("after_seq") or 0),
                int(args.get("limit") or 50),
            )
        elif name == "qomo_clear":
            result = clear_history(str(args.get("uid") or ""))
        elif name == "qomo_admin_users":
            result = {"users": admin_users(str(args.get("status") or "") or None)}
        elif name == "qomo_admin_review":
            result = admin_review(str(args.get("uid") or ""), str(args.get("action") or ""))
        elif name == "qomo_admin_status":
            result = admin_status(str(args.get("uid") or ""), str(args.get("status") or ""))
        else:
            return json.dumps({"error": f"未知工具：{name}"}, ensure_ascii=False)
    except QomoError as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"error": f"QomoChat 工具执行异常：{exc}"}, ensure_ascii=False)
    return json.dumps(result, ensure_ascii=False, default=str)
