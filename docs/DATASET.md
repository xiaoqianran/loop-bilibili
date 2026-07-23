# 字幕数据方案（单仓 loop-bilibili）

字幕抓取与归档 **全部落在本仓库**，不再拆 `loop-bilibili-subbatch` / 独立数据仓。

## 三层数据（别混）

| 路径 | 用途 | 是否进 git |
|------|------|------------|
| `catalogs/{mid}-{name}/` | 投稿目录元数据（all.json）；README 只有摘要 Top20 | **是** |
| `data/subtitle/{mid}-{name}/` | 抓取工作区（results / items / srt / done） | **否**（体积大、含完整 cue JSON） |
| `data/subtitles/ups/{mid}-{name}/` | 瘦归档 + **README 全量导航**（标题↔txt 预览↔srt） | **是** |

**人类读字幕：打开 `data/subtitles/ups/{slug}/README.md`，不要只翻 srt 文件名。**

```text
抓取                              打包
data/subtitle/UID-name/  ──pack-subtitles──►  data/subtitles/ups/UID-name/
  items/*.json (肥)                           README.md  ← 全量有序 + 预览 + 链接
  results.json (肥)                           srt/  txt/  index.jsonl  meta.json
  srt/*.srt
```

只重建导航：

```bash
python3 main.py rebuild-hubs --archive data/subtitles --catalogs catalogs
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
