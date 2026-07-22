# modules/subtitle — 字幕批量导出

默认 **conservative**：`item_delay=2.0±0.5s`，**串行**（不并发）。  
（曾用 5s → 3s → 2s；实跑无真实限流后温和提速。）

```bash
python3 main.py subtitle --bvid BV1BVEs6LENZ
python3 main.py subtitle --catalog catalogs/2071007724-海安雨 --limit 2 --resume

# 需要更慢（更稳）
python3 main.py subtitle --catalog catalogs/... --item-delay 5 --item-jitter 1.5
```
