# Triton AIOS 技能（Skill）系统设计方案

> 目标：把输入框左边的「Agent」开关换成「Skill」入口。点开后弹出技能面板，
> 选中某个技能后，这一轮对话会带上该技能**独立的提示词 + 独立的工具集 + 独立的执行逻辑**，
> 而且新增技能不需要改动主控代码。

---

## 1. 总体结构

```
输入框  [Skill ▾]  ← 点开技能面板（弹窗）
    │
    ├─ 选择技能 ──► POST /api/chat/stream {message, skill:"app_writer", ...}
    │
    └─ 添加技能 ──► POST /api/skills/upload（txt / json → 变成新技能）

后端：
  skills/                      技能包（一个技能一个模块，注册表驱动）
    base.py                    Skill 数据结构 + 注册表 + 关键词匹配
    app_writer.py              技能①「AI 写应用」
    ui_html.py                 技能②「系统界面」
    web_search.py              技能③「联网搜索」
    add_skill.py               技能④「添加技能」+ 自定义技能读写
    custom/<id>.json           上传得到的提示词技能
  skills_api.py                GET /api/skills、POST /api/skills/upload、DELETE /api/skills/{id}
  agents.py                    主控：按技能拼 system prompt + 工具集 + 执行器
  docs/APP_SDK.md              「AI 写应用」用的 SDK 说明（已由我整理好）
```

数据流（一次对话）：

```
用户消息 + skill_id
   → skills.get(skill_id)                    取技能定义
   → build_system_prompt(skill)              基础调度词 + 技能提示词
   → tool_schemas_for(skill)                 核心工具 + 技能专属工具（按 core_scope 裁剪）
   → execute_tool(name, args, skill)         技能执行器优先，核心工具兜底
   → SSE 事件（delta / tool / app_changed / ui_reload / skill_auto）
   → 前端：气泡文字 + 步骤 + 真实副作用（刷应用列表 / 重载界面 / 播放音乐…）
```

---

## 2. 技能的数据结构（skills/base.py）

```python
@dataclass
class Skill:
    id: str                      # 唯一 id，前端传的就是它
    name: str                    # 显示名（中文）
    icon: str                    # lucide 图标名
    description: str             # 弹窗里的一行说明
    prompt: str                  # 注入主控 system prompt 的技能提示词
    triggers: list[str] = []     # 关键词（不选技能时的自动触发）
    tools: list[dict] = []       # 该技能额外的 OpenAI 工具 schema
    executors: dict = {}         # 工具名 -> 执行函数（args -> json 字符串）
    core_scope: str = "base"     # all | base | none —— 允许看到的核心工具范围
    shows_workspace: bool = False# 激活时是否展开右侧 workspace 面板
    hint: str = ""               # 选中后输入框 placeholder
    source: str = "builtin"      # builtin | custom
```

注册表 API：`register() / get() / all_skills() / public_list() / auto_match(text)`。
新技能只要 `skills/__init__.py` 里多 import 一个模块即可，主控零改动。

---

## 3. 四个内置技能

### ① `app_writer` — AI 写应用

| 项目 | 内容 |
| --- | --- |
| 触发 | 弹窗选择；关键词：写个应用 / 做个应用 / 生成小工具 / 做个网页应用 / app |
| 提示词 | 「你是 AIOS 应用工坊。先 `app_sdk` 读 SDK，再 `app_create` 一次性写出可运行应用；必须能被 iframe 直接打开，不能有构建步骤。」 |
| 工具 | `app_sdk`（读 SDK）、`app_create`（id/名称/图标/文件表 一次写入）、`app_write`（追加或改单文件）、`app_list`、`app_open`（打开刚生成的应用窗口） |
| 执行逻辑 | 文件写到 `apps/<id>/`（`manifest.json` + `index.html` + 可选 `app.js/styles.css`）；写完发 `app_changed` 事件 → 前端刷新任务栏 / 启动台 / 已开窗口 |
| 界面显示 | manifest `kind: "html"`、`entry: "index.html"` → 前端用 iframe 窗口承载，等价于系统自带应用 |
| SDK | `docs/APP_SDK.md`（我整理的规范：manifest 字段、可用的宿主能力、窗口尺寸、主题变量、禁止项） |

### ② `ui_html` — 系统界面（改 HTML，不改 MOD）

| 项目 | 内容 |
| --- | --- |
| 触发 | 弹窗选择；关键词：改 HTML / 改系统界面 / 换壳 / 改主题源码 |
| 提示词 | 「你是界面工程师，直接改 `static/index.html`、`static/styles.css` 来换界面；必须保留 `#triton-desktop`、`#chat-input`、脚本引用，改完立即 reload 验证。」 |
| 工具 | `html_read`、`html_patch`（find/replace，最安全）、`html_write`（整文件）、`html_backups`、`html_restore`、`ui_reload` |
| 执行逻辑 | 写前自动备份到 `static/.ui-backups/`；写后做结构校验（缺关键节点直接拒绝并回滚）；成功后发 `ui_reload` 事件让前端重载页面 |
| 与 ui_api 的区别 | `ui_api`（ui_apply）是运行时改元素样式，刷新即失效；本技能直接改源码，是持久的界面变更 |

### ③ `web_search` — 联网搜索

| 项目 | 内容 |
| --- | --- |
| 触发 | 弹窗选择；关键词：搜一下 / 搜索 / 联网查 / 最新消息 / 帮我查 |
| 提示词 | 「你可以联网。先 `web_search` 拿候选结果，再 `web_fetch` 打开最相关的 1-3 篇读正文，回答必须带来源链接，不许编造。」 |
| 工具 | `web_search(query, count)`、`web_fetch(url, max_chars)` |
| 执行逻辑 | 后端自研：DuckDuckGo(html) → DuckDuckGo(lite) → Bing → 百度，依次降级；`config.json: search.api_key`（provider=tavily）时优先走 JSON API；带 TTL 缓存；`web_fetch` 用标准库 HTMLParser 抽正文（零第三方依赖），并做 SSRF 防护（放行代理 fake-ip 段） |
| 返回 | `{ok, provider, results:[{title,url,snippet,source}], cached}` |

### ④ `add_skill` — 添加技能

| 项目 | 内容 |
| --- | --- |
| 触发 | 弹窗里「添加技能 → 选择 txt/json」；或选中本技能后用对话创建 |
| 上传 | txt：全文即提示词，文件名即技能名；json：`{name, description, prompt, keywords, icon, tools}`（缺省字段自动补） |
| 工具 | `skill_list`、`skill_create`、`skill_update`、`skill_delete` |
| 执行逻辑 | 落盘到 `skills/custom/<id>.json` 并热注册；返回后前端刷新技能面板，新技能立刻可选 |
| 扩展能力 | 自定义技能可写 `tools: ["web_search","python_run"]` 复用内置工具，等于「用提示词 + 现成工具」拼出新技能 |

---

## 4. 前端交互

* 输入框左侧按钮：`Agent` → **`Skill`**（图标 `wand-sparkles`）。
* 点击弹出面板（`#skill-pop`）：列表 = `GET /api/skills`（内置 + 自定义），每项显示图标 / 名称 / 说明；
  顶部有「普通对话（不使用技能）」用于退出技能。
* 选中后：输入框上方出现技能条（`#skill-strip`，可一键清除），placeholder 变成该技能的 hint。
* 发送时：`{message, session_id, attachments, skill}`。
* 服务端事件：
  | 事件 | 前端动作 |
  | --- | --- |
  | `app_changed` | 重新拉 `/api/apps`，刷新任务栏 / 启动台 / 已打开的应用窗口 |
  | `ui_reload` | 提示后 `location.reload()` |
  | `skill_changed` | 重新拉 `/api/skills`，技能面板立刻出现新技能 |
  | `skill_auto` | 提示「已自动启用技能：xxx」 |
  | `open_window` / `play` / `ui_command` | 保持原有行为 |

---

## 5. 安全与回退

1. **HTML 改动**：写前备份 + 结构校验 + `html_restore`；校验不过直接拒绝写入。
2. **应用生成**：只允许写 `apps/<id>/`（id 白名单字符），不会碰到系统文件。
3. **搜索**：多引擎依次降级，全失败时返回明确错误而不是空结果；结果带来源，模型必须引用。
4. **技能未命中**：`skill` 传了未知 id / 自定义技能被删除 → 自动回退「普通对话」，不报错。
5. **工具越权**：`core_scope` 控制技能能看到哪些核心工具；执行器只暴露该技能声明过的工具。

---

## 6. 再加一个技能要做什么

```python
# skills/my_skill.py
from .base import Skill, register

def run(args):  # 工具执行逻辑
    return json.dumps({"ok": True, "echo": args.get("text")})

register(Skill(
    id="my_skill", name="我的技能", icon="puzzle",
    description="一句话说明",
    prompt="你是……（注入主控的行为约束）",
    triggers=["我的技能关键词"],
    tools=[{"type": "function", "function": {"name": "my_tool", "description": "...", "parameters": {...}}}],
    executors={"my_tool": run},
    core_scope="base",
))
```

再到 `skills/__init__.py` 里 `from . import my_skill`，前端弹窗会自动多出一项，主控 / 前端都不用改。
