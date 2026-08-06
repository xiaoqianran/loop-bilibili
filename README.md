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

### 产品路径（不强制 Cookie）

```text
常驻服务（本机/服务器）
  定时刷首页推荐 (feed/rcmd，访客可用)
       → 规则筛选（以后：标签/关键词/时长…）
       → 入队抓字幕 (HTTP/WBI，串行限速)
       → SQLite
       → 可选 push ModelScope → Pages 展示
```

**Cookie 不是必须的。**  
空 Cookie = 访客态；接口照样能用。Cookie 只在你想要「登录后的个性化首页」或降低云机房 IP 风控时才有用。

**真正必填的云端 Secret（若要备份/Pages）：** 只有 `MODELSCOPE_API_TOKEN`。

**Pages：** Deploy from a branch → `gh-pages` / `/`  
站点：https://xiaoqianran.github.io/loop-bilibili/

**本地：**

```bash
# 刷一轮首页并处理字幕队列（无需 Cookie）
PYTHONPATH=src python3 -m loop_bilibili once --max-jobs 30

# 可选：某博主全量（脚本，非主路径）
PYTHONPATH=src python scripts/scrape_creator.py --mid 2071007724 --name 海安雨 --slug haianyu --push-ms
```

## 文档

- v1 数据集说明仍在分支 v1：`docs/DATASET.md`
- 扩展路线（历史）：分支 v1 的 `docs/ROADMAP.md`

## 许可

见 [LICENSE](LICENSE)。
