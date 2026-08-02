# Userscripts

浏览器侧字幕工具，协议与 `packages/bili_subbatch` / Chrome SubBatch 对齐。

## `bili-subbatch.user.js`（v0.7.0）

**世界级工作台 UI（v0.7）**：三栏导航，**AI 笔记默认占满主画布**；字幕库 / 设置各为独立页。Catppuccin Mocha 玻璃拟态、可拖拽/拉伸/贴边。

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
- **AI 分析**（v0.6，见下）

### 安装

1. [Tampermonkey](https://www.tampermonkey.net/)
2. 导入或粘贴 `bili-subbatch.user.js`（覆盖旧版即可，版本 **0.7.0**）  
3. 右下角 **CC** 打开工作台（默认 **AI 笔记** 全高画布）  
4. 切到 **字幕库** → 扫描 / 勾选 → 回 **AI 笔记** → **开始分析**  
5. 首次用 AI：进 **设置**，填 Base URL / Key / Model → **保存配置**（推理模型建议关流式）

### UI

| 项 | 说明 |
|----|------|
| 主题 | [Catppuccin Mocha](https://catppuccin.com/palette/) 玻璃拟态 |
| 导航 | **AI 笔记**（默认全高画布）/ **字幕库** / **设置** |
| 穿透 | 根节点 `pointer-events: none`；FAB / 面板 / 贴边标签可点 |
| 拖拽 | 按住标题栏拖动；松手靠近左右边缘会**贴边收起** |
| 拉伸 | 拖面板四边/四角（最小约 420×520，默认约 560×820） |
| 贴边 | 标题栏「⧉」/「—」；侧边 **AI · CC** 标签展开 |
| 记忆 | 几何+工作区 → `bili-subbatch-ui-v2`；AI → `bili-subbatch-ai-v1` |

---

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
| 流式输出 | SSE；推理模型先推 `reasoning` 时易误判为空 | **默认关**（更稳） |
| System 提示词 | 角色与输出格式约束 | 中文笔记 + MD + mermaid |
| User 模板 | 业务提示 + 字幕占位 | 见脚本默认 |

#### v0.6.1 修复（「一直连接中」）

实测 `openai/gpt-oss-120b` 返回字段：

- 流式 chunk 里经常是 `delta.reasoning` / `reasoning_content`，`content` 为空字符串  
- 非流式最终 `message.content` 有正文，同时带长 `reasoning`

旧版只读 `delta.content`，界面一直停在「连接中…」。现已：

1. 同时解析 `content` + `reasoning` / `reasoning_content`  
2. SSE **按行缓冲**（避免半包 JSON 丢弃）  
3. 默认 **非流式**；可选打开流式  
4. 状态栏显示接收进度（字节 / 正文长度 / 思考长度）

配置持久化：`localStorage` 键 **`bili-subbatch-ai-v1`**（**不会**随 git 同步；请勿把真实 Key 提交进仓库）。

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

### 自检

```bash
node userscripts/_wbi_check.mjs
```

### 注意

- 收藏夹 / 部分字幕需要**已登录**
- 大批量请提高间隔，避免风控
- AI 依赖外网 CDN 与你的 API 网关可用性
- 仅供个人学习研究；字幕权利归 UP / B 站
