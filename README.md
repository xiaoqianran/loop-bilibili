# loop-bilibili

B 站视频字幕摄取服务（**v2**）。

- **入口**：`src/loop_bilibili`（CLI：`init` / `once` / `worker` / `status`）
- **状态**：SQLite WAL（默认 `data/v2/loop.db`，**不进 git**）
- **字幕源**：HTTP/WBI（SubBatch 请求链）+ 可选 opencli 博主列表
- **首页推荐**：Web `feed/rcmd` + WBI
- **v1 旧架构**：见分支 [`v1`](https://github.com/xiaoqianran/loop-bilibili/tree/v1) / 标签 `v1-archive`（含旧 `main.py`、`data/subtitles/` 瘦归档）

## 架构

```text
发现视频 (博主 opencli / 首页 rcmd)
    → SQLite (videos, discoveries, jobs)
    → 字幕 worker (HTTP/WBI)
    → subtitles + analyze job (AI stub)
```

```text
loop-bilibili/
├── pyproject.toml
├── config.toml
├── src/loop_bilibili/
│   ├── cli.py
│   ├── database.py
│   ├── ingest.py
│   ├── worker.py
│   └── sources/
└── data/v2/                 # 运行库与导出（gitignore）
```

## 快速开始

```bash
export BILI_COOKIE='SESSDATA=...; bili_jct=...; DedeUserID=...'

# 开发
PYTHONPATH=src python3 -m loop_bilibili init
PYTHONPATH=src python3 -m loop_bilibili once
PYTHONPATH=src python3 -m loop_bilibili status

# 或安装
pip install -e .
loop-bilibili init
```

编辑 `config.toml`：`sources.creators`、`worker.job_delay`、`runtime.cookie` 等。

## 数据存在哪

| 内容 | 位置 | 云端 |
|------|------|------|
| v2 运行库 / 抓取结果 | `data/v2/*.db`、导出 txt（gitignore） | **ModelScope 私有数据集**（冷备份） |
| v1 瘦字幕归档 srt/txt | 仅分支 **`v1`** 的 `data/subtitles/` | GitHub 分支 `v1` |

### ModelScope 备份（抓完推送）

数据集：[`yuminghui/loop-bilibili-v2`](https://modelscope.cn/datasets/yuminghui/loop-bilibili-v2)（private）

```bash
# token 放在本地 .env（gitignore）或环境变量
# MODELSCOPE_API_TOKEN=ms-...

# 每次抓取完成后推送备份
python scripts/push_modelscope_v2.py --name haianyu
python scripts/push_modelscope_v2.py --name xiaolaoshi
```

### 两条线（分开）

**A. 本机/服务器抓取（0 Cookie 可用）**

```text
刷首页推荐 (访客 feed/rcmd)
  → 筛选（以后加）
  → 抓字幕
  → data/v2/*.db
  → 可选：scripts/push_modelscope_v2.py
```

```bash
# 0 Cookie 试首页（已实测可用）
PYTHONPATH=src python scripts/try_homepage_guest.py --pages 2 --max-jobs 8

# 正式一轮
PYTHONPATH=src python3 -m loop_bilibili once --max-jobs 30
```

**B. GitHub Actions = 只做可视化同步（不抓 B 站）**

```text
ModelScope 数据集 → 构建静态站 → gh-pages
```

- Secret 只需：`MODELSCOPE_API_TOKEN`
- **不需要** `BILI_COOKIE`
- 每天定时 / 手动 Run：`Sync ModelScope → Pages`
- 站点：https://xiaoqianran.github.io/loop-bilibili/
- Pages 设置：Deploy from a branch → **`gh-pages`** / **`/`**

## 文档

- v1 数据集说明仍在分支 v1：`docs/DATASET.md`
- 扩展路线（历史）：分支 v1 的 `docs/ROADMAP.md`

## 许可

见 [LICENSE](LICENSE)。
