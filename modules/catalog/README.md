# modules/catalog — UP 投稿目录导出

按系列分组导出某 UP 的**全部投稿**目录（Markdown + JSON + CSV）。

## opencli

```bash
opencli bilibili user-videos <uid> --limit 30 --page 1 --order pubdate -f json
```

## 行为

- 串行分页拉取，默认 **conservative** 限速（约 1.5s/页、30 条/页）
- **合集和系列**：拉 B 站空间 Tab「合集和系列」
  （`seasons_series_list` + 合集/系列 archives），写入 `collections.json`
  与 `series/*.md`（不再用标题 `【】` 猜合集）
- 失败退避 / 412 冷却 / `.progress.json` 断点续跑
- 输出到 `catalogs/{uid}-{name}/`

## 运行

```bash
# 根入口（推荐）
python3 main.py catalog 2071007724 --name 海安雨
python3 main.py catalog 2071007724 --name 海安雨 --resume
python3 main.py catalog --rebuild catalogs/2071007724-海安雨

# 兼容旧脚本
python3 scripts/export_up.py 2071007724 --name 海安雨
```

## 模块文件

| 文件 | 职责 |
|------|------|
| `models.py` | 视频规范化、系列识别 |
| `collector.py` | opencli 拉取 + 限速重试 |
| `writer.py` | md / json / csv 写出 |
| `export.py` | 导出与 rebuild 编排 |
