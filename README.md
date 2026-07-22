# loop-bilibili

B 站自动化与数据采集（**模块化**）。根目录 **`main.py`** 统一入口；公共能力在 **`packages/loop_core`**；业务在 **`modules/*`**。

对齐：[loop-zhihu](https://github.com/xiaoqianran/loop-zhihu)。数据经 [opencli](https://github.com/jackwener/OpenCLI) `bilibili` adapter。

**默认防风控**：列表类 `page_delay≈1.5s`；逐视频类 `item_delay≈3s`（温和提速后的默认，仍 **serial** + jitter + 退避，**不并发**）。写操作（发评/关注）不做成模块。

## 架构

```text
loop-bilibili/
├── main.py
├── config.example.yaml
├── catalogs/                 # catalog 导出
├── data/                     # hot/ranking/feed/summary/... 运行输出（gitignore 可选）
├── packages/loop_core/       # opencli、限速、batch resume
├── modules/
│   ├── catalog/              # ✅ user-videos 全量目录
│   ├── discover/             # ✅ hot / ranking / search
│   ├── feed/                 # ✅ feed 动态
│   ├── summary/              # ✅ AI 总结（item 限速 + resume）
│   ├── subtitle/             # ✅ 字幕
│   ├── comments/             # ✅ 评论
│   └── item_batch/           # 共享串行批处理
└── tests/
```

## 快速开始

```bash
opencli bilibili --help
python3 main.py modules
python3 main.py status

# 列表 / 发现（conservative）
python3 main.py hot --limit 5
python3 main.py ranking --limit 5
python3 main.py search "AI" --limit 5
python3 main.py feed --limit 5

# UP 目录
python3 main.py catalog 2071007724 --name 海安雨

# 逐视频（更严 item_delay，支持 --catalog + --resume）
python3 main.py summary --bvid BV1BVEs6LENZ
python3 main.py subtitle --catalog catalogs/2071007724-海安雨 --limit 3
python3 main.py comments --bvid BV1BVEs6LENZ --comment-limit 10
```

## 模块状态

| 模块 | 状态 | opencli | 限速类型 |
|------|------|---------|----------|
| catalog | ✅ | user-videos | list |
| hot | ✅ | hot | list |
| ranking | ✅ | ranking | list |
| search | ✅ | search | list |
| feed | ✅ | feed | list |
| summary | ✅ | summary | **item** (~3s 默认) |
| subtitle | ✅ | subtitle | **item** (~3s 默认) |
| comments | ✅ | comments | **item** (~3s 默认) |

非目标（不实现为归档模块）：`comment` 发帖、`follow`/`unfollow` 等写操作；大体积 `download` 为可选扩展。

## 限速 profile

| profile | page_delay | item_delay | 用途 |
|---------|------------|------------|------|
| **conservative**（默认） | 1.5±0.5s | **3.0±1.0s** | 日常 / 温和提速 |
| balanced | 1.0±0.3s | 2.0±0.5s | 再快一点 |
| aggressive | 0.3±0.1s | 1.2±0.3s | 调试（易风控） |

字幕/总结/评论 **仅串行**，不通过提高并发提速。需要更慢可 `--item-delay 5`。

批量 summary/subtitle/comments：`done.json` + `results.json` + `items/*.json`，默认 resume。

## 关于限速与风控（摘要）

投稿列表/发现串行分页；逐视频操作间隔必须 **严于** 列表。识别 `-799` / `-412` / `-352` 等信号并退避。B 站无公开 QPS SLA；策略为社区实践下的保守默认。

检索渠道：错误码镜像、bilibili-api FAQ、分页 sleep 实践、CSDN/掘金/知乎讨论、RSSHub 412 issue、开放平台/小程序频率说明、合规动态。

## 测试

```bash
python3 -m unittest tests.test_batch_and_profiles -v
```

## License

MIT
