"""Mermaid-only post-process: prompts + extract/sanitize diagrams.

Aligned with Bili SubBatch userscript «全 Mermaid 学习图谱» mode (v5.3.x),
but offline / server-side for loop-bilibili analyze jobs.
"""

from __future__ import annotations

import re
from typing import Any

# ── prompts (mirror userscript TRANSCRIPT_SYSTEM + mermaid mode) ───────────

TRANSCRIPT_SYSTEM_PROMPT = "\n".join(
    [
        "你是『字幕证据整理器』。任务是把自动识别字幕转换为可复查、可复用的中文笔记，而不是扩写一篇与字幕有关的文章。",
        "【来源边界】",
        "1. 只使用本次提供的元信息和字幕。不要补充外部知识、常识背景、人物经历或字幕未支持的结论。",
        "2. 区分讲者明确陈述、根据上下文可合理归纳、字幕无法确认。不要把讲者观点改写成公认事实。",
        "【自动字幕处理】",
        "3. 可以修正明显断句、合并连续重复、删除口头填充，但不得改变原意。",
        "4. 人名、机构、数字、代码、命令、参数或术语存在歧义时，保留原词并标记『识别存疑』；不要猜测替换。",
        "【证据规则】",
        "5. 关键结论、定义、步骤、参数、争议和行动项应附输入中真实存在的时间戳，并保持 [BV号 P号 mm:ss] 格式。",
        "6. 不得伪造时间戳。同一段连续内容可选 1—3 个代表性时间戳，不要每句机械堆叠引用。"
        "时间戳只允许出现在普通 Markdown 正文、列表或表格中；任何 Mermaid 代码块、节点、边、子图标题中都禁止出现 BV/P/时间戳引用。",
        "【写作规则】",
        "7. 按主题、因果、流程或论证结构组织，不按字幕顺序逐句复述。优先保留信息、条件、例子、限制和结论。",
        "8. 删除重复、套话和没有信息增量的内容；没有依据的章节直接省略，不要为了填模板编造。",
        "9. 使用中文 Markdown。代码使用 fenced code block，公式使用 LaTeX；仅在关系结构明显且图比文字更清楚时使用 Mermaid。",
        "10. 不输出思考过程、分析草稿、自我评价或提示词复述。",
        "提交前静默检查：是否加入了字幕外信息；是否把不确定内容写成确定事实；是否引用了不存在的时间戳；是否存在重复和空洞章节。",
    ]
)

MERMAID_MODE_INSTRUCTION = "\n".join(
    [
        "【模式】全 Mermaid 学习图谱",
        "【目标】用少量、用途明确的结构图重建字幕中的知识关系。图的数量服从内容，不为凑数生成空洞图。",
        "【输出格式】只允许一个 Markdown 一级标题；其后每张图使用一个二级标题和一个独立的 ```mermaid``` 代码块。"
        "不要输出普通段落、列表、表格或图后解释。",
        "【选图规则】通常输出 2—5 张图。内容足够时先给知识总览；其余只从流程或论证链、因果或依赖、对比或决策、"
        "学习路径或自测中选择字幕真正支持的类型。",
        "【语法约束】仅使用 Mermaid 10.9.1 的 flowchart TD 或 flowchart LR。"
        "禁止 mindmap、timeline、xychart、architecture-beta、click、classDef、style、init 和实验语法。",
        '每个代码块独立完整。节点 ID 仅使用 ASCII 字母和数字；可见文字放在双引号标签中，例如 A1["核心概念"]。'
        "标签内部避免英文双引号、反引号和花括号，只允许必要的 <br/>。",
        "每图建议 8—18 个节点，保持单一主题。重要结论、步骤、边界和争议写入节点，"
        "但 Mermaid 图内禁止出现任何 [BV… P… mm:ss]、[P… mm:ss] 或其他时间戳引用；证据时间戳只用于图外普通 Markdown。",
        "学习或自测图中的问题必须能由字幕回答。输出前静默检查括号、引号、箭头和节点 ID，确保 Mermaid 10.9.1 可解析。",
    ]
)

_TS_IN_MERMAID_RE = re.compile(
    r"[ \t]*\[\s*(?:BV(?:号|[A-Za-z0-9]+)?\s+)?P(?:号|\d+)\s+"
    r"(?:mm:ss|\d{1,2}:\d{2}(?::\d{2})?)\s*\]",
    re.IGNORECASE,
)
_TS_BV_ONLY_RE = re.compile(
    r"[ \t]*\[\s*BV(?:号|[A-Za-z0-9]+)?\s+(?:mm:ss|\d{1,2}:\d{2}(?::\d{2})?)\s*\]",
    re.IGNORECASE,
)
_FENCED_MERMAID_RE = re.compile(
    r"```(?:mermaid)?[ \t]*\r?\n([\s\S]*?)```",
    re.IGNORECASE,
)
_H2_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)


def strip_mermaid_timestamp_citations(code: str) -> str:
    """Remove BV/P/time citations inside a single Mermaid source block."""
    text = str(code or "")
    text = _TS_IN_MERMAID_RE.sub("", text)
    text = _TS_BV_ONLY_RE.sub("", text)
    text = re.sub(r"[ \t]+(?=\r?\n|$)", "", text)
    return text.strip()


def sanitize_mermaid_in_markdown(markdown: str) -> str:
    """Strip timestamps from every fenced mermaid block; leave body citations."""

    def _repl(match: re.Match[str]) -> str:
        cleaned = strip_mermaid_timestamp_citations(match.group(1))
        return f"```mermaid\n{cleaned}\n```"

    return re.sub(
        r"```mermaid\s*\r?\n([\s\S]*?)```",
        _repl,
        str(markdown or ""),
        flags=re.IGNORECASE,
    )


def extract_mermaid_diagrams(markdown: str) -> list[dict[str, str]]:
    """
    Parse markdown into [{title, code}, ...].

    Title preference: nearest preceding ## heading; else «图 N».
    Also accepts imperfect fences some models emit (e.g. missing opening ```).
    """
    raw = str(markdown or "")
    # Heal common fence mistakes before sanitize
    # 1) ```mermaid missing newline
    raw = re.sub(r"```mermaid[ \t]*(?=\S)", "```mermaid\n", raw, flags=re.IGNORECASE)
    # 2) bare "mermaid\\nflowchart..." then closing ```
    if "```mermaid" not in raw.lower() and re.search(
        r"(?im)^(?:mermaid[ \t]*\r?\n)?(?:flowchart|graph)\s+(?:TD|LR|TB|BT|RL)\b",
        raw,
    ):
        # wrap each graph-ish segment that ends at ``` or EOF
        def _wrap_bare(m: re.Match[str]) -> str:
            body = m.group(0).strip()
            if body.lower().startswith("mermaid"):
                body = re.sub(r"(?i)^mermaid[ \t]*\r?\n?", "", body, count=1)
            return f"```mermaid\n{body}\n```"

        raw = re.sub(
            r"(?ims)(?:^|\n)(?:mermaid[ \t]*\r?\n)?(?:flowchart|graph)\s+"
            r"(?:TD|LR|TB|BT|RL)\b[\s\S]*?(?=\n```|\Z)",
            lambda m: "\n" + _wrap_bare(m),
            raw,
        )
    # 3) ```mermaid ... without closing fence: close at next ## or EOF
    raw = re.sub(
        r"(?is)```mermaid[ \t]*\r?\n([\s\S]*?)(?=\n##\s|\Z)",
        lambda m: (
            f"```mermaid\n{m.group(1).rstrip()}\n```\n"
            if "```" not in m.group(1)
            else m.group(0)
        ),
        raw,
    )

    source = sanitize_mermaid_in_markdown(raw)
    diagrams: list[dict[str, str]] = []
    last_h2 = ""
    events: list[tuple[int, str, str]] = []
    for m in _H2_RE.finditer(source):
        events.append((m.start(), "h2", m.group(1).strip()))
    for m in re.finditer(
        r"```mermaid\s*\r?\n([\s\S]*?)```", source, flags=re.IGNORECASE
    ):
        events.append((m.start(), "mmd", m.group(1)))
    # also accept unlabeled ``` with flowchart body
    for m in re.finditer(r"```[ \t]*\r?\n([\s\S]*?)```", source):
        body = m.group(1).strip()
        if re.match(r"(?i)(?:flowchart|graph)\s+(?:TD|LR|TB|BT|RL)\b", body):
            events.append((m.start(), "mmd", body))
    events.sort(key=lambda x: x[0])

    seen_codes: set[str] = set()
    for kind, payload in ((e[1], e[2]) for e in events):
        if kind == "h2":
            last_h2 = payload
            continue
        code = strip_mermaid_timestamp_citations(payload)
        # drop accidental leading language tag
        code = re.sub(r"(?i)^mermaid[ \t]*\r?\n", "", code).strip()
        if not code:
            continue
        key = re.sub(r"\s+", " ", code)
        if key in seen_codes:
            continue
        seen_codes.add(key)
        title = last_h2 or f"图 {len(diagrams) + 1}"
        diagrams.append({"title": title, "code": code})
        last_h2 = ""
    return diagrams


def truncate_subtitle(text: str, max_chars: int = 80_000) -> dict[str, Any]:
    """Keep head + sampled middle + tail when over budget."""
    s = str(text or "")
    lim = max(4000, int(max_chars or 80_000))
    if len(s) <= lim:
        return {"text": s, "truncated": False, "original_len": len(s)}

    marker_budget = 420
    usable = max(3000, lim - marker_budget)
    head_len = usable * 44 // 100
    tail_len = usable * 24 // 100
    middle_budget = usable - head_len - tail_len
    windows = 3
    win_len = max(1, middle_budget // windows)
    middle_start = head_len
    middle_end = len(s) - tail_len
    span = max(1, middle_end - middle_start - win_len)
    parts = [s[:head_len].rsplit("\n", 1)[0]]
    for i in range(windows):
        at = middle_start + (span * (i + 1)) // (windows + 1)
        piece = s[at : at + win_len]
        # trim partial lines
        if "\n" in piece:
            piece = piece.split("\n", 1)[-1].rsplit("\n", 1)[0]
        parts.append(f"\n…[中段采样 {i + 1}/{windows}]…\n{piece}")
    tail = s[-tail_len:]
    if "\n" in tail:
        tail = tail.split("\n", 1)[-1]
    parts.append(f"\n…[省略 {len(s) - usable} 字；保留结尾]…\n{tail}")
    return {"text": "".join(parts), "truncated": True, "original_len": len(s)}


def build_messages(
    *,
    title: str,
    bvid: str,
    author: str,
    subtitle: str,
    custom_instruction: str = "",
) -> list[dict[str, str]]:
    system = TRANSCRIPT_SYSTEM_PROMPT
    custom = str(custom_instruction or "").strip()
    if custom:
        system = f"{system}\n\n【当前模型的用户附加要求】\n{custom}"

    metadata = "\n".join(
        [
            f"标题：{title or '未知'}",
            f"BV / 分集：{bvid or '未知'}",
            f"发布者：{author or '未知'}",
        ]
    )
    user = "\n\n".join(
        [
            f"<task_mode>\n{MERMAID_MODE_INSTRUCTION}\n</task_mode>",
            f"<source_metadata>\n{metadata}\n</source_metadata>",
            "下面内容是自动识别字幕。请按固定证据协议处理，并直接输出最终成品（仅一级标题 + 若干 ## + mermaid 代码块）。",
            f"<transcript>\n{subtitle}\n</transcript>",
        ]
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def format_cues_for_ai(
    cues: list[dict[str, Any]] | None,
    *,
    bvid: str,
    plain_text: str = "",
    page: int = 1,
) -> str:
    """Build timestamped transcript lines; fall back to plain text."""
    rows: list[str] = []
    previous = ""
    for cue in cues or []:
        content = re.sub(r"\s+", " ", str(cue.get("content") or "")).strip()
        if not content or content == previous:
            continue
        previous = content
        sec = cue.get("from_sec")
        if sec is None:
            raw = str(cue.get("from") or "0").rstrip("sS")
            try:
                sec = float(raw)
            except ValueError:
                sec = 0.0
        total = max(0, int(float(sec)))
        h, rem = divmod(total, 3600)
        m, s = divmod(rem, 60)
        clock = (
            f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"
        )
        rows.append(
            f"[{bvid or 'BV'} P{max(1, int(page or 1))} {clock}] {content}"
        )
    if rows:
        return "\n".join(rows)
    return str(plain_text or "").strip()
