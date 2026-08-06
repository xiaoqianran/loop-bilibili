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

### GitHub Pages 字幕可视化

静态站由 Actions 从 ModelScope 拉快照 → 构建 → 部署：

1. 仓库 **Settings → Secrets** 添加 `MODELSCOPE_API_TOKEN`
2. **Settings → Pages** → Source 选 **GitHub Actions**
3. 运行 workflow：**Actions → Deploy subtitle site to GitHub Pages → Run workflow**

本地预览：

```bash
python scripts/build_subtitle_site.py --from-dir data/v2 --out site
python -m http.server -d site 8080
# 浏览器打开 http://127.0.0.1:8080/
```

站点地址（开启后）：`https://xiaoqianran.github.io/loop-bilibili/`

## 文档

- v1 数据集说明仍在分支 v1：`docs/DATASET.md`
- 扩展路线（历史）：分支 v1 的 `docs/ROADMAP.md`

## 许可

见 [LICENSE](LICENSE)。
