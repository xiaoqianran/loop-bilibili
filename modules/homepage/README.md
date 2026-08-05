# homepage

B 站 **Web 推荐首页**（个性化 feed），**不依赖 opencli**。

- 协议：`GET /x/web-interface/wbi/index/top/feed/rcmd` + WBI
- 实现：`packages/bili_subbatch/homepage.py`
- 入口：`python3 main.py homepage ...`

与 `hot`（全站热门 `/popular`）不同：本模块是登录 Cookie 下的推荐流；`fresh_idx` 递增即「刷新」。

```bash
export BILI_COOKIE='SESSDATA=...; bili_jct=...; DedeUserID=...'
python3 main.py homepage --limit 20 --pages 2 --out data
python3 main.py subtitle --bvids data/homepage/bvids.txt -o data --resume
```
