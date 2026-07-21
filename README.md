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

## 关于限速与风控（必读）

抓取 B 站数据时，**不能假设「一次拉完、越快越好」就没事**。

投稿列表虽然是分页请求（不是把全站塞进一个 HTTP），但若 **页与页之间零间隔、失败立刻重试、甚至并发狂打**，仍可能触发限流或风控：轻则返回「请求过于频繁」，重则 IP/会话被拦截，任务中途失败且难以恢复。批量做字幕、评论、AI 总结时风险更高。

因此本项目将 **限速、退避、断点续跑** 视为导出能力的一部分，而不是可选项。实现与默认参数以社区实践与公开错误码为参考（见下），**不是** B 站官方 SLA；平台策略会变，我们默认偏保守。

### 我们采用的策略（摘要）

| 项 | 约定 |
|----|------|
| 请求方式 | **串行**翻页，默认禁止并发 |
| 页大小 | 默认约 **30 条/页**（比 50 更温和） |
| 页间隔 | 默认约 **1.5s ± 抖动**，避免整秒对齐 |
| 搜索解析 UP 名 | 更慢（约 **3–5s/次** 量级） |
| 失败 | **指数退避重试**；识别过频 / 风控码，而非盲目重试 |
| 硬风控（如 -412） | **冷却后再试**，并 **落盘进度** 支持续跑 |
| 扩展能力 | 全量字幕 / 总结等按「每视频」更严限速，与列表导出分开 |

当前脚本若尚未完全落地上述参数，以仓库后续更新为准；策略意图以本节为准。

### 常见相关信号（社区归纳）

| 信号 | 常见含义 | 应对思路 |
|------|----------|----------|
| `code -799` | 请求过于频繁 | 加长等待，指数退避 |
| `code` / HTTP `-412` | 请求被风控拦截（常与 IP/会话相关） | 长冷却，勿原速死磕 |
| `code -352` | 校验/指纹类失败（如 UA、签名） | 检查会话与客户端，不单靠 sleep |
| 空列表 / 明显变慢 | 可能「软限流」 | 当可疑过频处理，退避后重试 |

说明：限流 **不一定** 返回 HTTP 429，需结合业务 `code` 与空数据等情况判断。

### 检索与了解渠道

撰写本节时参考了下列类型的公开信息（工程折中，非官方保证）：

| 类型 | 渠道 / 代表 | 看什么 |
|------|-------------|--------|
| 错误码整理 | 社区 bilibili API 文档镜像中的公共错误码表（如 `-799` / `-412` / `-352` 等） | 失败如何分类 |
| Python 调用库 FAQ | bilibili-api 一类开源库的文档与说明 | 线性请求相对安全、并发易 412 |
| 工程实践 | GitHub 示例脚本、博客（投稿列表 `space` 分页 + `sleep`） | 常见间隔 1s 级、`ps=30/50` |
| 站点/问答 | CSDN、掘金、知乎等「B 站爬虫 / 限流 / 412」讨论 | 搜索更严、批量需延时 |
| 产品 issue | RSSHub 等「B 站空间/投稿 412」类 issue | 风控与 Cookie/访问方式 |
| 开放平台 / 小程序文档 | [open.bilibili.com](https://open.bilibili.com)、小程序「接口调用频率」类说明 | 官方有「按频次限制」的产品思路；**不等于**网页投稿接口的公开 QPS |
| 合规动态 | 非官方 API 汇总仓库停更/归档相关公开说明（2026） | 克制使用、避免滥用 |

**重要**：B 站 **没有** 对外承诺「投稿列表允许 QPS = x」。上表策略是综合公开讨论后的 **保守默认**，仅用于个人学习与目录整理；请合理控制频率，遵守平台规则与适用法律。

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
