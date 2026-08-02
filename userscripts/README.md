# Userscripts

浏览器侧字幕工具，协议与 `packages/bili_subbatch` / Chrome SubBatch 对齐。

## `bili-subbatch.user.js`（v0.5.0）

**Catppuccin Mocha** 悬浮玻璃面板：可拖拽、可拉伸、可贴边自动收起。容器 `pointer-events: none`，空白穿透；仅 FAB / 面板 / 贴边标签可点。

流程：右下角 **CC** →（自动识别或手动选模式）→ 扫描列表 → 勾选 → 批量 SRT/TXT。

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
| **视频选集** | 同上，勾选「展开全部分P」 | `pages[]` 拆成多条 |
| **个人主页** | `space.bilibili.com/{mid}` / `…/video` | `x/space/wbi/arc/search` |
| **收藏夹** | `…/favlist?fid=` · `medialist/detail/ml{id}` | `x/v3/fav/resource/list` |
| **合集** | `space…/lists/{sid}` · `collectiondetail?sid=` | `polymer/…/seasons_archives_list` |
| **搜索页** | `search.bilibili.com/all?keyword=` | `x/web-interface/wbi/search/type` |

未识别时会尝试从页面 DOM 扫 `a[href*="/video/BV"]`（兜底）。

### 能力

- 自动识别页面类型（badge 显示）
- **扫描当前页**：分页拉取列表（可设「最多页」、间隔 ms）
- 列表勾选 / 全选 / 全不选
- **下载 SRT / TXT**、复制全文、复制 BV 列表
- 批量中可 **停止**；限速默认 400ms
- Cookie：当前浏览器登录态（`GM_xmlhttpRequest`）

### 安装

1. [Tampermonkey](https://www.tampermonkey.net/)
2. 导入或粘贴 `bili-subbatch.user.js`（覆盖旧版即可，版本 **0.5.0**）
3. 打开上表任一页面 → 右下角 **CC** → 确认/切换模式 → **扫描当前页** → 勾选 → 下载

### UI

| 项 | 说明 |
|----|------|
| 主题 | [Catppuccin Mocha](https://catppuccin.com/palette/) userstyle 变量 |
| 穿透 | 根节点 `pointer-events: none`；FAB / 面板 / 贴边标签可点 |
| 拖拽 | 按住标题栏拖动；松手靠近左右边缘会**贴边收起** |
| 拉伸 | 拖面板四边/四角调整大小（最小约 300×280） |
| 贴边 | 标题栏「⧉」贴边 / 「—」收起；侧边 **字幕 CC** 标签展开；移出自动收 |
| 记忆 | 位置/大小/贴边写入 `localStorage`（`bili-subbatch-ui-v1`） |
| 模式 | 下拉：自动识别 / 六种强制类型 |

### 推荐用法

| 场景 | 操作 |
|------|------|
| 下一集视频字幕 | 视频页 → 扫描 → 下载（可展开分P） |
| UP 主页批量 | 个人主页 → 调大「最多页」→ 扫描 → 勾选 → SRT |
| 收藏夹 / 合集 | 打开对应页 → 扫描 → 批量 |
| 搜索结果 | 搜索页 → 扫描（按页上限）→ 批量 |
| 给 CLI 用 | 勾选后「复制 BV 列表」→ `main.py subtitle` |

### 与 CLI 分工

| | 油猴 | `python3 main.py subtitle` |
|--|------|------------------------------|
| 交互列表页 | ✅ | 需 catalog / 列表文件 |
| 多 UP 全量 + resume + 进 git | ❌ | ✅ |
| 登录 | 浏览器 | `BILI_COOKIE` |

### 自检

```bash
node userscripts/_wbi_check.mjs
```

### 注意

- 收藏夹 / 部分字幕需要**已登录**
- 大批量请提高间隔，避免风控
- 仅供个人学习研究；字幕权利归 UP / B 站
