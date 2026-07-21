# loop-bilibili

给一个 **B 站 UP 主名字 / UID / 空间链接**，自动拉取该 UP **全部投稿视频**，并按 **系列**（标题里的 `【…】` 标签）导出完整目录。

数据来源：[opencli](https://github.com/jackwener/OpenCLI) 的 `bilibili` adapter（`user-videos`）。

## 依赖

- Python 3.10+
- 已安装并可运行的 `opencli`（且 bilibili adapter 可用）

```bash
# 确认 opencli 可用
opencli bilibili user-videos --help
```

## 用法

```bash
# UID
python3 scripts/export_up.py 2071007724 --name 海安雨

# 空间链接
python3 scripts/export_up.py 'https://space.bilibili.com/2071007724' --name 海安雨

# UP 名（若 opencli 能解析）
python3 scripts/export_up.py 海安雨
```

可选参数：

| 参数 | 说明 | 默认 |
|------|------|------|
| `--name` | 显示名（UID/链接时建议带上） | 与 target 相同 |
| `--out` | 导出根目录 | `catalogs` |

## 输出结构

```text
catalogs/
  {uid}-{name}/
    README.md          # 总目录：系列一览 / 最新 / 播放 Top
    meta.json          # 元信息
    all.json           # 全量视频列表
    by_series.json     # 按系列分组
    series/
      每天一个宝藏问题.md
      每天一个宝藏论文.md
      ...
```

系列识别规则：从标题中的 `【标签】` 提取；优先含「宝藏 / 系列 / 合集 / 每天 / 每周」等词的标签；没有标签则归入「未分类 / 其他」。

## 示例目录

已导出示例：

- [海安雨（2071007724）](catalogs/2071007724-海安雨/README.md)

```bash
python3 scripts/export_up.py 2071007724 --name 海安雨
```

## 相关 opencli 命令

```bash
opencli bilibili user-videos <uid> --limit 50 --page 1 --order pubdate -f json
opencli bilibili search "关键词" --limit 10 -f json
opencli bilibili video <bvid>
opencli bilibili summary <bvid>
opencli bilibili subtitle <bvid>
```

## License

MIT
