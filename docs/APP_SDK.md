# Triton AIOS 应用 SDK（v1）

写给「AI 写应用」技能用的规范。**没有构建步骤、没有依赖安装**：
生成一个文件夹，AIOS 就能把它当系统应用显示出来。

## 1. 应用长什么样

```
apps/<app_id>/
  manifest.json      # 必需，应用自述
  index.html         # 必需（manifest.entry 指向它），窗口里用 iframe 直接打开
  app.js  style.css  # 可选，index.html 里用相对路径引用
```

`<app_id>`：小写字母 / 数字 / 下划线，建议 `ai_` 前缀，例如 `ai_pomodoro`。

## 2. manifest.json

```json
{
  "name": "番茄钟",
  "icon": "timer",
  "kind": "html",
  "description": "25 分钟专注 + 5 分钟休息",
  "entry": "index.html",
  "version": "1.0.0",
  "author": "AI",
  "window": { "width": 420, "height": 380 }
}
```

* `icon`：lucide 图标名（`timer` / `calculator` / `sticky-note` / `gamepad-2` / `chart-line` …）。
* `kind` 必须是 `"html"`，AIOS 才知道用网页窗口承载它。
* `window` 是建议尺寸（可省略，默认 680×520）。

## 3. 页面里能用的宿主能力（同源 API）

所有请求都用 `fetch(url, ...)`，路径是绝对的 `/api/...`。

| 能力 | 调用 |
| --- | --- |
| 列工作区文件 | `GET /api/workspace/list` → `{files:[{name,path,type,size,children?}]}` |
| 读文件 | `GET /api/workspace/read?path=...` → `{content}` |
| 写文件（存数据就用它） | `PUT /api/workspace/write`，body `{path, content}` |
| 删 / 改名 / 建目录 | `POST /api/workspace/delete` / `rename` / `mkdir`，body `{path,...}` |
| 运行 Python（进阶） | `POST /api/python/run`，body `{path}` |
| 应用列表 | `GET /api/apps` |

存自己的数据统一放 `apps_data/<app_id>/`，例如：

```js
const DATA = 'apps_data/ai_pomodoro/state.json';
async function save(state){
  await fetch('/api/workspace/write', {
    method:'PUT', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({ path: DATA, content: JSON.stringify(state, null, 2) })
  });
}
async function load(){
  const r = await fetch('/api/workspace/read?path=' + encodeURIComponent(DATA));
  if (!r.ok) return { rounds: 0 };
  return JSON.parse((await r.json()).content || '{}');
}
```

## 4. 样式约定

窗口是 iframe，AIOS 的主题变量不会自动继承，自己写一份即可（跟随系统深浅色）：

```css
:root{
  --bg:#f5f7fb; --panel:#fff; --text:#1b2333; --dim:#5d6679;
  --line:rgba(21,30,48,.13); --accent:#4d6ef8;
}
@media (prefers-color-scheme: dark){
  :root{ --bg:#171d2e; --panel:#242c40; --text:#e9edf6; --dim:#a7afc3;
         --line:rgba(255,255,255,.14); --accent:#6e8bff; }
}
html,body{margin:0;height:100%;background:var(--bg);color:var(--text);
  font:14px/1.45 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif}
```

布局要求：`html,body{height:100%}`，内容自适应，**不要出现整页滚动条除非确实需要**。
窗口默认 680×520，小工具建议 420×380 左右。

## 5. 硬性规则（违反会导致应用不可用）

1. 只写 HTML / CSS / 原生 JS，**不要**引入构建工具、npm、React 打包产物、TypeScript。
2. **不要依赖外部 CDN / 网络请求**（可能离线）。需要图标就内联 SVG 或 Unicode。
3. 不要用 `window.top`、`parent.location`、`alert` 阻塞式弹窗；提示用页面内元素。
4. 所有异步操作都要 `try/catch`，失败时在页面上显示可读的错误，不要留白屏。
5. 单文件优先：能写进 `index.html` 就不要拆；拆的话用相对路径 `./app.js`。
6. 页面里不要写 `localStorage` 作为唯一存储（换浏览器会丢）——重要数据写工作区文件。

## 6. 最小可用模板

```html
<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>番茄钟</title>
<style>
  :root{--bg:#f5f7fb;--panel:#fff;--text:#1b2333;--accent:#4d6ef8;--line:rgba(21,30,48,.13)}
  @media (prefers-color-scheme:dark){:root{--bg:#171d2e;--panel:#242c40;--text:#e9edf6;--accent:#6e8bff;--line:rgba(255,255,255,.14)}}
  html,body{margin:0;height:100%;background:var(--bg);color:var(--text);
    font:14px/1.45 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif}
  .wrap{height:100%;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:14px;padding:18px}
  .time{font-size:44px;font-weight:600;font-variant-numeric:tabular-nums}
  button{border:0;border-radius:10px;padding:9px 16px;background:var(--accent);color:#fff;font-size:14px;cursor:pointer}
</style>
</head>
<body>
  <div class="wrap">
    <div class="time" id="t">25:00</div>
    <button id="go">开始</button>
  </div>
<script>
  const el = document.getElementById('t'), go = document.getElementById('go');
  let left = 25*60, timer = null;
  function paint(){ el.textContent = String(Math.floor(left/60)).padStart(2,'0')+':'+String(left%60).padStart(2,'0'); }
  go.onclick = () => {
    if (timer){ clearInterval(timer); timer = null; go.textContent = '继续'; return; }
    go.textContent = '暂停';
    timer = setInterval(() => { left = Math.max(0, left-1); paint();
      if (!left){ clearInterval(timer); timer = null; go.textContent = '开始'; } }, 1000);
  };
  paint();
</script>
</body>
</html>
```

## 7. 交付前自检清单

* [ ] `manifest.json` 是合法 JSON，`kind="html"`，`entry` 指向真实存在的文件
* [ ] 在 680×520 和 380×300 两种尺寸下都不溢出、不白屏
* [ ] 刷新后数据仍在（写进了工作区文件）
* [ ] 没有外部网络依赖，没有控制台报错
