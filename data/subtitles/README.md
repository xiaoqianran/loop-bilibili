# data/subtitles — packed subtitle archive

B 站 UP 字幕瘦归档，由 **loop-bilibili** `main.py pack-subtitles` 生成（SubBatch HTTP）。

> **非官方**。字幕多为平台 AI/CC 轨；版权归原 UP / B 站。仅供个人学习、检索与研究。

完整方案见仓库 `docs/DATASET.md`。原始抓取工作区在 `data/subtitle/`（可 gitignore 大 JSON）。

## 布局

```text
ups/{mid}-{name}/
  meta.json       # UP 统计
  index.jsonl     # 每行一个视频的瘦元数据（无 cue 正文）
  srt/{bvid}.srt  # 带时间轴字幕
  txt/{bvid}.txt  # 纯文本（便于 RAG / 全文检索）
dataset.json      # 全局清单
```

## 已收录

| slug | ok | empty | cues | srt |
|------|----|-------|------|-----|
| `1223644844-杜子源源` | 67 | 1 | 18780 | 1.2 MB |
| `2071007724-海安雨` | 276 | 5 | 35900 | 2.4 MB |
| `280780745-张小珺商业访谈录` | 23 | 8 | 93487 | 6.6 MB |
| `291215958-谦行AIing` | 97 | 6 | 50738 | 3.4 MB |
| `325864133-飞天闪客` | 62 | 8 | 16002 | 1.1 MB |
| `341376543-堂吉诃德拉曼查的英豪` | 7 | 0 | 7252 | 0.5 MB |
| `3493124676520059-程序员扣丁` | 22 | 0 | 2665 | 0.2 MB |
| `3546804396230870-码里奥Ziho` | 70 | 0 | 14332 | 1.0 MB |
| `3546957802899978-日新月异max` | 4 | 0 | 1528 | 0.1 MB |
| `404186354-callwhl` | 8 | 1 | 2375 | 0.2 MB |
| `499725296-AI扫地曾` | 141 | 3 | 22956 | 1.5 MB |

共 **11** 个 UP，打包时间见 `dataset.json`。

## 重新打包

```bash
python3 main.py pack-subtitles \
  --src-root data/subtitle \
  --catalogs catalogs \
  -o data/subtitles
```
