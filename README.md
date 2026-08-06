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

### 自动抓取 → ModelScope → Pages（推荐日常）

```text
每天定时 (GitHub Actions)
  1. scrape_creator.py --from-config --push-ms   # 抓 B 站 → 本地 db → 推 ModelScope
  2. build_subtitle_site.py                      # 生成静态站
  3. 部署 gh-pages                               # https://xiaoqianran.github.io/loop-bilibili/
```

**Secrets（Settings → Secrets and variables → Actions）：**

| Secret | 用途 |
|--------|------|
| `MODELSCOPE_API_TOKEN` | 推送/拉取数据集（必填） |
| `BILI_COOKIE` | `SESSDATA=...; bili_jct=...; DedeUserID=...`（强烈建议，防 -352） |

**Pages 设置：** Deploy from a branch → **`gh-pages`** / **`/`**

**Workflows：**

| 名称 | 作用 |
|------|------|
| `Daily scrape + ModelScope + Pages` | 每天自动抓取+备份+上线（也可手动 Run） |
| `Deploy subtitle site to GitHub Pages` | 仅从 ModelScope 重建站点 |

**本地手动跑一整轮：**

```bash
# .env 里已有 MODELSCOPE_API_TOKEN；另 export BILI_COOKIE=...
export BILI_COOKIE='SESSDATA=...; bili_jct=...; DedeUserID=...'

# 抓 config.toml 里所有 creators 并推 ModelScope
PYTHONPATH=src python scripts/scrape_creator.py --from-config --push-ms

# 本地预览站点
python scripts/build_subtitle_site.py --from-dir data/v2 --out site --base-url /loop-bilibili
python -m http.server -d site 8080
```

站点：https://xiaoqianran.github.io/loop-bilibili/

## 文档

- v1 数据集说明仍在分支 v1：`docs/DATASET.md`
- 扩展路线（历史）：分支 v1 的 `docs/ROADMAP.md`

## 许可

见 [LICENSE](LICENSE)。
