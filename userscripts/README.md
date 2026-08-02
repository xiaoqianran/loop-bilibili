# Userscripts

浏览器侧字幕工具，协议与 `packages/bili_subbatch` / Chrome SubBatch 对齐。

## `bili-subbatch.user.js`（v0.8.1）

**AI 工作台（v0.8）**：三栏导航，**AI 笔记默认全高画布**；对齐业界油猴 AI 脚本实践（见 [PEER_AI_PRACTICES.md](PEER_AI_PRACTICES.md)）。Catppuccin Mocha、可拖拽/拉伸/贴边。  
**v0.8.1**：仓库内 **不** 硬编码 API Key；GM stream→text 回退 **不会** 并行双 POST。

| 工作区 | 用途 |
|--------|------|
| **AI 笔记**（默认） | 全高输出画布 + 一键「开始分析」 |
| **字幕库** | 扫描、勾选、下载 SRT/TXT、送 AI |
| **设置** | OpenAI 兼容 API / 提示词 / 流式开关 |

容器 `pointer-events: none`，空白穿透。流程：CC → **字幕库**扫描勾选 → **AI 笔记**分析。

### 模式（自动 + 可切换）

| 模式 | 行为 |
|------|------|
| **自动识别**（默认） | 按 URL/页内信息识别；**视频页默认「单个视频」**（不因多分P自动变选集） |
| 单个视频 | 只处理当前分P（`?p=`） |
| 视频选集 | 展开该稿全部分P |
| 个人主页 / 收藏夹 / 合集 / 搜索页 | 分页拉列表 |

合集播放页 `/list/{mid}?sid=` 会识别为**合集**（不再误判成单视频）。  
若自动不准，用下拉框强制切换；状态区会显示「自动本会是：xxx（已手动覆盖）」。

### 支持的页面

| 类型 | 示例 URL | 接口 |
|------|----------|------|
| **单个视频** | `www.bilibili.com/video/BVxxxx` | `view/detail` + 字幕链 |
| **视频选集** | 同上，模式选「视频选集」 | `pages[]` 拆成多条 |
| **个人主页** | `space.bilibili.com/{mid}` / `…/video` | `x/space/wbi/arc/search` |
| **收藏夹** | `…/favlist?fid=` · `medialist/detail/ml{id}` | `x/v3/fav/resource/list` |
| **合集** | `space…/lists/{sid}` · `collectiondetail?sid=` · `/list/{mid}?sid=` | `polymer/…/seasons_archives_list` |
| **搜索页** | `search.bilibili.com/all?keyword=` | `x/web-interface/wbi/search/type` |

未识别时会尝试从页面 DOM 扫 `a[href*="/video/BV"]`（兜底）。

### 能力

- 自动识别页面类型（badge 显示）
- **扫描当前页**：分页拉取列表（可设「最多页」、间隔 ms）
- 列表勾选 / 全选 / 全不选
- **下载 SRT / TXT**、复制全文、复制 BV 列表
- 批量中可 **停止**；限速默认 400ms
- Cookie：当前浏览器登录态（`GM_xmlhttpRequest`）
- **AI 分析**（页内 fetch 流式优先 · 见下）

### 安装

1. [Tampermonkey](https://www.tampermonkey.net/)
2. 导入或粘贴 `bili-subbatch.user.js`（覆盖旧版，版本 **0.8.1**）  
3. 右下角 **CC** 打开工作台（默认 **AI 笔记**）  
4. **字幕库** → 扫描 / 勾选 → **AI 笔记** → **开始分析**  
5. **设置**：Base URL / Key / Model → **保存**（流式默认开）  

### UI

| 项 | 说明 |
|----|------|
| 主题 | [Catppuccin Mocha](https://catppuccin.com/palette/) 玻璃拟态 |
| 导航 | **AI 笔记** / **字幕库** / **设置** |
| 流式滚动 | **粘底**默认开；上滑暂停；锚点 + 双 rAF |
| 画布操作 | 粘底 / 复制 / 顶部；流式光标与 live 点 |
| 穿透 / 拖拽 / 贴边 | 同 v0.7 |
| 记忆 | UI `bili-subbatch-ui-v2`；AI `bili-subbatch-ai-v2`（GM_setValue 优先） |

### Peer 实践（v0.8 采纳）

详见 [PEER_AI_PRACTICES.md](PEER_AI_PRACTICES.md)。至少包括：

1. **页内 `fetch` + stream reader 优先**（opencli 实测；失败再 GM）  
2. **GM 不设 `timeout`**；支持 `responseType: "stream"` + `getReader`（GreasyFork 459997 风格）  
3. **粘底阈值 + 用户上滑暂停** + rAF 合并绘制  
4. **GM_setValue / localStorage** 双写配置  
5. **AbortController / GM abort** 仅停 AI  
6. **reasoning 字段**与字幕截断

---

## opencli 本地复现（2026-08-03）

本机 `opencli doctor`：**Daemon + Extension 已连接**。

在 B 站视频页 `eval` 页内 `fetch`：

| 方式 | 结果 |
|------|------|
| 非流式 page fetch | HTTP 200，~1.3s（注意 content 可能先为 null、有 reasoning） |
| **流式 page fetch + getReader** | **成功**：首包 ~0.96s，总 ~1.7s，正文+思考均有 |

结论：API 与浏览器网络正常；问题在 **油猴 GM_xmlhttpRequest 路径**（timeout/fetch 模式/空闲断连），不在密钥或网关本身。

## AI 分析（油猴重点）

把**已勾选视频的字幕**发给任意 **OpenAI 兼容** Chat Completions 接口，支持：

| 阶段 | 能力 |
|------|------|
| **发送前** | 自定义 System 提示词 + User 模板注入；变量 `{{title}}` `{{bvid}}` `{{author}}` `{{subtitle}}` |
| **发送中** | `stream: true` SSE 流式输出，结果区实时刷新 |
| **发送后** | Markdown 渲染；**fenced 代码块** + **highlight.js 多语言高亮**；**```mermaid** 图 |

### 配置项（AI 面板 → 配置）

| 字段 | 说明 | 默认示例 |
|------|------|----------|
| Base URL | 兼容网关，需含 `/v1` | `https://…/v1` |
| API Key | `Authorization: Bearer …` | 面板内填写，**存本机** |
| Model | 模型名 | 如 `openai/gpt-oss-120b` |
| Temperature | 0–2 | `0.4` |
| Max tokens | 生成上限 | `4096` |
| 流式输出 | 页内/SSE 保活 | **默认开** |
| System / User 提示词 | 注入字幕变量 | `{{title}}` `{{bvid}}` `{{author}}` `{{subtitle}}` |

#### v0.6.1 / v0.7.1 修复笔记

**「一直连接中」**（字段）：`gpt-oss` 流式先推 `reasoning`/`reasoning_content`，旧代码只读 `content`。

**「client_gone / context canceled」**（你贴的日志，v0.7.1）：

| 现象 | 原因 |
|------|------|
| 状态 error · 结束原因 `client_gone` | **浏览器/油猴客户端先断开**，不是模型本身失败 |
| 响应约 10.0s，已有输出 token | 非流式等整包；空闲无字节时中间层/扩展常在 ~10s 掐连接 |
| 输入 13k tokens | 字幕过长，生成更慢，更容易断 |

修复：

1. **默认强制 SSE 流式保活**（`stream: true`，配置键 `bili-subbatch-ai-v2`）  
2. **v0.7.2：完全不传 `timeout` 字段**  
   - Tampermonkey 文档：设置 `timeout` 会 **enforce fetch mode**  
   - Chrome 下 fetch 模式 **onprogress 不可用**，且 `timeout:0` 仍会触发 **ontimeout**  
   - 这就是界面「请求超时（已禁用 timeout…）」的直接原因  
3. ontimeout/onerror 若已有正文/思考 → **按成功结束**，不丢结果  
4. 流式阶段只刷纯文本，结束后再 Markdown/高亮/Mermaid  
5. 字幕超长截断（默认约 1.8 万字）控制 token

配置持久化：`GM_setValue` + `localStorage` 键 **`bili-subbatch-ai-v2`**（勿把 Key 提交进仓库）。

脚本声明 `@connect *`，可换成任意兼容地址（如 OpenAI / 自建 NewAPI / OneAPI / 本地 vLLM）。

### 使用步骤

1. 扫描并勾选 1～N 条视频（无字幕会先按 bili_subbatch 协议拉取）  
2. 点 **AI 分析**（或 **AI 面板** 查看配置/结果）  
3. 流式输出出现在「结果」页；结束后自动：  
   - GFM Markdown（标题、列表、表格、引用…）  
   - ` ```lang ` 代码块 → highlight.js 高亮 + 语言标签  
   - ` ```mermaid ` → mermaid 渲染  
4. 生成中可点 **停止 AI**

未勾选时：若列表为空会尝试扫描当前页；仍无选中则提示先勾选。

### 渲染依赖（CDN）

首次渲染时从 jsDelivr 加载（需能访问外网）：

- `marked` — Markdown  
- `highlight.js`（atom-one-dark）— 代码高亮  
- `mermaid` — 流程图 / 时序图等  

CDN 失败时降级为简易 Markdown（代码块仍以 `<pre>` 展示）。

### 请求形态

```http
POST {baseUrl}/chat/completions
Authorization: Bearer {apiKey}
Content-Type: application/json

{
  "model": "…",
  "messages": [
    { "role": "system", "content": "…" },
    { "role": "user", "content": "…注入字幕后的模板…" }
  ],
  "temperature": 0.4,
  "max_tokens": 8192,
  "stream": true
}
```

流式解析标准 SSE：`data: {json}\n` / `data: [DONE]`；若网关不支持 stream，会尝试按完整 JSON 回退解析。

### 安全与合规

- API Key 只存在浏览器 `localStorage`，**不要**写进公开 README / commit  
- 字幕与提示词会发往你配置的第三方 API，注意隐私与 ToS  
- 仅供个人学习研究；字幕权利归 UP / B 站  

---

### 推荐用法

| 场景 | 操作 |
|------|------|
| 下一集视频字幕 | 视频页 → 扫描 → 下载 |
| 单集 AI 笔记 | 视频页 → 扫描 → **AI 分析** |
| 多分P 一起问 | 模式「视频选集」→ 扫描 → 全选 → AI 分析 |
| UP 主页批量下 SRT | 个人主页 → 调大「最多页」→ 扫描 → SRT |
| 给 CLI 用 | 勾选后「复制 BV 列表」→ `main.py subtitle` |

### 与 CLI 分工

| | 油猴 | `python3 main.py subtitle` |
|--|------|------------------------------|
| 交互列表页 | ✅ | 需 catalog / 列表文件 |
| 多 UP 全量 + resume + 进 git | ❌ | ✅ |
| 即时 AI 笔记 / Mermaid | ✅ v0.6 | 可后续做 processor |
| 登录 | 浏览器 | `BILI_COOKIE` |

### 版本摘要

| 版本 | 要点 |
|------|------|
| 0.1 | 单视频 SRT/TXT |
| 0.2 | 六种页面列表 + 批量 |
| 0.3 | Catppuccin 透明边栏 |
| 0.4 | 自动识别 + 手动模式 |
| 0.5 | 拖拽 / 拉伸 / 贴边收起 |
| **0.6** | **OpenAI 兼容 AI：提示词、流式、MD/高亮/Mermaid** |
| **0.6.1** | **修复推理模型 reasoning 字段；默认非流式；SSE 行缓冲** |
| **0.7.0** | **三工作区 UI：AI 全高画布 / 字幕库 / 设置** |
| **0.7.1** | **修复 client_gone：默认流式保活、timeout:0、字幕截断** |
| **0.7.2** | **禁止设置 timeout（TM 会强制 fetch 并误触 ontimeout）** |
| **0.7.3** | **opencli 验证后：页内 fetch 优先，GM 回退** |
| **0.7.4** | **流式粘底滚动 / 光标 / 复制** |
| **0.8.0** | **Peer 实践合入：GM stream reader、GM 存储、pure-logic 离线测试** |
| **0.8.1** | **移除硬编码 Key；GM 回退单飞（abort stream 后再 text）** |

### 自检

```bash
node userscripts/_wbi_check.mjs
node userscripts/_pure_logic_test.mjs   # 从 shipped 脚本提取 pure-logic 区域实测
```

### 注意

- 收藏夹 / 部分字幕需要**已登录**
- 大批量请提高间隔，避免风控
- AI 依赖外网 CDN 与你的 API 网关可用性
- 仅供个人学习研究；字幕权利归 UP / B 站
