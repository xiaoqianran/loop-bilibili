# loop-bilibili

B 站自动化与数据采集（**模块化**）。根目录 **`main.py`** 统一入口；公共能力在 **`packages/loop_core`**；业务在 **`modules/*`**。

对齐参考仓库：[loop-zhihu](https://github.com/xiaoqianran/loop-zhihu) 的分层方式。

数据经 [opencli](https://github.com/jackwener/OpenCLI) 的 `bilibili` adapter 获取。

## 架构

```text
loop-bilibili/
├── main.py                 # CLI 入口
├── config.example.yaml     # 配置模板
├── catalogs/               # 导出结果（示例数据可入库）
├── packages/
│   └── loop_core/          # opencli 运行器、限速、进度、错误分类
└── modules/
    └── catalog/            # ✅ UP 投稿全量目录（按系列）
    # summary / subtitle    # 规划中
```

| 层 | 职责 |
|----|------|
| `loop_core` | 与业务无关的流水线零件（限速、opencli、断点） |
| `modules/*` | 领域模型、采集、写出规则 |
| `main.py` | 子命令调度 |

```bash
python3 main.py modules   # 查看模块与 opencli 对应
python3 main.py status    # 本地 catalogs 状态
```

## 依赖

- Python 3.10+（仅标准库）
- 可用的 `opencli` + bilibili adapter

```bash
opencli bilibili user-videos --help
```

## 使用

### 导出 UP 完整目录（catalog）

```bash
# UID + 显示名（推荐）
python3 main.py catalog 2071007724 --name 海安雨

# 空间链接
python3 main.py catalog 'https://space.bilibili.com/2071007724' --name 海安雨

# 中断后续跑
python3 main.py catalog 2071007724 --name 海安雨 --resume

# 离线：用已有 all.json 重生成 md/csv
python3 main.py catalog --rebuild catalogs/2071007724-海安雨

# 兼容旧入口
python3 scripts/export_up.py 2071007724 --name 海安雨
```

### catalog 参数

| 参数 | 说明 | 默认 |
|------|------|------|
| `target` | UP 名 / UID / 空间链接 | 导出时必填 |
| `--name` | 显示名 | 与 target 相同 |
| `--out` | 输出根目录 | `catalogs` |
| `--profile` | `conservative` / `balanced` / `aggressive` | `conservative` |
| `--page-size` / `--delay` / `--jitter` | 覆盖 profile | — |
| `--retries` / `--max-pages` / `--cooldown-412` | 重试与页上限 | — |
| `--order` | `pubdate` / `click` / `stow` | `pubdate` |
| `--resume` | 断点续跑 | off |
| `--rebuild DIR` | 离线重建 | — |

### 限速 profile

| profile | page_size | 页间隔 | 适用 |
|---------|-----------|--------|------|
| **conservative**（默认） | 30 | ~1.5s ±0.5s | 日常全量导出 |
| **balanced** | 50 | ~1.0s ±0.3s | 列表较快 |
| **aggressive** | 50 | ~0.3s ±0.1s | 调试；易限流 |

## 输出结构

```text
catalogs/{uid}-{name}/
  README.md series/*.md
  all.json all.csv by_series.json meta.json
  # 抓取中途: .progress.json all.partial.json
```

## 模块状态

| 模块 | 状态 | opencli | 说明 |
|------|------|---------|------|
| **catalog** | ✅ | `user-videos` | 全量投稿目录、按系列导出 |
| summary | 规划 | `summary` | 批量 AI 总结（更严限速） |
| subtitle | 规划 | `subtitle` | 批量字幕 |

模块说明见各目录 `modules/*/README.md`。

## 示例

- [海安雨（2071007724）](catalogs/2071007724-海安雨/README.md)

## 关于限速与风控（必读）

抓取 B 站数据时，**不能假设「一次拉完、越快越好」就没事**。

投稿列表虽是分页请求，但若 **页间零间隔、失败立刻重试、并发狂打**，仍可能触发限流或风控。批量字幕 / AI 总结风险更高。

本项目将 **限速、退避、断点续跑** 视为能力的一部分。参数参考社区实践与公开错误码，**不是** B 站官方 SLA；默认偏保守。

### 策略摘要

| 项 | 约定 |
|----|------|
| 请求方式 | **串行**翻页，默认禁止并发 |
| 页大小 | 默认约 **30 条/页** |
| 页间隔 | 默认约 **1.5s ± 抖动** |
| 搜索解析 UP 名 | 约 **3–5s/次** |
| 失败 | **指数退避**；识别过频 / 风控信号 |
| 硬风控（如 -412） | **冷却** + **进度落盘** + `--resume` |

### 常见信号（社区归纳）

| 信号 | 含义 | 应对 |
|------|------|------|
| `code -799` | 请求过于频繁 | 退避 |
| `-412` | IP/会话风控拦截 | 长冷却，勿原速死磕 |
| `-352` | 校验/指纹失败 | 检查会话，不单靠 sleep |
| 空列表 / 变慢 | 可能软限流 | 退避后重试 |

### 检索与了解渠道

| 类型 | 渠道 | 看什么 |
|------|------|--------|
| 错误码 | 社区 API 文档镜像（`-799` / `-412` / `-352`） | 失败分类 |
| 开源库 FAQ | bilibili-api 等 | 线性安全、并发易 412 |
| 工程实践 | GitHub / 博客分页 + `sleep` | 1s 级间隔、`ps=30/50` |
| 问答站 | CSDN、掘金、知乎 | 搜索更严、批量需延时 |
| Issue | RSSHub 等 412 讨论 | Cookie / 风控 |
| 开放平台 / 小程序 | open.bilibili.com 频率说明 | 有频次思想，≠ 投稿列表官方 QPS |
| 合规 | 非官方 API 文档停更说明（2026） | 克制使用 |

**重要**：B 站未公开「投稿列表 QPS = x」。策略为工程折中，仅供个人学习与目录整理；请遵守平台规则与法律。

## 相关 opencli

```bash
opencli bilibili user-videos <uid> --limit 30 --page 1 --order pubdate -f json
opencli bilibili search "关键词" --limit 10 -f json
opencli bilibili video <bvid>
opencli bilibili summary <bvid>
opencli bilibili subtitle <bvid>
```

## License

MIT
