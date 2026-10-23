"""配置读取与写入。

分两层：
  config.json       公开默认配置（会随预览版一起分发，不放任何私密信息）
  config.local.json 本机私密配置（API Key / 私人服务器地址，.gitignore 排除，不打包）

读取时把 config.local.json 深一层合并到 config.json 上，所以旧代码只读
config.json 的地方也能拿到本机配置。
"""

import json
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
PUBLIC_CONFIG = BASE_DIR / "config.json"
LOCAL_CONFIG = BASE_DIR / "config.local.json"


def _read(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def read_raw() -> dict[str, Any]:
    """公开配置 + 本机私密配置（后者优先，字典字段做一层合并）。"""
    merged = _read(PUBLIC_CONFIG)
    for key, value in _read(LOCAL_CONFIG).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            child = dict(merged[key])
            child.update(value)
            merged[key] = child
        else:
            merged[key] = value
    return merged


def _write(path: Path, data: dict, indent: int = 2) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=indent) + "\n", encoding="utf-8")


def write_public(updates: dict) -> dict:
    """更新公开配置（不会写入本机私密文件）。"""
    data = _read(PUBLIC_CONFIG)
    data.update(updates)
    _write(PUBLIC_CONFIG, data)
    return read_raw()


def write_local(updates: dict) -> dict:
    """更新本机私密配置（API Key 等）。"""
    data = _read(LOCAL_CONFIG)
    data.update(updates)
    _write(LOCAL_CONFIG, data)
    return read_raw()
