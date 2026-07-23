# 过世方案：opencli 字幕抓取

本目录保留 **旧版 opencli `bilibili subtitle` 逐条抓字幕** 实现，仅作历史对照。

## 为何退役

| 问题 | 表现 |
|------|------|
| 假阳性风控 | 字幕时间戳 `429.xs` 被误判 rate-limit |
| 慢 | 每条走 opencli 子进程 + 默认 item_delay≈2s |
| empty 偏多 | 游客/无 Cookie 时 AI 字幕轨经常拿不到 |
| 与扩展脱节 | 未复刻 SubBatch 的 WBI + player/dm 回退链 |

## 现行方案（请用这个）

根目录：

```bash
python3 main.py subtitle --catalog catalogs/UID-name -o data --resume
```

实现：`packages/bili_subbatch` + `modules/subtitle/`（HTTP + WBI，默认 ~0.4s 间隔）。

归档瘦数据：

```bash
python3 main.py pack-subtitles \
  --src-root data/subtitle \
  -o data/subtitles
```

## 旧入口（勿用于生产）

```python
from legacy.opencli.subtitle_export import export_subtitles_opencli
```

需要本机已安装并可登录的 [opencli](https://github.com/jackwener/OpenCLI)。

## 其它模块说明

`catalog` / `hot` / `ranking` / `search` / `feed` / `summary` / `comments` **仍经 opencli**（列表与官方 AI 总结等尚未 HTTP 化）。  
**仅字幕**从 opencli 迁出；本目录只归档字幕相关旧代码。
