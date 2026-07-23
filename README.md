# loop-bilibili

B 站自动化与数据采集（**单仓**）。根目录 **`main.py`**；公共库在 **`packages/`**；业务在 **`modules/`**。

- **字幕（现行）**：`packages/bili_subbatch` — SubBatch HTTP/WBI，**不依赖 opencli**
- **列表 / 总结 / 评论**：仍走 [opencli](https://github.com/jackwener/OpenCLI) `bilibili` adapter
- **过世方案**：`legacy/opencli/`（旧 opencli 字幕）

## 架构

```text
loop-bilibili/
├── main.py
├── packages/
│   ├── loop_core/          # opencli 运行器、限速、进度（列表/总结/评论）
│   └── bili_subbatch/      # 字幕 HTTP 客户端 + pack
├── modules/
│   ├── catalog/ discover/ feed/ summary/ comments/
│   └── subtitle/           # → bili_subbatch
├── legacy/opencli/         # 过世：opencli 字幕
├── catalogs/               # UP 投稿目录（可提交）
├── data/
│   ├── subtitle/           # 抓取工作区（gitignore）
│   └── subtitles/          # 瘦归档 srt+txt+index（进 git）
└── docs/DATASET.md
```

## 快速开始

```bash
# 依赖：列表类仍需 opencli；字幕仅需 Python 3.10+
opencli bilibili --help

python3 main.py modules
python3 main.py catalog 2071007724 --name 海安雨

# 字幕（SubBatch，推荐 Cookie）
export BILI_COOKIE='SESSDATA=...; bili_jct=...; DedeUserID=...'
python3 main.py subtitle --catalog catalogs/2071007724-海安雨 -o data --resume

# 打包瘦数据并准备提交
python3 main.py pack-subtitles --src-root data/subtitle -o data/subtitles --skip-empty-up
```

## 模块

| 模块 | 后端 | 说明 |
|------|------|------|
| catalog / hot / ranking / search / feed | opencli | 列表类，page_delay≈1.5s |
| summary / comments | opencli | item 串行，默认 ~2s |
| **subtitle** | **bili_subbatch** | SubBatch，默认 ~0.4–0.5s |
| pack-subtitles | bili_subbatch.pack | 工作区 → `data/subtitles` |

## 字幕数据

**两条入口都行；一般先看目录，再下钻字幕：**

| 去哪 | 链接 | 适合 |
|------|------|------|
| **★ 按 UP 从目录进（catalog）** | **[catalogs/](catalogs/)** → 各 `catalogs/{mid}-名字/README.md` | **先看**：官方合集/系列 + 每条 **txt/srt** 链接 + 顶栏字幕导航 |
| **★ 按 UP 读字幕（归档总表）** | **[data/subtitles/README.md](data/subtitles/README.md)** → `ups/{mid}-名字/README.md` | **再读**：全量有序 + **txt 预览** + srt/txt |
| 方案说明 | [docs/DATASET.md](docs/DATASET.md) | 三层数据含义 |
| 扩展路线 | [docs/ROADMAP.md](docs/ROADMAP.md) | pipeline / LLM / Actions |
| 版权说明 | [data/subtitles/NOTICE](data/subtitles/NOTICE) | 非官方、学习研究用 |

**同一 UP 双入口示例（北歌AIGC）：**

1. catalog 目录：[catalogs/3546588152597451-北歌AIGC/README.md](catalogs/3546588152597451-北歌AIGC/README.md)
2. 字幕 hub：[data/subtitles/ups/3546588152597451-北歌AIGC/README.md](data/subtitles/ups/3546588152597451-北歌AIGC/README.md)

```bash
# 本地：先目录，后字幕
open catalogs/
open data/subtitles/README.md
```

## 限速

| 场景 | 默认 |
|------|------|
| 列表 page_delay | 1.5±0.5s（conservative） |
| 字幕 delay | profile 映射 ≈0.5s（非 opencli 的 2s） |
| 总结/评论 item_delay | ~2s |

字幕可用 `--item-delay 0.3` 覆盖；**仅串行**。

## 过世方案

`legacy/opencli/` — 旧 `bilibili subtitle` 路径，含假阳性 429、偏慢等问题，**不要再用**。

## License

MIT（工具代码）。字幕文本权利归原 UP / B 站，见 `data/subtitles/NOTICE`。
