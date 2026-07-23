# subtitle（现行：SubBatch HTTP）

不依赖 opencli。协议与 Chrome 扩展 SubBatch 一致，实现在 `packages/bili_subbatch/`。

```bash
# 单 UP catalog 后批量字幕
python3 main.py catalog 280780745 --name 张小珺商业访谈录
python3 main.py subtitle --catalog catalogs/280780745-张小珺商业访谈录 -o data --resume

# Cookie（推荐，AI 字幕更稳）
export BILI_COOKIE='SESSDATA=...; bili_jct=...; DedeUserID=...'
python3 main.py subtitle --bvid BV1xxx -o data --cookie "$BILI_COOKIE"
```

输出：`data/subtitle/{slug}/`（results / done / items / srt）。

打包进仓瘦数据：

```bash
python3 main.py pack-subtitles --src-root data/subtitle -o data/subtitles
```

## 过世方案

opencli 字幕见 `legacy/opencli/`。
