# data/subtitles — packed subtitle archive

B 站 UP 字幕瘦归档，由 **loop-bilibili** `main.py pack-subtitles` 生成（SubBatch HTTP）。

> **非官方**。字幕多为平台 AI/CC 轨；版权归原 UP / B 站。仅供个人学习、检索与研究。

**怎么读：** 不要只看 `srt/` 文件名。每个 UP 目录里的 **[README.md](ups/)** 是给人用的总索引——
按 catalog 顺序列出**全部**视频，带播放链接、字幕状态、`txt` 预览，以及相对路径的 srt/txt。

完整方案见仓库 `docs/DATASET.md`。原始抓取工作区在 `data/subtitle/`（可 gitignore 大 JSON）。

## 布局

```text
ups/{mid}-{name}/
  README.md       # ★ 人类导航：全量有序列表 + txt 预览 + srt/txt 链接
  meta.json       # UP 统计
  index.jsonl     # 每行一个视频的瘦元数据（无 cue 正文）
  srt/{bvid}.srt  # 带时间轴字幕
  txt/{bvid}.txt  # 纯文本（便于 RAG / 全文检索）
dataset.json      # 全局清单
```

## 已收录 UP（点进 slug 看 README）

| UP | 字幕 ok | empty | cues | srt | 导航 |
|----|---------|-------|------|-----|------|
| 杜子源源 | 67 | 1 | 18780 | 1.2 MB | [README](ups/1223644844-杜子源源/README.md) |
| 赵扶风-1942型 | 32 | 39 | 8677 | 0.5 MB | [README](ups/1595797-赵扶风-1942型/README.md) |
| 原子能 | 0 | 84 | 0 | 0.0 MB | [README](ups/162183-原子能/README.md) |
| 海安雨 | 276 | 5 | 35900 | 2.4 MB | [README](ups/2071007724-海安雨/README.md) |
| 张小珺商业访谈录 | 23 | 8 | 93487 | 6.6 MB | [README](ups/280780745-张小珺商业访谈录/README.md) |
| 谦行AIing | 97 | 6 | 50738 | 3.4 MB | [README](ups/291215958-谦行AIing/README.md) |
| 飞天闪客 | 62 | 8 | 16002 | 1.1 MB | [README](ups/325864133-飞天闪客/README.md) |
| 堂吉诃德拉曼查的英豪 | 7 | 0 | 7252 | 0.5 MB | [README](ups/341376543-堂吉诃德拉曼查的英豪/README.md) |
| 程序员扣丁 | 22 | 0 | 2665 | 0.2 MB | [README](ups/3493124676520059-程序员扣丁/README.md) |
| 北歌AIGC | 40 | 0 | 17961 | 1.2 MB | [README](ups/3546588152597451-北歌AIGC/README.md) |
| 抽象狗哥 | 133 | 12 | 16275 | 1.1 MB | [README](ups/3546739107695411-抽象狗哥/README.md) |
| 码里奥Ziho | 70 | 0 | 14332 | 1.0 MB | [README](ups/3546804396230870-码里奥Ziho/README.md) |
| 日新月异max | 4 | 0 | 1528 | 0.1 MB | [README](ups/3546957802899978-日新月异max/README.md) |
| callwhl | 8 | 1 | 2375 | 0.2 MB | [README](ups/404186354-callwhl/README.md) |
| AI扫地曾 | 141 | 3 | 22956 | 1.5 MB | [README](ups/499725296-AI扫地曾/README.md) |
| 明文传输不 | 0 | 46 | 0 | 0.0 MB | [README](ups/61214429-明文传输不/README.md) |
| 随意Official | 53 | 6 | 7902 | 0.6 MB | [README](ups/79356601-随意Official/README.md) |

共 **17** 个 UP，打包时间见 `dataset.json`。

## 重新打包 / 只重建导航

```bash
python3 main.py pack-subtitles \
  --src-root data/subtitle \
  --catalogs catalogs \
  -o data/subtitles

# 不重新 pack 文件，只重写各 UP 的 README.md
python3 main.py rebuild-hubs --archive data/subtitles --catalogs catalogs
```
