# modules/summary — 官方 AI 总结

```bash
opencli bilibili summary BV1xxx -f json
```

默认 **item_delay ~5s**（conservative），串行 + resume。

```bash
python3 main.py summary --bvid BV1BVEs6LENZ
python3 main.py summary --catalog catalogs/2071007724-海安雨 --limit 3 --resume
```
