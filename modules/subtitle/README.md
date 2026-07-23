# subtitle（现行：Pipeline + SubBatch HTTP）

不依赖 opencli。实现：`packages/bili_subbatch/`（client + **pipeline + processors**）。

```bash
python3 main.py catalog 291215958 --name 谦行AIing
export BILI_COOKIE='SESSDATA=...; bili_jct=...; DedeUserID=...'
python3 main.py subtitle --catalog catalogs/291215958-谦行AIing -o data --resume
```

抓取时默认 processor 链（**实时落盘**）：

1. `normalize_cues` — 去空行、压空白  
2. `write_srt` — `srt/{bvid}.srt`  
3. `write_txt` — `txt/{bvid}.txt`（RAG / 未来 LLM）  
4. `index_jsonl` — 追加 `index.jsonl` 瘦索引  

输出目录：`data/subtitle/{slug}/`（results / done / items / srt / txt / index.jsonl）。

扩展：实现 `SubtitleProcessor`，经 `export_subtitles(..., processors=[...])` 注入。  
路线图：`docs/ROADMAP.md`（LLM、GitHub Actions、离线 reprocess）。

打包进仓：

```bash
python3 main.py pack-subtitles --src-root data/subtitle -o data/subtitles
```

## 过世方案

opencli 字幕见 `legacy/opencli/`。
