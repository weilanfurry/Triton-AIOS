"""对话自动保存：写入项目 data/conversations.json。"""

import json
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/api", tags=["storage"])

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
CONV_FILE = DATA_DIR / "conversations.json"


def read_conversations() -> list:
    if CONV_FILE.exists():
        try:
            data = json.loads(CONV_FILE.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
        except (OSError, json.JSONDecodeError):
            pass
    return []


class ConversationsIn(BaseModel):
    conversations: list


@router.get("/conversations")
async def get_conversations() -> dict:
    return {"conversations": read_conversations()}


@router.put("/conversations")
async def save_conversations(body: ConversationsIn) -> dict:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CONV_FILE.write_text(
        json.dumps(body.conversations, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return {"status": "ok", "count": len(body.conversations)}
