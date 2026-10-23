"""技能③：联网搜索（后端自己实现的搜索接口）。

不依赖任何第三方解析库：
  - 无 API Key 时：DuckDuckGo(html) → DuckDuckGo(lite) → Bing，依次降级
  - config.json 里配了 search.api_key（provider=tavily）时：优先走 JSON API
  - 结果带 TTL 缓存；web_fetch 用标准库 HTMLParser 抽正文
"""

import html as html_lib
import ipaddress
import json
import re
import socket
import time
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, unquote, urlparse

import httpx

from .base import Skill, register
from config_store import read_raw as read_app_config

BASE_DIR = Path(__file__).resolve().parent.parent

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
DEFAULT_TIMEOUT = 18.0
DEFAULT_COUNT = 5
MAX_COUNT = 10
MAX_TEXT = 12000

_CACHE: dict[str, tuple[float, list[dict]]] = {}


def _search_conf() -> dict:
    raw: dict[str, Any] = read_app_config()
    conf = raw.get("search") if isinstance(raw, dict) else None
    return conf if isinstance(conf, dict) else {}


def _ok(**kw: Any) -> str:
    payload = {"ok": True}
    payload.update(kw)
    return json.dumps(payload, ensure_ascii=False)


def _err(message: str, **extra: Any) -> str:
    payload = {"ok": False, "error": message}
    payload.update(extra)
    return json.dumps(payload, ensure_ascii=False)


# ===== 解析器 =====

class _DdgParser(HTMLParser):
    """DuckDuckGo html/lite 的结果页：按顺序取标题 + 摘要。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.items: list[dict] = []
        self._grab: str | None = None
        self._buf: list[str] = []
        self._href = ""
        self._snippet_for: int | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {k: (v or "") for k, v in attrs}
        cls = attr.get("class", "")
        if tag == "a" and ("result__a" in cls or "result-link" in cls or "result__url" in cls):
            self._grab, self._buf, self._href = "title", [], attr.get("href", "")
        elif ("result__snippet" in cls or "result-snippet" in cls) and self.items:
            self._grab, self._buf = "snippet", []
            self._snippet_for = len(self.items) - 1

    def handle_data(self, data: str) -> None:
        if self._grab:
            self._buf.append(data)

    def handle_endtag(self, tag: str) -> None:
        if not self._grab:
            return
        if self._grab == "title" and tag == "a":
            title = _clean(" ".join(self._buf))
            url = _unwrap_ddg(self._href)
            if title and url:
                self.items.append({"title": title, "url": url, "snippet": ""})
            self._grab = None
        elif self._grab == "snippet" and tag in {"a", "td", "div"}:
            text = _clean(" ".join(self._buf))
            if text and self._snippet_for is not None and self._snippet_for < len(self.items):
                self.items[self._snippet_for]["snippet"] = text
            self._grab = None
            self._snippet_for = None


class _BingParser(HTMLParser):
    """Bing 结果页：li.b_algo 里的第一个链接 + 描述。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.items: list[dict] = []
        self._algo = 0
        self._grab: str | None = None
        self._buf: list[str] = []
        self._href = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {k: (v or "") for k, v in attrs}
        cls = attr.get("class", "")
        if tag == "li" and "b_algo" in cls:
            self._algo += 1
            self.items.append({"title": "", "url": "", "snippet": ""})
        elif tag == "a" and self._algo and not self.items[self._algo - 1]["url"]:
            self._grab, self._buf, self._href = "title", [], attr.get("href", "")
        elif tag == "p" and self._algo:
            self._grab, self._buf = "snippet", []

    def handle_data(self, data: str) -> None:
        if self._grab:
            self._buf.append(data)

    def handle_endtag(self, tag: str) -> None:
        if not self._grab:
            return
        if self._grab == "title" and tag == "a":
            item = self.items[self._algo - 1]
            item["title"] = _clean(" ".join(self._buf))
            item["url"] = self._href if self._href.startswith("http") else ""
            self._grab = None
        elif self._grab == "snippet" and tag == "p":
            item = self.items[self._algo - 1]
            if not item["snippet"]:
                item["snippet"] = _clean(" ".join(self._buf))
            self._grab = None


class _TextParser(HTMLParser):
    """正文抽取：丢掉脚本 / 样式 / 导航，按块级标签换行。"""

    SKIP = {"script", "style", "noscript", "svg", "head", "template", "iframe"}
    BLOCK = {"p", "div", "section", "article", "li", "br", "tr", "h1", "h2", "h3", "h4", "pre", "blockquote"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title = ""
        self._skip = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self.SKIP:
            self._skip += 1
        if tag == "title":
            self._in_title = True
        if tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP and self._skip:
            self._skip -= 1
        if tag == "title":
            self._in_title = False
        if tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        if self._in_title:
            self.title += data
        self.parts.append(data)

    def text(self) -> str:
        raw = html_lib.unescape("".join(self.parts))
        lines = [_clean(line) for line in raw.split("\n")]
        return "\n".join(line for line in lines if line)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _unwrap_ddg(href: str) -> str:
    if not href:
        return ""
    if href.startswith("//"):
        href = "https:" + href
    try:
        parsed = urlparse(href)
        if "duckduckgo.com" in parsed.netloc:
            target = parse_qs(parsed.query).get("uddg")
            if target:
                return unquote(target[0])
        return href
    except ValueError:
        return href


# 代理软件（Clash / Surge 等）的 fake-ip 段：域名会被解析到这里，不代表内网
_FAKE_IP_NET = ipaddress.ip_network("198.18.0.0/15")


def _is_internal(ip: ipaddress._BaseAddress) -> bool:
    return bool(ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved)


def _blocked_host(host: str) -> bool:
    """SSRF 防护：不抓本机 / 内网地址。

    注意：本机装了代理时，域名会被解析到 fake-ip 段（198.18.0.0/15），
    那不是内网，必须放行，否则所有网页都抓不到。
    """
    host = (host or "").strip().lower().strip("[]")
    if not host or host in {"localhost", "127.0.0.1", "::1", "0.0.0.0"}:
        return True
    if host.endswith((".local", ".internal", ".localhost")):
        return True
    # 字面量 IP：直接判断
    try:
        literal = ipaddress.ip_address(host)
        return _is_internal(literal)
    except ValueError:
        pass
    # 域名：解析结果全是内网才拦（fake-ip 直接放行）
    try:
        info = socket.getaddrinfo(host, None)
    except OSError:
        return False
    addrs: list[ipaddress._BaseAddress] = []
    for entry in info:
        try:
            addrs.append(ipaddress.ip_address(entry[4][0]))
        except ValueError:
            continue
    if not addrs:
        return False
    if all(ip in _FAKE_IP_NET for ip in addrs):
        return False
    return all(_is_internal(ip) for ip in addrs)


async def _get(url: str, *, params: dict | None = None, data: dict | None = None) -> str:
    headers = {
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    timeout = float(_search_conf().get("timeout") or DEFAULT_TIMEOUT)
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=8.0), follow_redirects=True) as client:
        if data is not None:
            resp = await client.post(url, data=data, headers=headers)
        else:
            resp = await client.get(url, params=params, headers=headers)
        resp.raise_for_status()
        return resp.text


# ===== 搜索提供方 =====

async def _search_tavily(query: str, count: int, api_key: str) -> list[dict]:
    timeout = float(_search_conf().get("timeout") or DEFAULT_TIMEOUT)
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=8.0)) as client:
        resp = await client.post(
            "https://api.tavily.com/search",
            json={"api_key": api_key, "query": query, "max_results": count, "search_depth": "basic"},
        )
        resp.raise_for_status()
        data = resp.json()
    out = []
    for item in (data.get("results") or [])[:count]:
        out.append({
            "title": _clean(item.get("title")),
            "url": item.get("url") or "",
            "snippet": _clean(item.get("content")),
            "source": "tavily",
        })
    return out


async def _search_ddg_html(query: str, count: int) -> list[dict]:
    page = await _get("https://html.duckduckgo.com/html/", params={"q": query})
    parser = _DdgParser()
    parser.feed(page)
    return [dict(item, source="duckduckgo") for item in parser.items[:count]]


async def _search_ddg_lite(query: str, count: int) -> list[dict]:
    page = await _get("https://lite.duckduckgo.com/lite/", data={"q": query})
    parser = _DdgParser()
    parser.feed(page)
    return [dict(item, source="duckduckgo-lite") for item in parser.items[:count]]


async def _search_bing(query: str, count: int) -> list[dict]:
    page = await _get("https://www.bing.com/search", params={"q": query, "setlang": "zh-CN", "count": str(count)})
    parser = _BingParser()
    parser.feed(page)
    out = [item for item in parser.items if item["url"] and item["title"]]
    return [dict(item, source="bing") for item in out[:count]]


async def _search_baidu(query: str, count: int) -> list[dict]:
    """百度作为最后兜底：只取标题 + 链接（结果页结构简单，正则足够）。"""
    page = await _get("https://www.baidu.com/s", params={"wd": query, "rn": str(count)})
    out: list[dict] = []
    for match in re.finditer(r'<h3[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', page, re.S):
        href, raw = match.group(1), match.group(2)
        title = _clean(re.sub(r"<[^>]+>", "", raw))
        if title and href.startswith("http"):
            out.append({"title": title, "url": href, "snippet": "", "source": "baidu"})
        if len(out) >= count:
            break
    return out


PROVIDERS: list[tuple[str, Callable[[str, int], Any]]] = [
    ("duckduckgo", _search_ddg_html),
    ("duckduckgo-lite", _search_ddg_lite),
    ("bing", _search_bing),
    ("baidu", _search_baidu),
]


# ===== 工具 =====

async def tool_web_search(args: dict) -> str:
    query = _clean(args.get("query") or args.get("q") or "")
    if not query:
        return _err("请提供搜索关键词 query")
    try:
        count = int(args.get("count") or DEFAULT_COUNT)
    except (TypeError, ValueError):
        count = DEFAULT_COUNT
    count = max(1, min(MAX_COUNT, count))

    conf = _search_conf()
    ttl = float(conf.get("cache_seconds") or 600)
    key = query.lower() + "|" + str(count)
    hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < ttl:
        return _ok(query=query, provider="cache", cached=True, results=hit[1])

    problems: list[str] = []
    api_key = str(conf.get("api_key") or "").strip()
    provider_name = str(conf.get("provider") or "auto").strip().lower()
    if api_key and provider_name in {"tavily", "auto"}:
        try:
            results = await _search_tavily(query, count, api_key)
            if results:
                _CACHE[key] = (time.time(), results)
                return _ok(query=query, provider="tavily", cached=False, results=results)
            problems.append("tavily 没有返回结果")
        except Exception as exc:                       # noqa: BLE001 - 降级到免密钥引擎
            problems.append("tavily 失败：" + type(exc).__name__)

    for name, fn in PROVIDERS:
        if provider_name not in {"auto", name}:
            continue
        try:
            results = await fn(query, count)
        except Exception as exc:                       # noqa: BLE001 - 换下一个引擎
            problems.append(name + " 失败：" + type(exc).__name__)
            continue
        if results:
            _CACHE[key] = (time.time(), results)
            return _ok(query=query, provider=name, cached=False, results=results)
        problems.append(name + " 没有返回结果")

    return _err(
        "所有搜索来源都失败了（可能是网络不通或都被限流）",
        query=query,
        problems=problems,
        hint="可以先用 web_fetch 直接打开已知网址，或稍后重试。",
    )


async def tool_web_fetch(args: dict) -> str:
    url = str(args.get("url") or "").strip()
    if not url:
        return _err("请提供 url")
    if url.startswith("//"):
        url = "https:" + url
    if not url.startswith(("http://", "https://")):
        return _err("只支持 http / https 链接：" + url)
    parsed = urlparse(url)
    if _blocked_host(parsed.hostname or ""):
        return _err("出于安全考虑，不能抓取本机或内网地址：" + url)
    try:
        limit = int(args.get("max_chars") or 4000)
    except (TypeError, ValueError):
        limit = 4000
    limit = max(500, min(MAX_TEXT, limit))

    try:
        page = await _get(url)
    except Exception as exc:                           # noqa: BLE001 - 如实回报失败原因
        return _err("抓取失败：" + type(exc).__name__ + " " + str(exc)[:120], url=url)

    parser = _TextParser()
    try:
        parser.feed(page)
    except Exception:                                  # noqa: BLE001 - 解析失败就用原始文本
        pass
    text = parser.text()
    return _ok(
        url=url,
        title=_clean(parser.title)[:120],
        chars=len(text),
        truncated=len(text) > limit,
        text=text[:limit],
    )


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "联网搜索网页，返回标题 / 链接 / 摘要列表（真实请求，不是凭记忆）。"
                "用户要查最新消息、资料、事实核对时必须先用它。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索关键词，用自然语言也可以"},
                    "count": {"type": "integer", "description": "返回条数，默认 5，最多 10"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_fetch",
            "description": "抓取一个网页并抽成纯文本正文（用来精读搜索结果，回答时引用来源）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "要打开的 http/https 链接"},
                    "max_chars": {"type": "integer", "description": "正文截断长度，默认 4000，最多 12000"},
                },
                "required": ["url"],
            },
        },
    },
]


register(Skill(
    id="web_search",
    name="联网搜索",
    icon="globe",
    description="真的发请求去搜网页，再打开结果页读正文，回答带来源",
    hint="想查什么？例如：帮我搜一下今天的人工智能新闻",
    order=30,
    triggers=("搜一下", "搜索一下", "帮我搜", "联网搜", "网上查", "查一下最新", "最新消息", "帮我查查", "google一下", "百度一下"),
    tools=TOOLS,
    executors={
        "web_search": tool_web_search,
        "web_fetch": tool_web_fetch,
    },
    core_scope="base",
    prompt=(
        "你是 AIOS 的「联网搜索」技能。回答必须来自真实抓取的内容。\n"
        "工作流程：\n"
        "1. 先调用 web_search 拿候选结果（可以多搜一两次换关键词）。\n"
        "2. 挑最相关的 1-3 条，用 web_fetch 打开读正文（摘要不够时一定要读正文）。\n"
        "3. 回答用中文，关键结论后面标出来源链接；搜不到 / 抓取失败就如实说，不要编造。\n"
        "注意：搜索接口可能返回英文结果，翻译要点即可，链接原样保留。"
    ),
))
