# ROADMAP / 待办（可扩展字幕管线）

当前已落地：**可插拔 Pipeline + processors**（抓取时即可写 srt/txt/index、normalize）。

下面是「比如」类想法拆解，按优先级排期。

---

## P0 — 稳健与可扩展（已做 / 紧接着）

- [x] `SubtitlePipeline` + `BatchConfig` / `BatchStats`
- [x] `SubtitleProcessor` 协议与默认链：normalize → srt → txt → index
- [x] `BiliClient` 可注入 http / wbi
- [x] 抓取时同步写 `txt/` + `index.jsonl`（实时落盘，不必等 pack）
- [ ] 单元测试覆盖 pipeline resume、processor 注入、normalize
- [ ] `pack-subtitles` 优先复用已有 `txt/` / `index.jsonl`，减少二次生成

## P1 — 抓取时「实时处理」

- [ ] **Processor: quality gate**  
  过滤过短 / 乱码 / 纯音乐标；写 `extras.quality`
- [ ] **Processor: chapter-ish split**  
  按静默/长间隔切段落 → `segments.json`（非 LLM）
- [ ] **Processor: webhook / log sink**  
  每条 ok 回调 HTTP 或写 `events.jsonl`（给下游消费）
- [ ] **并发可选（默认仍 serial）**  
  `BatchConfig.workers=1|2`，严格限速令牌桶，防风控

## P2 — 对接 LLM 梳理字幕

- [x] **Mermaid AI 后处理（v2 analyze job）**  
  `ai_worker` + `ai_mermaid`：字幕 ok → `analyze` → OpenAI 兼容 API → `analyses` 表（markdown + diagrams_json）
- [x] **配置与密钥**  
  `config.toml [ai]` + `.env` 的 `AI_API_KEY` / `AI_BASE_URL` / `AI_MODEL`（密钥不进 git）
- [x] **Pages 可视化**  
  `build_subtitle_site.py` 导出 diagrams，前端 mermaid@10.9.1 渲染；视频页可切换图谱/字幕
- [x] **CLI**  
  `loop-bilibili analyze [--bvid] [--force] [--limit]`
- [ ] **成本控制**  
  仅 `cue_count > N` 或标题匹配才调 LLM；缓存命中跳过
- [ ] **结构化 schema 扩展**  
  嘉宾 / 主题 / 时间线大纲（当前仅 mermaid mode）

## P3 — GitHub Actions 日更

- [ ] **workflow: daily catalog delta**  
  cron：对 `catalogs/*` 或配置列表拉最新投稿
- [ ] **workflow: subtitle resume**  
  对 `data/subtitle/*` resume 增量；失败开 issue
- [ ] **workflow: pack + commit**  
  `pack-subtitles` 后 commit `data/subtitles`（或 PR）
- [ ] **workflow: LLM weekly**  
  仅周末跑 summary（省钱）；artifact 上传
- [ ] **Secrets**  
  `BILI_COOKIE`、可选 `LLM_API_KEY`；文档写清轮换方式

## P4 — 产品化

- [ ] 多 UP 编排：`profiles.yaml` 声明 mid 列表与 processors
- [ ] 搜索：本地 `index.jsonl` 全文（或 sqlite FTS）
- [ ] 导出：合集 Markdown / 单嘉宾合订本
- [ ] 付费课堂（cheese）已购路径（harness + 权限检测）

---

## 扩展点速查（写代码时用）

```python
from bili_subbatch import BatchConfig, SubtitlePipeline
from bili_subbatch.processors.base import BaseProcessor

class MyLlmProcessor(BaseProcessor):
    name = "llm_summary"
    def on_result(self, result, row, ctx):
        if result.status != "ok":
            return row
        # text = result.plain_text()
        # row["extras"] = {"summary": "..."}
        return row

pipe = SubtitlePipeline(
    BatchConfig(delay=0.5, write_txt=True),
    processors=[..., MyLlmProcessor()],
)
pipe.run(bvids, "data/subtitle/UID-name")
```

实现新能力时：**优先加 Processor**，不要改 `client.py` 核心请求链。
