# modules/discover — 热门 / 排行 / 搜索

## opencli

```bash
opencli bilibili hot --limit 10 -f json
opencli bilibili ranking -f json
opencli bilibili search "关键词" --limit 10 -f json
```

## 限速

列表级 **conservative**（页/次间隔 ~1.5s 类），串行。

## 运行

```bash
python3 main.py hot --limit 5
python3 main.py ranking --limit 5
python3 main.py search "AI" --limit 5
```
