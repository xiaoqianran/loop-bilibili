# loop-bilibili

B 站视频字幕摄取服务（**v2**）：按用户兴趣筛选首页推荐，限速抓字幕，SQLite 持久化；云端只做 Hugging Face 备份与 GitHub Pages 可视化。

| 能力 | 说明 |
|------|------|
| CLI | `init` / `once` / **`run`** / `worker` / `status` |
| 运行时库 | `data/v2/loop.db`（WAL，**gitignore**） |
| 首页发现 | Web `feed/rcmd` + WBI；有 Cookie = **账号个性化** |
| 偏好筛选 | `preferences.toml`：keywords + related + soft n-gram，`must_not` 硬拦 |
| 字幕 | HTTP/WBI 串行限速 + 风控指数退避 |
| Cookie | 推荐 **opencli** 从已登录浏览器导出 → `.env` |
| 云端 | HF 私有数据集冷备份；Actions 只做 HF → Pages（**不抓 B 站**） |
| v1 归档 | 分支 [`v1`](https://github.com/xiaoqianran/loop-bilibili/tree/v1) / 标签 `v1-archive` |

## 架构

```text
opencli 已登录 Chrome
    → scripts/sync_cookie_from_opencli.py → .env (BILI_COOKIE)
    → loop-bilibili run / once
         → 首页 rcmd（带 Cookie 个性化）
         → preference 打分
         → videos + discoveries（全量元数据）
         → jobs（仅 selected，enqueue_once 去重）
         → 限速字幕 worker
         → subtitles

可选：scripts/push_hf_v2.py
    → Hugging Face seachen/loop-bilibili-v2
    → GitHub Actions 构建 Pages
```

```text
loop-bilibili/
├── config.toml              # 节奏 / worker 参数（勿写 Cookie）
├── preferences.toml         # 兴趣词与阈值
├── .env                     # BILI_COOKIE / HF_TOKEN（gitignore）
├── src/loop_bilibili/
│   ├── cli.py               # init / once / run / worker / status
│   ├── cadence.py           # 长跑：按 interval 循环发现 + 干活
│   ├── ingest.py            # 发现 + 偏好入队
│   ├── preference/          # 打分器
│   ├── worker.py            # 字幕任务与退避
│   └── sources/             # homepage_rcmd / subtitle HTTP
├── scripts/
│   ├── sync_cookie_from_opencli.py
│   ├── set_bili_cookie.py
│   ├── push_hf_v2.py / fetch_hf_v2.py
│   └── build_subtitle_site.py
└── data/v2/                 # 运行库（gitignore）
```

## 快速开始

### 1. 依赖

```bash
cd loop-bilibili
# Python 3.10+
pip install -e .          # 或: PYTHONPATH=src python3 -m loop_bilibili ...
```

个性化首页还需要本机：

- [opencli](https://github.com/jackwener/OpenCLI) 已安装
- `opencli doctor` 通过（Daemon + Chrome 扩展已连接）
- 浏览器已登录 bilibili.com（`opencli bilibili whoami` 能看到账号）

### 2. Cookie（个性化，推荐）

Cookie **不要写进 `config.toml` / 不要提交 git**。放在 gitignore 的 `.env`：

```bash
# 一键：从 opencli 浏览器会话导出 SESSDATA 等 → 写入 .env
python scripts/sync_cookie_from_opencli.py --test

# 或手动粘贴
python scripts/set_bili_cookie.py 'SESSDATA=...; bili_jct=...; DedeUserID=...' --test

# 仅检查是否已配置
python scripts/set_bili_cookie.py --check
```

底层 opencli 命令（需本机已装用户适配器 `export-cookie`，或直接用仓库脚本同步）：

```bash
opencli bilibili whoami
opencli bilibili export-cookie -f json   # 若已注册该子命令
```

启动后应看到：

```text
homepage: personalized mode (SESSDATA present — rcmd uses your account feed)
```

无 Cookie 时仍可跑**访客**首页，但不是账号个性化推荐。

可选强制登录：

```toml
# config.toml
[runtime]
require_cookie = true
```

### 3. 兴趣与节奏

- `preferences.toml`：兴趣 `keywords` / 扩展 `related`、`threshold`、`must_not`
- `config.toml`：
  - `sources.homepage_interval_s`：长跑两次刷首页的间隔（默认 600s）
  - `sources.homepage_pages` / `homepage_ps`：每轮拉取量
  - `worker.job_delay` / `risk_*`：字幕限速与风控退避
  - `worker.jobs_per_cycle`：每发现周期最多处理多少任务

### 4. 跑起来

```bash
PYTHONPATH=src python3 -m loop_bilibili init

# 单轮：刷首页 + 偏好入队 + 处理最多 N 个字幕任务
PYTHONPATH=src python3 -m loop_bilibili once --max-jobs 30

# 长跑（推荐挂机）：按 homepage_interval_s 循环
PYTHONPATH=src python3 -m loop_bilibili run

# 有界试跑（2 个发现周期，间隔 0）
PYTHONPATH=src python3 -m loop_bilibili run --max-cycles 2 --homepage-interval 0

# 只消费队列、不再刷首页
PYTHONPATH=src python3 -m loop_bilibili worker --max-idle 3

PYTHONPATH=src python3 -m loop_bilibili status
```

安装后也可用：

```bash
pip install -e .
loop-bilibili run
```

## 数据与存储

| 内容 | 位置 | 云端 |
|------|------|------|
| 运行时 SoT | `data/v2/loop.db`（多 UP 混流 + jobs） | 可选推 HF |
| 按人归档库 | `data/v2/<slug>.db`（`scrape_creator`） | HF snapshot |
| 密钥 | `.env`（`BILI_COOKIE` / `HF_TOKEN`） | **禁止进 git** |
| v1 瘦字幕 | 仅分支 `v1` 的 `data/subtitles/` | GitHub `v1` |

### Hugging Face 备份

数据集：[`seachen/loop-bilibili-v2`](https://huggingface.co/datasets/seachen/loop-bilibili-v2)（private）

```bash
# .env
# HF_TOKEN=hf_...
# HF_DATASET_REPO=seachen/loop-bilibili-v2

python scripts/push_hf_v2.py --name haianyu
python scripts/push_hf_v2.py --all
```

### 两条线（务必分开）

**A. 本机抓取（可带 Cookie 个性化）**

```text
opencli Cookie → 首页 rcmd → preference → loop.db 字幕
  → 可选 push_hf_v2
```

**B. GitHub Actions = 只可视化（不抓 B 站）**

```text
HF 数据集 → build_subtitle_site → Actions artifact → GitHub Pages
```

- Secret：`HF_TOKEN`（**不需要** `BILI_COOKIE`）
- Workflow：`Deploy subtitle site to GitHub Pages` / `Sync HF → Pages`
- 站点：https://xiaoqianran.github.io/loop-bilibili/
- Pages 源：**GitHub Actions**（不要用 Deploy from branch `gh-pages`）

## 命令对照

| 命令 | 作用 |
|------|------|
| `init` | 建 SQLite schema |
| `once` | 发现一轮 + 处理一批 jobs |
| `run` | 长跑 cadence（间隔刷首页 + 干活） |
| `worker` | 只处理 jobs，不发现 |
| `status` | 视频/任务/字幕计数 |
| `scripts/sync_cookie_from_opencli.py` | opencli → `.env` |
| `scripts/push_hf_v2.py` | 本地 db → HF |
| `scripts/build_subtitle_site.py` | 本地建静态站（CI 同款） |

## 测试

```bash
PYTHONPATH=src python -m unittest tests.test_preference tests.test_cadence tests.test_workers tests.test_cli -v
```

## 文档

- v1 数据集说明：分支 v1 的 `docs/DATASET.md`
- 扩展路线（历史）：分支 v1 的 `docs/ROADMAP.md`

## 许可

见 [LICENSE](LICENSE)。
