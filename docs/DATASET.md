# 字幕数据方案（单仓 loop-bilibili）

字幕抓取与归档 **全部落在本仓库**，不再拆 `loop-bilibili-subbatch` / 独立数据仓。

## 两层数据

| 路径 | 用途 | 是否进 git |
|------|------|------------|
| `data/subtitle/{mid}-{name}/` | 抓取工作区（results / items / srt / done） | **否**（体积大、含完整 cue JSON） |
| `data/subtitles/` | 瘦归档（srt + txt + index.jsonl） | **是**（可 clone / 检索） |

```text
抓取                              打包
data/subtitle/UID-name/  ──pack-subtitles──►  data/subtitles/ups/UID-name/
  items/*.json (肥)                           srt/  txt/  index.jsonl
  results.json (肥)                           meta.json
  srt/*.srt
```

## 抓取（现行 SubBatch）

```bash
export BILI_COOKIE='SESSDATA=...'   # 推荐
python3 main.py catalog 280780745 --name 张小珺商业访谈录
python3 main.py subtitle --catalog catalogs/280780745-张小珺商业访谈录 -o data --resume
```

实现：`packages/bili_subbatch` + `modules/subtitle/`。

## 打包进仓

```bash
python3 main.py pack-subtitles \
  --src-root data/subtitle \
  --catalogs catalogs \
  -o data/subtitles \
  --skip-empty-up
```

然后 `git add data/subtitles catalogs && git commit && git push`。

## 过世方案

opencli 字幕：`legacy/opencli/`（勿用于生产）。

## 合规

见 `data/subtitles/NOTICE`：学习研究用途；权利归 UP / 平台。
