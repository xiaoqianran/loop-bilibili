// ==UserScript==
// @name         Bili SubBatch (loop-bilibili)
// @namespace    https://github.com/loop-bilibili/bili-subbatch
// @version      5.0.5
// @description  B站字幕研究工作台 v2：四级缓存、字幕检索跳转、同步高亮、清晰可缩放 Mermaid 与 AI 笔记
// @author       loop-bilibili
// @match        *://www.bilibili.com/video/*
// @match        *://www.bilibili.com/list/*
// @match        *://www.bilibili.com/bangumi/play/*
// @match        *://www.bilibili.com/medialist/*
// @match        *://www.bilibili.com/favlist*
// @match        *://space.bilibili.com/*
// @match        *://search.bilibili.com/*
// @connect      api.bilibili.com
// @connect      aisubtitle.hdslb.com
// @connect      *.hdslb.com
// @connect      bilibili.com
// @connect      *
// @connect      cdn.jsdelivr.net
// @grant        unsafeWindow
// @grant        GM_xmlhttpRequest
// @grant        GM_setClipboard
// @grant        GM_addStyle
// @grant        GM_info
// @grant        GM_setValue
// @grant        GM_getValue
// @run-at       document-idle
// @license      MIT
// @downloadURL none
// ==/UserScript==

/**
 * v2.0.3 — 新增「全 Mermaid 学习图谱」模式：多图拆解知识、流程、因果、学习路径与自测。
 * v2.0.1 — Mermaid 高对比可读渲染：原始宽度、缩放工具栏与全屏查看。
 * v2.0 — 四级缓存与 stale-while-revalidate；当前字幕全文检索、时间跳转、播放同步高亮。
 * v1.1 — 当前视频自动抓字幕：页面直读优先、内存缓存、WBI 完整链路回退。
 * v1.0 — 安全 Markdown、时间戳证据、按需渲染、追加式流输出、低功耗 UI。
 * v0.8 — Peer AI-userscript practices: page fetch stream first,
 * GM stream fallback (no timeout), stick-bottom scroll, GM storage.
 * See PEER_AI_PRACTICES.md.
 */

(function () {
  "use strict";

  const SCRIPT_VERSION =
    (typeof GM_info !== "undefined" && GM_info?.script?.version) || "5.0.5";
  const PANEL_ID = "bili-subbatch-panel";
  const UI_STORE_KEY = "bili-subbatch-ui-v2";
  /** v2：默认 stream=true，避免非流式长推理被中间层 10s 掐断 (client_gone) */
  const AI_STORE_KEY = "bili-subbatch-ai-v2";
  const AUTO_CAPTURE_STORE_KEY = "bili-subbatch-auto-capture-v1";
  const AUTO_ANALYZE_STORE_KEY = "bili-subbatch-auto-analyze-v1";
  const TRANSCRIPT_FOLLOW_STORE_KEY = "bili-subbatch-transcript-follow-v2";
  const PLAYER_SUBTITLE_STORE_KEY = "bili-subbatch-player-subtitle-v2";
  const PLAYER_SUBTITLE_SELECTORS = Object.freeze({
    button: ".bpx-player-ctrl-subtitle",
    panel: ".bpx-player-ctrl-subtitle-box",
    item: ".bpx-player-ctrl-subtitle-language-item[data-lan]",
    active: ".bpx-player-ctrl-subtitle-language-item.bpx-state-active",
  });
  const CACHE_DB_NAME = "bili-subbatch-cache-v2";
  const CACHE_DB_VERSION = 1;
  const CACHE_STORE_NAME = "records";
  const CACHE_CHANNEL_NAME = "bili-subbatch-cache-v2";
  const CACHE_SESSION_PREFIX = "bsb:v2:";
  const MEMORY_CACHE_LIMIT = 32;
  const VIEW_CACHE_TTL_MS = 30 * 60_000;
  const TRACK_CACHE_TTL_MS = 10 * 60_000;
  const SUBTITLE_CACHE_TTL_MS = 30 * 24 * 60 * 60_000;
  const SUBTITLE_REVALIDATE_MS = 12 * 60 * 60_000;
  const AUTO_CAPTURE_DELAY_MS = 420;
  const AUTO_ANALYZE_DELAY_MS = 180;
  const pageWindow = typeof unsafeWindow !== "undefined" ? unsafeWindow : window;
  const MAX_SUBTITLE_CHARS = 160000;
  const STREAM_PAINT_INTERVAL_MS = 48;
  const RENDER_BATCH_SIZE = 24;
  const NOTE_FONT_MIN = 14;
  const NOTE_FONT_MAX = 22;
  const WBI_TTL_MS = 600_000;
  const DEFAULT_DELAY_MS = 400;
  const DEFAULT_MAX_PAGES = 20;
  const MIN_W = 420;
  const MIN_H = 520;
  const DOCK_EDGE_PX = 32;
  const DOCK_SNAP_PX = 36;

  /** OpenAI 兼容默认值（密钥优先存入 userscript 隔离存储） */
  const AI_DEFAULTS = {
    baseUrl: "",
    // Never ship secrets in-repo; user fills in Settings (GM/local storage only)
    apiKey: "",
    model: "openai/gpt-oss-120b",
    temperature: 0.4,
    maxTokens: 4096,
    /**
     * 必须默认 true：非流式要等整包，长请求常被扩展/代理 ~10s 空闲断开
     * 网关日志表现为 client_gone / context canceled。
     * 流式会先推 reasoning，已兼容 content+reasoning 字段。
     */
    stream: true,
    systemPrompt:
      "你是严谨的视频研究笔记助手。只依据用户提供的带时间戳字幕写中文 Markdown 笔记。" +
      "重要结论、步骤和定义尽可能附来源时间戳，格式为 [BV号 P号 mm:ss]；不要伪造时间戳。" +
      "代码使用 fenced code block；确有结构关系时才使用 mermaid；公式使用 LaTeX。" +
      "优先使用短段落、信息密度高的列表与清晰层级，不输出思考过程，不编造字幕外事实。",
    userPromptTemplate:
      "{{modeInstruction}}\n\n" +
      "请把以下字幕整理为可长期复习、可核查来源的研究笔记：\n" +
      "1. 核心摘要（3—6 条）\n2. 结构化详细笔记\n3. 关键概念与术语\n" +
      "4. 方法、流程或架构（确有必要时用 mermaid）\n5. 可执行事项与仍不确定之处\n\n" +
      "引用关键内容时保留 [BV号 P号 mm:ss]。避免空泛套话和机械复述。\n\n" +
      "【元信息】\n标题：{{title}}\nBV：{{bvid}}\nUP：{{author}}\n\n" +
      "【带时间戳字幕】\n{{subtitle}}\n",
  };
  /**
   * 「全 Mermaid 学习图谱」模式的附加系统约束。
   * 与用户已保存的 systemPrompt 合并，避免旧配置中的普通笔记要求削弱专属模式。
   */
  const MERMAID_LEARNING_SYSTEM_PROMPT = [
    "当前选择的是『全 Mermaid 学习图谱』模式。",
    "除 Markdown 一级标题、每张图的二级标题和最多一句非实质性读图提示外，所有实质内容必须放入 Mermaid fenced code block；不要再用普通段落、项目符号、表格重复图中信息。",
    "根据字幕内容生成 3—6 张可独立渲染的结构图；至少包括知识总览、概念关系或因果链、过程或论证链、学习路径与自测图。没有依据的类别不要硬编。",
    "全部图表只使用 Mermaid 10.9.1 稳定语法：flowchart TD 或 flowchart LR。不要使用 mindmap、timeline、xychart、packet、architecture-beta、click、classDef、style、主题初始化指令或实验语法。",
    "每个代码块必须独立完整，不跨代码块复用节点 ID。节点 ID 只使用 ASCII 字母与数字，例如 A1、B2；可见文本统一写入带双引号的节点标签，例如 A1[\"核心概念\"]。",
    "节点标签保持简短，单图通常不超过 18 个节点；避免标签中的英文双引号、反引号、花括号和复杂 HTML，只允许必要的 <br/> 换行。",
    "重要结论、定义、步骤和争议尽量在对应节点中保留字幕时间戳，格式继续使用 [BV号 P号 mm:ss]；不得伪造来源。",
    "学习路径与自测图应把先修知识、理解顺序、易错点、复习问题和应用检查连接成可执行的学习闭环。",
    "输出前自行检查每个 Mermaid 块的括号、引号、连线和节点 ID，确保可被 Mermaid 10.9.1 直接解析。",
  ].join("\n");

  const UA =
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 " +
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36";

  function loadAutoCaptureSetting() {
    const raw = storageGet(AUTO_CAPTURE_STORE_KEY, true);
    return ![false, 0, "0", "false", "off"].includes(raw);
  }

  function saveAutoCaptureSetting(enabled) {
    storageSet(AUTO_CAPTURE_STORE_KEY, enabled ? "true" : "false");
  }

  /** 默认开启：当前视频字幕抓取成功后，自动执行与“开始分析”按钮相同的流程。 */
  function loadAutoAnalyzeSetting() {
    const raw = storageGet(AUTO_ANALYZE_STORE_KEY, true);
    return ![false, 0, "0", "false", "off"].includes(raw);
  }

  function saveAutoAnalyzeSetting(enabled) {
    storageSet(AUTO_ANALYZE_STORE_KEY, enabled ? "true" : "false");
  }

  function loadTranscriptFollowSetting() {
    const raw = storageGet(TRANSCRIPT_FOLLOW_STORE_KEY, true);
    return ![false, 0, "0", "false", "off"].includes(raw);
  }

  function loadPlayerSubtitleSetting() {
    const raw = storageGet(PLAYER_SUBTITLE_STORE_KEY, true);
    return ![false, 0, "0", "false", "off"].includes(raw);
  }

  function debounce(fn, wait = 100) {
    let timer = 0;
    return function (...args) {
      clearTimeout(timer);
      timer = window.setTimeout(() => fn.apply(this, args), wait);
    };
  }

  function lruGet(map, key) {
    if (!map.has(key)) return null;
    const value = map.get(key);
    map.delete(key);
    map.set(key, value);
    return value;
  }

  function lruSet(map, key, value, max = MEMORY_CACHE_LIMIT) {
    if (map.has(key)) map.delete(key);
    map.set(key, value);
    while (map.size > max) map.delete(map.keys().next().value);
    return value;
  }

  function sessionCacheRead(kind, key, ttlMs) {
    try {
      const raw = sessionStorage.getItem(`${CACHE_SESSION_PREFIX}${kind}:${key}`);
      if (!raw) return null;
      const record = JSON.parse(raw);
      if (!record || Date.now() - Number(record.at || 0) > ttlMs) {
        sessionStorage.removeItem(`${CACHE_SESSION_PREFIX}${kind}:${key}`);
        return null;
      }
      return record.value;
    } catch (_) {
      return null;
    }
  }

  function sessionCacheWrite(kind, key, value) {
    try {
      sessionStorage.setItem(
        `${CACHE_SESSION_PREFIX}${kind}:${key}`,
        JSON.stringify({ at: Date.now(), value }),
      );
    } catch (_) {
      /* storage quota or privacy mode: memory/IDB still work */
    }
  }

  const MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
    33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40, 61,
    26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36,
    20, 34, 44, 52,
  ];

  const TYPE_LABEL = {
    video: "单个视频",
    selection: "视频选集",
    user: "个人主页",
    favorite: "收藏夹",
    collection: "合集",
    search: "搜索页",
    unknown: "未知页面",
  };

  /** 可手动切换的模式（auto 走识别；其余强制类型） */
  const MODE_OPTIONS = [
    "auto",
    "video",
    "selection",
    "user",
    "favorite",
    "collection",
    "search",
  ];

  /** AI 笔记输出模式；集中维护，避免 UI 与持久化白名单不一致。 */
  const NOTE_MODE_OPTIONS = Object.freeze([
    "deep",
    "concise",
    "study",
    "action",
    "mermaid",
  ]);

  // ─── MD5 ────────────────────────────────────────────────────────────────
  function md5(str) {
    function cmn(q, a, b, x, s, t) {
      a = (a + q + x + t) | 0;
      return (((a << s) | (a >>> (32 - s))) + b) | 0;
    }
    function ff(a, b, c, d, x, s, t) {
      return cmn((b & c) | (~b & d), a, b, x, s, t);
    }
    function gg(a, b, c, d, x, s, t) {
      return cmn((b & d) | (c & ~d), a, b, x, s, t);
    }
    function hh(a, b, c, d, x, s, t) {
      return cmn(b ^ c ^ d, a, b, x, s, t);
    }
    function ii(a, b, c, d, x, s, t) {
      return cmn(c ^ (b | ~d), a, b, x, s, t);
    }
    function toUtf8Bytes(input) {
      const s = unescape(encodeURIComponent(input));
      const out = new Array(s.length);
      for (let i = 0; i < s.length; i++) out[i] = s.charCodeAt(i);
      return out;
    }
    function bytesToWords(bytes) {
      const words = [];
      for (let i = 0; i < bytes.length; i++) {
        words[i >> 2] |= bytes[i] << ((i % 4) * 8);
      }
      return words;
    }
    function wordsToHex(words) {
      const hex = "0123456789abcdef";
      let out = "";
      for (let i = 0; i < words.length * 4; i++) {
        out +=
          hex.charAt((words[i >> 2] >> ((i % 4) * 8 + 4)) & 0x0f) +
          hex.charAt((words[i >> 2] >> ((i % 4) * 8)) & 0x0f);
      }
      return out;
    }
    const bytes = toUtf8Bytes(str);
    const bitLen = bytes.length * 8;
    bytes.push(0x80);
    while (bytes.length % 64 !== 56) bytes.push(0);
    const words = bytesToWords(bytes);
    words.push(bitLen >>> 0);
    words.push(Math.floor(bitLen / 0x100000000));
    let a0 = 0x67452301,
      b0 = 0xefcdab89,
      c0 = 0x98badcfe,
      d0 = 0x10325476;
    for (let i = 0; i < words.length; i += 16) {
      let a = a0,
        b = b0,
        c = c0,
        d = d0;
      const w = words.slice(i, i + 16);
      while (w.length < 16) w.push(0);
      a = ff(a, b, c, d, w[0], 7, 0xd76aa478);
      d = ff(d, a, b, c, w[1], 12, 0xe8c7b756);
      c = ff(c, d, a, b, w[2], 17, 0x242070db);
      b = ff(b, c, d, a, w[3], 22, 0xc1bdceee);
      a = ff(a, b, c, d, w[4], 7, 0xf57c0faf);
      d = ff(d, a, b, c, w[5], 12, 0x4787c62a);
      c = ff(c, d, a, b, w[6], 17, 0xa8304613);
      b = ff(b, c, d, a, w[7], 22, 0xfd469501);
      a = ff(a, b, c, d, w[8], 7, 0x698098d8);
      d = ff(d, a, b, c, w[9], 12, 0x8b44f7af);
      c = ff(c, d, a, b, w[10], 17, 0xffff5bb1);
      b = ff(b, c, d, a, w[11], 22, 0x895cd7be);
      a = ff(a, b, c, d, w[12], 7, 0x6b901122);
      d = ff(d, a, b, c, w[13], 12, 0xfd987193);
      c = ff(c, d, a, b, w[14], 17, 0xa679438e);
      b = ff(b, c, d, a, w[15], 22, 0x49b40821);
      a = gg(a, b, c, d, w[1], 5, 0xf61e2562);
      d = gg(d, a, b, c, w[6], 9, 0xc040b340);
      c = gg(c, d, a, b, w[11], 14, 0x265e5a51);
      b = gg(b, c, d, a, w[0], 20, 0xe9b6c7aa);
      a = gg(a, b, c, d, w[5], 5, 0xd62f105d);
      d = gg(d, a, b, c, w[10], 9, 0x02441453);
      c = gg(c, d, a, b, w[15], 14, 0xd8a1e681);
      b = gg(b, c, d, a, w[4], 20, 0xe7d3fbc8);
      a = gg(a, b, c, d, w[9], 5, 0x21e1cde6);
      d = gg(d, a, b, c, w[14], 9, 0xc33707d6);
      c = gg(c, d, a, b, w[3], 14, 0xf4d50d87);
      b = gg(b, c, d, a, w[8], 20, 0x455a14ed);
      a = gg(a, b, c, d, w[13], 5, 0xa9e3e905);
      d = gg(d, a, b, c, w[2], 9, 0xfcefa3f8);
      c = gg(c, d, a, b, w[7], 14, 0x676f02d9);
      b = gg(b, c, d, a, w[12], 20, 0x8d2a4c8a);
      a = hh(a, b, c, d, w[5], 4, 0xfffa3942);
      d = hh(d, a, b, c, w[8], 11, 0x8771f681);
      c = hh(c, d, a, b, w[11], 16, 0x6d9d6122);
      b = hh(b, c, d, a, w[14], 23, 0xfde5380c);
      a = hh(a, b, c, d, w[1], 4, 0xa4beea44);
      d = hh(d, a, b, c, w[4], 11, 0x4bdecfa9);
      c = hh(c, d, a, b, w[7], 16, 0xf6bb4b60);
      b = hh(b, c, d, a, w[10], 23, 0xbebfbc70);
      a = hh(a, b, c, d, w[13], 4, 0x289b7ec6);
      d = hh(d, a, b, c, w[0], 11, 0xeaa127fa);
      c = hh(c, d, a, b, w[3], 16, 0xd4ef3085);
      b = hh(b, c, d, a, w[6], 23, 0x04881d05);
      a = hh(a, b, c, d, w[9], 4, 0xd9d4d039);
      d = hh(d, a, b, c, w[12], 11, 0xe6db99e5);
      c = hh(c, d, a, b, w[15], 16, 0x1fa27cf8);
      b = hh(b, c, d, a, w[2], 23, 0xc4ac5665);
      a = ii(a, b, c, d, w[0], 6, 0xf4292244);
      d = ii(d, a, b, c, w[7], 10, 0x432aff97);
      c = ii(c, d, a, b, w[14], 15, 0xab9423a7);
      b = ii(b, c, d, a, w[5], 21, 0xfc93a039);
      a = ii(a, b, c, d, w[12], 6, 0x655b59c3);
      d = ii(d, a, b, c, w[3], 10, 0x8f0ccc92);
      c = ii(c, d, a, b, w[10], 15, 0xffeff47d);
      b = ii(b, c, d, a, w[1], 21, 0x85845dd1);
      a = ii(a, b, c, d, w[8], 6, 0x6fa87e4f);
      d = ii(d, a, b, c, w[15], 10, 0xfe2ce6e0);
      c = ii(c, d, a, b, w[6], 15, 0xa3014314);
      b = ii(b, c, d, a, w[13], 21, 0x4e0811a1);
      a = ii(a, b, c, d, w[4], 6, 0xf7537e82);
      d = ii(d, a, b, c, w[11], 10, 0xbd3af235);
      c = ii(c, d, a, b, w[2], 15, 0x2ad7d2bb);
      b = ii(b, c, d, a, w[9], 21, 0xeb86d391);
      a0 = (a0 + a) | 0;
      b0 = (b0 + b) | 0;
      c0 = (c0 + c) | 0;
      d0 = (d0 + d) | 0;
    }
    return wordsToHex([a0, b0, c0, d0]);
  }

  // ─── pure helpers (offline harness extracts // #region pure-logic) ─────
  // #region pure-logic
  function keyFromUrl(url) {
    let name = String(url || "").split("/").pop() || "";
    if (name.includes(".")) name = name.split(".").slice(0, -1).join(".");
    return name;
  }

  function mixinKey(imgKey, subKey) {
    let raw = String(imgKey) + String(subKey);
    const maxIdx = Math.max(...MIXIN_KEY_ENC_TAB);
    if (maxIdx >= raw.length) raw = raw.padEnd(maxIdx + 1, "0");
    let out = "";
    for (const i of MIXIN_KEY_ENC_TAB) out += raw[i] || "";
    return out.slice(0, 32);
  }

  function encWbi(params, imgKey, subKey, wts) {
    const data = {};
    for (const [k, v] of Object.entries(params)) data[String(k)] = v;
    data.wts = wts == null ? Math.floor(Date.now() / 1000) : Number(wts);
    const forbidden = new Set(["!", "'", "(", ")", "*"]);
    const parts = [];
    for (const key of Object.keys(data).sort()) {
      const val = String(data[key])
        .split("")
        .filter((c) => !forbidden.has(c))
        .join("");
      parts.push(`${encodeURIComponent(key)}=${encodeURIComponent(val)}`);
    }
    const query = parts.join("&");
    return `${query}&w_rid=${md5(query + mixinKey(imgKey, subKey))}`;
  }

  function applyPromptTemplate(tpl, vars) {
    return String(tpl || "").replace(/\{\{(\w+)\}\}/g, (_, k) =>
      vars && vars[k] != null ? String(vars[k]) : "",
    );
  }

  function extractAssistantText(piece) {
    if (!piece || typeof piece !== "object") return { content: "", reasoning: "" };
    const content =
      (typeof piece.content === "string" && piece.content) ||
      (typeof piece.text === "string" && piece.text) ||
      "";
    const reasoning =
      (typeof piece.reasoning_content === "string" && piece.reasoning_content) ||
      (typeof piece.reasoning === "string" && piece.reasoning) ||
      "";
    return { content, reasoning };
  }

  function extractFromChoice(choice) {
    if (!choice) return { content: "", reasoning: "" };
    const fromDelta = extractAssistantText(choice.delta);
    const fromMsg = extractAssistantText(choice.message);
    return {
      content: fromDelta.content || fromMsg.content || "",
      reasoning: fromDelta.reasoning || fromMsg.reasoning || "",
    };
  }

  function formatAiDisplay(content, reasoning) {
    const body = String(content || "");
    if (body.trim()) return body;
    // 不把供应商返回的 reasoning / chain-of-thought 暴露到界面。
    return reasoning && String(reasoning).trim() ? "正在分析字幕并组织笔记…" : "";
  }

  function truncateForAi(text, maxChars) {
    const s = String(text || "");
    const lim = maxChars == null ? MAX_SUBTITLE_CHARS : Math.max(4000, Number(maxChars));
    if (s.length <= lim) return { text: s, truncated: false, originalLen: s.length };

    // 比“只保留开头”更稳：保留首尾，并从中段均匀抽取连续窗口。
    const markerBudget = 420;
    const usable = Math.max(3000, lim - markerBudget);
    const headLen = Math.floor(usable * 0.44);
    const tailLen = Math.floor(usable * 0.24);
    const middleBudget = usable - headLen - tailLen;
    const windows = 3;
    const winLen = Math.floor(middleBudget / windows);
    const middleStart = headLen;
    const middleEnd = s.length - tailLen;
    const span = Math.max(1, middleEnd - middleStart - winLen);
    const parts = [s.slice(0, headLen).replace(/[^\n]*$/, "")];
    for (let i = 0; i < windows; i++) {
      const at = middleStart + Math.floor((span * (i + 1)) / (windows + 1));
      let piece = s.slice(at, at + winLen);
      piece = piece.replace(/^[^\n]*\n?/, "").replace(/[^\n]*$/, "");
      parts.push(`\n…[中段采样 ${i + 1}/${windows}]…\n${piece}`);
    }
    parts.push(`\n…[省略 ${s.length - usable} 字；保留结尾]…\n${s.slice(-tailLen).replace(/^[^\n]*\n?/, "")}`);
    return { text: parts.join(""), truncated: true, originalLen: s.length };
  }

  /** Peer scroll pattern: stick when distance-to-bottom < threshold */
  function shouldStickBottom(scrollHeight, scrollTop, clientHeight, threshold) {
    const th = threshold == null ? 48 : threshold;
    return scrollHeight - scrollTop - clientHeight < th;
  }

  /**
   * 流式滚动状态机（纯函数，供离线测试）。
   * 修 0.8.3：距底 <80 自动 stick=true 会把刚上滑的用户拽回。
   *
   * @param {object} s  { stick, userReading, progScroll }
   * @param {object} ev { type: 'wheel-up'|'scroll'|'resume'|'start'|'paint', gap? }
   * @returns {{ stick:boolean, userReading:boolean, allowPaintScroll:boolean }}
   */
  function resolveAiScrollState(s, ev) {
    let stick = !!s.stick;
    let userReading = !!s.userReading;
    const prog = !!s.progScroll;
    const type = (ev && ev.type) || "";
    const gap = ev && typeof ev.gap === "number" ? ev.gap : null;

    if (type === "start" || type === "resume") {
      stick = true;
      userReading = false;
    } else if (type === "wheel-up" || type === "detach") {
      stick = false;
      userReading = true;
    } else if (type === "scroll") {
      if (prog) {
        /* 程序化滚动：状态不变 */
      } else if (gap != null && gap > 24) {
        stick = false;
        userReading = true;
      } else if (gap != null && gap <= 12 && userReading) {
        // 用户自己滚回贴底才恢复
        stick = true;
        userReading = false;
      }
    }

    const allowPaintScroll = stick && !userReading;
    return { stick, userReading, allowPaintScroll };
  }

  /**
   * 在 marked 之前抽出数学公式，避免 `_` `^` `\` 被 Markdown 拆坏。
   * 支持：$$ $$ / \[ \] / \( \) / $ $ / ```math|latex|tex
   * 代码块内公式不抽取；纯数字 $12.5 不当作公式。
   * @returns {{ md: string, maths: Array<{tex:string, display:boolean}> }}
   */
  function prepareMarkdownMath(md) {
    const maths = [];
    let s = String(md || "");

    // ```math / latex / tex → 独立公式
    s = s.replace(/```(?:math|latex|tex)\s*\n([\s\S]*?)```/gi, (_, tex) => {
      const i = maths.length;
      maths.push({ tex: String(tex).trim(), display: true });
      return `\n\n@@BSBMATH${i}@@\n\n`;
    });

    // 保护其余 fenced / inline code
    const codes = [];
    s = s.replace(/```[\s\S]*?```/g, (m) => {
      const i = codes.length;
      codes.push(m);
      return `@@BSBCODE${i}@@`;
    });
    s = s.replace(/`[^`\n]+`/g, (m) => {
      const i = codes.length;
      codes.push(m);
      return `@@BSBCODE${i}@@`;
    });

    // 独立公式 $$...$$
    s = s.replace(/\$\$([\s\S]+?)\$\$/g, (_, tex) => {
      const i = maths.length;
      maths.push({ tex: String(tex).trim(), display: true });
      return `\n\n@@BSBMATH${i}@@\n\n`;
    });
    // \[...\]
    s = s.replace(/\\\[([\s\S]+?)\\\]/g, (_, tex) => {
      const i = maths.length;
      maths.push({ tex: String(tex).trim(), display: true });
      return `\n\n@@BSBMATH${i}@@\n\n`;
    });
    // \(...\)
    s = s.replace(/\\\(([\s\S]+?)\\\)/g, (_, tex) => {
      const i = maths.length;
      maths.push({ tex: String(tex).trim(), display: false });
      return `@@BSBMATH${i}@@`;
    });
    // 行内 $...$：手动扫描，避免“价格 $12.5，后文 $x^2$”跨段误配。
    // 无效起始符只消耗自身，让后一个 $ 仍可作为真正公式的起点。
    {
      const input = s;
      let out = "";
      let cursor = 0;
      const escapedAt = (idx) => {
        let slashes = 0;
        for (let j = idx - 1; j >= 0 && input[j] === "\\"; j--) slashes += 1;
        return slashes % 2 === 1;
      };
      while (cursor < input.length) {
        let open = input.indexOf("$", cursor);
        while (open >= 0 && escapedAt(open)) open = input.indexOf("$", open + 1);
        if (open < 0) {
          out += input.slice(cursor);
          break;
        }
        out += input.slice(cursor, open);
        let close = input.indexOf("$", open + 1);
        while (close >= 0 && escapedAt(close)) close = input.indexOf("$", close + 1);
        if (close < 0) {
          out += input.slice(open);
          break;
        }
        const raw = input.slice(open + 1, close);
        const t = raw.trim();
        const hasBoundarySpace = raw !== t;
        const pureNumber = /^[\d,]+(?:\.\d+)?$/.test(t);
        const mathSignal = /[A-Za-z\\_^{}=+*/<>|]|[α-ωΑ-Ω]/.test(t);
        const valid = !!t && !hasBoundarySpace && !pureNumber && mathSignal;
        if (!valid) {
          out += "$";
          cursor = open + 1;
          continue;
        }
        const i = maths.length;
        maths.push({ tex: t, display: false });
        out += `@@BSBMATH${i}@@`;
        cursor = close + 1;
      }
      s = out;
    }

    s = s.replace(/@@BSBCODE(\d+)@@/g, (_, id) => {
      const i = Number(id);
      return codes[i] != null ? codes[i] : "";
    });

    return { md: s, maths };
  }

  /**
   * 把 @@BSBMATHn@@ 换成 KaTeX HTML（或 fallback）。
   * renderToString(tex, display) 可选；失败则转义原文。
   */
  function replaceMathPlaceholders(html, maths, renderToString) {
    return String(html || "").replace(/@@BSBMATH(\d+)@@/g, (full, id) => {
      const m = maths[Number(id)];
      if (!m) return full;
      if (typeof renderToString === "function") {
        try {
          const out = renderToString(m.tex, !!m.display);
          if (out != null && out !== "") return out;
        } catch (_) {
          /* fall through */
        }
      }
      const esc = String(m.tex)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;");
      return m.display
        ? `<pre class="bsb-math-fallback">${esc}</pre>`
        : `<code class="bsb-math-fallback">${esc}</code>`;
    });
  }

  function parseSseDataLine(line) {
    const t = String(line || "").trim();
    if (!t || t.startsWith(":")) return { kind: "skip" };
    if (!t.startsWith("data:")) return { kind: "skip" };
    const data = t.slice(5).trim();
    if (!data) return { kind: "skip" };
    if (data === "[DONE]") return { kind: "done" };
    try {
      const j = JSON.parse(data);
      if (j.error) {
        return {
          kind: "error",
          message: j.error.message || JSON.stringify(j.error),
        };
      }
      const piece = extractFromChoice(j.choices && j.choices[0]);
      return { kind: "delta", ...piece };
    } catch (_) {
      return { kind: "skip" };
    }
  }
  // #endregion pure-logic

  let wbiCache = { img: null, sub: null, at: 0 };

  async function getWbiKeys() {
    const now = Date.now();
    if (wbiCache.img && now - wbiCache.at < WBI_TTL_MS) {
      return [wbiCache.img, wbiCache.sub];
    }
    const nav = await httpJson("https://api.bilibili.com/x/web-interface/nav");
    const wbi = (nav && nav.data && nav.data.wbi_img) || {};
    const imgUrl = wbi.img_url || "";
    const subUrl = wbi.sub_url || "";
    if (!imgUrl || !subUrl) throw new Error("failed to get wbi keys from /nav");
    const img = keyFromUrl(imgUrl);
    const sub = keyFromUrl(subUrl);
    wbiCache = { img, sub, at: now };
    return [img, sub];
  }

  // ─── HTTP ───────────────────────────────────────────────────────────────
  function httpJson(url, extraHeaders) {
    return new Promise((resolve, reject) => {
      GM_xmlhttpRequest({
        method: "GET",
        url,
        anonymous: false,
        timeout: 30000,
        headers: Object.assign(
          {
            Accept: "application/json, text/plain, */*",
            Referer: "https://www.bilibili.com/",
            Origin: "https://www.bilibili.com",
            "User-Agent": UA,
          },
          extraHeaders || {},
        ),
        onload(res) {
          if (res.status < 200 || res.status >= 300) {
            reject(
              new Error(
                `HTTP ${res.status} for ${url}: ${(res.responseText || "").slice(0, 200)}`,
              ),
            );
            return;
          }
          try {
            resolve(JSON.parse(res.responseText));
          } catch (e) {
            reject(
              new Error(
                `invalid JSON from ${url}: ${(res.responseText || "").slice(0, 200)}`,
              ),
            );
          }
        },
        onerror() {
          reject(new Error(`network error for ${url}`));
        },
        ontimeout() {
          reject(new Error(`timeout for ${url}`));
        },
      });
    });
  }

  /**
   * 当前视频快速链路：页面 fetch 优先，避免每次请求跨 userscript bridge。
   * CORS、登录态或 CSP 不允许时再回退 GM_xmlhttpRequest。
   */
  async function requestJsonFast(url, { signal, headers } = {}) {
    let fetchError = null;
    try {
      const fetchFn = pageWindow.fetch || window.fetch;
      const res = await fetchFn.call(pageWindow, url, {
        method: "GET",
        credentials: "include",
        cache: "no-store",
        signal,
        headers: Object.assign({ Accept: "application/json, text/plain, */*" }, headers || {}),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status} for ${url}`);
      return await res.json();
    } catch (error) {
      if (error?.name === "AbortError") throw error;
      fetchError = error;
    }

    if (signal?.aborted) throw new DOMException("操作已取消", "AbortError");
    try {
      return await httpJson(url, headers);
    } catch (gmError) {
      throw gmError || fetchError || new Error(`request failed: ${url}`);
    }
  }

  function formatSubtitleUrl(u) {
    if (!u) return "";
    u = String(u).trim();
    if (!u) return "";
    if (u.startsWith("//")) return "https:" + u;
    if (u.startsWith("http://")) return "https://" + u.slice(7);
    if (!u.startsWith("http")) return "https://" + u.replace(/^\/+/, "");
    return u;
  }

  // ─── util ───────────────────────────────────────────────────────────────
  function extractBvid(text) {
    if (!text) return "";
    text = String(text).trim();
    if (!text) return "";
    if (/^BV[\w]+$/i.test(text)) return "BV" + text.slice(2);
    const m = text.match(/BV[\w]+/i);
    return m ? "BV" + m[0].slice(2) : "";
  }

  function resolveCid(view, page = 1) {
    const pages = view.pages || [];
    if (Array.isArray(pages) && pages.length && page >= 1 && page <= pages.length) {
      const cid = pages[page - 1].cid;
      return cid != null ? Number(cid) : null;
    }
    if (view.cid == null) return null;
    const n = Number(view.cid);
    return Number.isFinite(n) ? n : null;
  }

  function pickTrack(subs) {
    if (!subs || !subs.length) return null;
    for (const s of subs) {
      const lan = String(s.lan || "");
      if (
        lan === "zh-CN" ||
        lan === "ai-zh" ||
        lan.startsWith("zh") ||
        lan.startsWith("ai")
      ) {
        return s;
      }
    }
    return subs[0];
  }

  function isChargeExclusiveBlocked(view) {
    return Boolean(view.is_upower_exclusive && !view.is_upower_play);
  }

  function toCues(body) {
    const out = [];
    (body || []).forEach((c, i) => {
      const fr = Number(c.from) || 0;
      const to = Number(c.to) || 0;
      let index = i + 1;
      if (c.sid != null) {
        const n = Number(c.sid);
        if (Number.isFinite(n)) index = n;
      }
      out.push({
        index,
        from: `${fr.toFixed(2)}s`,
        to: `${to.toFixed(2)}s`,
        from_sec: fr,
        to_sec: to,
        content: String(c.content || ""),
      });
    });
    return out;
  }

  function dedupeCues(cues) {
    const out = [];
    for (const cue of cues || []) {
      const content = String(cue?.content || "").replace(/\s+/g, " ").trim();
      if (!content) continue;
      const normalized = content.toLocaleLowerCase();
      const previous = out[out.length - 1];
      if (previous && previous._normalized === normalized) {
        const previousTo = Number(previous.to_sec ?? parseSeconds(previous.to));
        const currentFrom = Number(cue.from_sec ?? parseSeconds(cue.from));
        if (currentFrom - previousTo <= 1.25) {
          const nextTo = Math.max(previousTo, Number(cue.to_sec ?? parseSeconds(cue.to)));
          previous.to_sec = nextTo;
          previous.to = `${nextTo.toFixed(2)}s`;
          continue;
        }
      }
      out.push({ ...cue, content, _normalized: normalized });
    }
    return out.map(({ _normalized, ...cue }) => cue);
  }

  function parseSeconds(val) {
    if (val == null) return 0;
    if (typeof val === "number") return val;
    const s = String(val).trim().replace(/s$/i, "");
    const n = Number(s);
    return Number.isFinite(n) ? n : 0;
  }

  function formatSrtTimestamp(sec) {
    if (sec < 0) sec = 0;
    const totalMs = Math.round(sec * 1000);
    const h = Math.floor(totalMs / 3_600_000);
    const rem = totalMs % 3_600_000;
    const m = Math.floor(rem / 60_000);
    const rem2 = rem % 60_000;
    const s = Math.floor(rem2 / 1000);
    const ms = rem2 % 1000;
    return (
      String(h).padStart(2, "0") +
      ":" +
      String(m).padStart(2, "0") +
      ":" +
      String(s).padStart(2, "0") +
      "," +
      String(ms).padStart(3, "0")
    );
  }

  function cuesToSrt(cues) {
    const lines = [];
    let n = 0;
    for (const c of cues) {
      const text = String(c.content || "")
        .replace(/\r\n/g, "\n")
        .replace(/\r/g, "\n")
        .trim();
      if (!text) continue;
      n += 1;
      const fr = c.from_sec != null ? c.from_sec : c.from;
      const to = c.to_sec != null ? c.to_sec : c.to;
      lines.push(String(n));
      lines.push(
        `${formatSrtTimestamp(parseSeconds(fr))} --> ${formatSrtTimestamp(parseSeconds(to))}`,
      );
      lines.push(text);
      lines.push("");
    }
    return lines.join("\n");
  }

  function cuesToTxt(cues) {
    return cues
      .map((c) => String(c.content || "").trim())
      .filter(Boolean)
      .join("\n");
  }

  function formatClock(sec) {
    const total = Math.max(0, Math.floor(Number(sec) || 0));
    const h = Math.floor(total / 3600);
    const m = Math.floor((total % 3600) / 60);
    const s = total % 60;
    return h > 0
      ? `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`
      : `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
  }

  function cuesToAiText(cues, bvid, page) {
    const rows = [];
    let previous = "";
    for (const cue of cues || []) {
      const content = String(cue.content || "").replace(/\s+/g, " ").trim();
      if (!content || content === previous) continue;
      previous = content;
      const sec = cue.from_sec != null ? cue.from_sec : parseSeconds(cue.from);
      rows.push(`[${bvid || "BV"} P${Math.max(1, Number(page) || 1)} ${formatClock(sec)}] ${content}`);
    }
    return rows.join("\n");
  }

  function stripHtml(s) {
    return String(s || "")
      .replace(/<[^>]+>/g, "")
      .replace(/&nbsp;/g, " ")
      .replace(/&amp;/g, "&")
      .replace(/&lt;/g, "<")
      .replace(/&gt;/g, ">")
      .replace(/&quot;/g, '"')
      .replace(/&#39;/g, "'")
      .replace(/\s+/g, " ")
      .trim();
  }

  function safeFilename(name) {
    return (
      String(name || "subtitle")
        .replace(/[\\/:*?"<>|]+/g, "_")
        .replace(/\s+/g, " ")
        .trim()
        .slice(0, 120) || "subtitle"
    );
  }

  function downloadText(filename, text) {
    const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    a.style.display = "none";
    document.body.appendChild(a);
    a.click();
    setTimeout(() => {
      URL.revokeObjectURL(url);
      a.remove();
    }, 1500);
  }

  function sleep(ms) {
    return new Promise((r) => setTimeout(r, ms));
  }

  // ─── page context detection ─────────────────────────────────────────────
  /**
   * 自动识别。约定：
   * - 视频页默认 type=video（单个视频），不因多分P自动变 selection
   * - /list + sid → collection；/list/ml → favorite
   * - 合集列表页（space lists / collectiondetail）→ collection
   * - 返回字段可被「手动模式」复用（mid/sid/bvid…）
   */
  function detectContext(href) {
    let u;
    try {
      u = new URL(href || location.href);
    } catch (_) {
      return { type: "unknown", source: "auto" };
    }
    const host = u.hostname.toLowerCase();
    const path = u.pathname;
    const hints = extractPageHints(u);

    // search
    if (/^search\.bilibili\.com$/i.test(host)) {
      if (/^\/(all|video)\/?$/i.test(path)) {
        const keyword =
          (u.searchParams.get("keyword") || "").trim() || hints.keyword;
        if (keyword) {
          const allowed = new Set([
            "totalrank",
            "click",
            "pubdate",
            "dm",
            "stow",
            "scores",
          ]);
          const order = (u.searchParams.get("order") || "totalrank")
            .trim()
            .toLowerCase();
          const page = Math.max(
            1,
            parseInt(u.searchParams.get("page") || "1", 10) || 1,
          );
          return {
            type: "search",
            source: "auto",
            keyword,
            order: allowed.has(order) ? order : "totalrank",
            page,
          };
        }
      }
      return { type: "unknown", source: "auto" };
    }

    // space
    if (/^space\.bilibili\.com$/i.test(host)) {
      let m = path.match(/^\/(\d+)\/lists\/(\d+)\/?$/i);
      if (m) {
        return {
          type: "collection",
          source: "auto",
          mid: m[1],
          season_id: m[2],
        };
      }
      m = path.match(/^\/(\d+)\/channel\/collectiondetail\/?$/i);
      if (m) {
        const sid =
          u.searchParams.get("sid") ||
          u.searchParams.get("season_id") ||
          hints.season_id;
        if (sid && /^\d+$/.test(sid)) {
          return {
            type: "collection",
            source: "auto",
            mid: m[1],
            season_id: String(sid),
          };
        }
      }
      // series / seasons 列表入口
      m = path.match(/^\/(\d+)\/channel\/seriesdetail\/?$/i);
      if (m) {
        const sid =
          u.searchParams.get("sid") ||
          u.searchParams.get("season_id") ||
          hints.season_id;
        if (sid && /^\d+$/.test(sid)) {
          return {
            type: "collection",
            source: "auto",
            mid: m[1],
            season_id: String(sid),
          };
        }
      }
      const fid = (u.searchParams.get("fid") || hints.media_id || "").trim();
      if (fid && /^\d+$/.test(fid) && /\/favlist\/?$/i.test(path)) {
        return { type: "favorite", source: "auto", media_id: fid };
      }
      m = path.match(/^\/(\d+)/);
      if (m) {
        const segs = path.split("/").filter(Boolean);
        const mid = m[1];
        if (
          segs.length === 1 ||
          (segs.length === 2 && /^(video|upload|dynamic|favlist)?$/i.test(segs[1])) ||
          (segs.length === 3 && segs[1] === "upload" && segs[2] === "video")
        ) {
          // favlist without fid still unknown for fav; treat as user
          if (segs[1] && /^favlist$/i.test(segs[1]) && !fid) {
            return { type: "user", source: "auto", mid, note: "favlist_no_fid" };
          }
          return { type: "user", source: "auto", mid };
        }
        // other space tabs: still expose mid for manual switch
        return {
          type: "user",
          source: "auto",
          mid,
          note: "space_tab",
          bvid: hints.bvid || undefined,
        };
      }
      return { type: "unknown", source: "auto", ...pickHintIds(hints) };
    }

    // www.bilibili.com
    if (/^(www\.)?bilibili\.com$/i.test(host)) {
      let m = path.match(/^\/medialist\/(?:detail|play)\/ml(\d+)\/?$/i);
      if (m) return { type: "favorite", source: "auto", media_id: m[1] };

      // /list/ml{id} 收藏夹播放/详情
      m = path.match(/^\/list\/ml(\d+)\/?/i);
      if (m) return { type: "favorite", source: "auto", media_id: m[1] };

      // /list/{mid}?sid= 合集播放页（常见误判为单视频）
      m = path.match(/^\/list\/(\d+)\/?/i);
      if (m) {
        const mid = m[1];
        const sid =
          u.searchParams.get("sid") ||
          u.searchParams.get("season_id") ||
          hints.season_id;
        const bvid =
          extractBvid(href) ||
          extractBvid(u.searchParams.get("bvid") || "") ||
          hints.bvid;
        if (sid && /^\d+$/.test(String(sid))) {
          return {
            type: "collection",
            source: "auto",
            mid,
            season_id: String(sid),
            bvid: bvid || undefined,
            page: Math.max(1, parseInt(u.searchParams.get("p") || "1", 10) || 1),
          };
        }
        // 无 sid 时：若有 BV 默认单视频，但带 mid 便于手动切合集
        if (bvid) {
          return {
            type: "video",
            source: "auto",
            bvid,
            mid,
            page: Math.max(1, parseInt(u.searchParams.get("p") || "1", 10) || 1),
            note: "list_without_sid",
          };
        }
        return { type: "user", source: "auto", mid, note: "list_mid_only" };
      }

      m = path.match(/^\/(?:fav|list)\/(?:ml)?(\d+)\/?$/i);
      if (m && !path.startsWith("/list/")) {
        return { type: "favorite", source: "auto", media_id: m[1] };
      }
      if (/^\/favlist\/?$/i.test(path)) {
        const fid = (u.searchParams.get("fid") || hints.media_id || "").trim();
        if (fid && /^\d+$/.test(fid)) {
          return { type: "favorite", source: "auto", media_id: fid };
        }
      }

      // 普通视频页：默认「单个视频」（不自动变选集）
      const bvid =
        extractBvid(path) || extractBvid(href) || hints.bvid;
      if (bvid && (/\/video\//i.test(path) || hints.fromVideoPath)) {
        const p = Math.max(1, parseInt(u.searchParams.get("p") || "1", 10) || 1);
        const ctx = {
          type: "video",
          source: "auto",
          bvid,
          page: p,
        };
        // 若页内能挖到合集信息，挂上供手动切换
        if (hints.mid && hints.season_id) {
          ctx.mid = hints.mid;
          ctx.season_id = hints.season_id;
          ctx.note = "video_has_ugc_season";
        }
        return ctx;
      }

      // 其它 list 形态
      if (/\/list\//i.test(path)) {
        const bvid2 =
          extractBvid(href) ||
          extractBvid(u.searchParams.get("bvid") || "") ||
          hints.bvid;
        if (bvid2) {
          return {
            type: "video",
            source: "auto",
            bvid: bvid2,
            page: Math.max(1, parseInt(u.searchParams.get("p") || "1", 10) || 1),
            mid: hints.mid || undefined,
            season_id: hints.season_id || undefined,
            note: "list_fallback_video",
          };
        }
      }
    }

    // DOM 兜底
    if (hints.bvid) {
      return {
        type: "video",
        source: "auto",
        bvid: hints.bvid,
        page: 1,
        mid: hints.mid || undefined,
        season_id: hints.season_id || undefined,
        note: "dom_bvid",
      };
    }
    return { type: "unknown", source: "auto", ...pickHintIds(hints) };
  }

  function pickHintIds(hints) {
    const o = {};
    if (hints.mid) o.mid = hints.mid;
    if (hints.season_id) o.season_id = hints.season_id;
    if (hints.media_id) o.media_id = hints.media_id;
    if (hints.bvid) o.bvid = hints.bvid;
    if (hints.keyword) o.keyword = hints.keyword;
    return o;
  }

  /** 从 URL / 页面链接尽量挖 mid、season_id、media_id、bvid */
  function extractPageHints(u) {
    const hints = {
      bvid: "",
      mid: "",
      season_id: "",
      media_id: "",
      keyword: "",
      fromVideoPath: false,
    };
    try {
      if (!u) u = new URL(location.href);
    } catch (_) {
      return hints;
    }
    hints.bvid =
      extractBvid(u.href) ||
      extractBvid(u.searchParams.get("bvid") || "") ||
      "";
    hints.keyword = (u.searchParams.get("keyword") || "").trim();
    const sid =
      u.searchParams.get("sid") ||
      u.searchParams.get("season_id") ||
      u.searchParams.get("business_id") ||
      "";
    if (sid && /^\d+$/.test(String(sid))) hints.season_id = String(sid);
    const fid = u.searchParams.get("fid") || "";
    if (fid && /^\d+$/.test(fid)) hints.media_id = fid;
    if (/\/video\//i.test(u.pathname)) hints.fromVideoPath = true;

    // path mid
    let m = u.pathname.match(/space\.bilibili\.com\/(\d+)/i);
    if (!m) m = String(location.href).match(/space\.bilibili\.com\/(\d+)/i);
    if (m) hints.mid = m[1];
    m = u.pathname.match(/^\/list\/(\d+)/i);
    if (m) hints.mid = m[1];
    m = u.pathname.match(/^\/(\d+)(?:\/|$)/);
    if (m && /space\.bilibili\.com/i.test(u.hostname)) hints.mid = m[1];

    // DOM: 合集 / 列表链接
    try {
      const anchors = document.querySelectorAll(
        'a[href*="lists/"], a[href*="collectiondetail"], a[href*="season_id"], a[href*="sid="], a[href*="/list/"]',
      );
      for (const a of anchors) {
        const href = a.getAttribute("href") || a.href || "";
        const lm = href.match(/\/(\d+)\/lists\/(\d+)/);
        if (lm) {
          if (!hints.mid) hints.mid = lm[1];
          if (!hints.season_id) hints.season_id = lm[2];
          break;
        }
        try {
          const au = new URL(href, location.origin);
          const sm =
            au.searchParams.get("sid") || au.searchParams.get("season_id");
          if (sm && /^\d+$/.test(sm)) {
            if (!hints.season_id) hints.season_id = sm;
            const mm = au.pathname.match(/\/(\d+)/);
            if (mm && !hints.mid) hints.mid = mm[1];
          }
          const listMid = au.pathname.match(/\/list\/(\d+)/i);
          if (listMid && !hints.mid) hints.mid = listMid[1];
        } catch (_) {
          /* ignore */
        }
      }
      if (!hints.bvid) {
        const b = extractBvid(
          document.querySelector('meta[itemprop="url"]')?.content || "",
        );
        if (b) hints.bvid = b;
      }
      if (!hints.bvid) {
        const og = document.querySelector('meta[property="og:url"]')?.content;
        if (og) hints.bvid = extractBvid(og);
      }
    } catch (_) {
      /* ignore */
    }
    return hints;
  }

  /**
   * 根据模式选择器 + 自动识别 得到最终扫描上下文。
   * mode=auto → 用 autoCtx；manual → 强制 type，参数从 auto/hints 填。
   */
  function resolveContext() {
    const root = document.getElementById(PANEL_ID);
    const modeSel = root?.querySelector('[data-role="mode"]');
    const mode = (modeSel?.value || state.mode || "auto").trim();
    state.mode = MODE_OPTIONS.includes(mode) ? mode : "auto";

    const auto = detectContext(location.href);
    state.autoCtx = auto;

    if (state.mode === "auto") {
      return { ...auto, source: "auto" };
    }

    const type = state.mode;
    const hints = extractPageHints();
    const base = { ...auto, ...pickHintIds(hints), type, source: "manual" };

    if (type === "video" || type === "selection") {
      base.bvid =
        auto.bvid ||
        hints.bvid ||
        extractBvid(location.href) ||
        "";
      base.page =
        auto.page ||
        Math.max(
          1,
          parseInt(new URL(location.href).searchParams.get("p") || "1", 10) || 1,
        );
    }
    if (type === "user") {
      base.mid = auto.mid || hints.mid || "";
    }
    if (type === "collection") {
      base.mid = auto.mid || hints.mid || "";
      base.season_id = auto.season_id || hints.season_id || "";
    }
    if (type === "favorite") {
      base.media_id = auto.media_id || hints.media_id || "";
    }
    if (type === "search") {
      base.keyword = auto.keyword || hints.keyword || "";
      base.order = auto.order || "totalrank";
      base.page = auto.page || 1;
    }
    return base;
  }

  function formatCtxBits(ctx) {
    const bits = [];
    if (ctx.bvid) bits.push(ctx.bvid);
    if (ctx.mid) bits.push(`mid=${ctx.mid}`);
    if (ctx.season_id) bits.push(`season=${ctx.season_id}`);
    if (ctx.media_id) bits.push(`fid=${ctx.media_id}`);
    if (ctx.keyword) bits.push(`「${ctx.keyword}」`);
    if (ctx.order && ctx.type === "search") bits.push(`order=${ctx.order}`);
    if (ctx.page && (ctx.type === "video" || ctx.type === "selection")) {
      bits.push(`p=${ctx.page}`);
    }
    if (ctx.note === "video_has_ugc_season") bits.push("页内含合集信息");
    if (ctx.note === "list_without_sid") bits.push("list页无sid");
    return bits.join(" · ") || location.href.slice(0, 80);
  }

  // ─── subtitle fetch (client.py) ─────────────────────────────────────────
  async function viewDetail(bvid) {
    const [img, sub] = await getWbiKeys();
    const q = encWbi({ bvid, need_elec: 0 }, img, sub);
    return httpJson(
      `https://api.bilibili.com/x/web-interface/wbi/view/detail?${q}`,
    );
  }

  async function playerWbiV2(aid, cid, bvid) {
    const [img, sub] = await getWbiKeys();
    const params = aid ? { aid, cid } : { bvid, cid };
    const q = encWbi(params, img, sub);
    return httpJson(`https://api.bilibili.com/x/player/wbi/v2?${q}`);
  }

  async function dmViewSubs(cid, bvid) {
    const dm = await httpJson(
      `https://api.bilibili.com/x/v2/dm/view?oid=${cid}&type=1&bvid=${bvid}`,
    );
    if (dm.code !== 0) return [];
    return (
      (dm.data && dm.data.subtitle && dm.data.subtitle.subtitles) || []
    ).slice();
  }

  async function aiSubtitleStat(aid, cid) {
    const data = await httpJson(
      `https://api.bilibili.com/x/player/v2/ai/subtitle/search/stat?aid=${aid}&cid=${cid}`,
    );
    if (data.code === 0 && data.data && data.data.subtitle_url) {
      return formatSubtitleUrl(data.data.subtitle_url);
    }
    return "";
  }

  async function collectTracks(aid, cid, bvid) {
    try {
      const player = await playerWbiV2(aid, cid, bvid);
      if (player.code === 0) {
        const subs = (
          (player.data && player.data.subtitle && player.data.subtitle.subtitles) ||
          []
        ).slice();
        if (subs.length) return { subs, source: "player_wbi" };
      }
    } catch (_) {
      /* fallthrough */
    }
    try {
      const subs = await dmViewSubs(cid, bvid);
      if (subs.length) return { subs, source: "dm_view" };
    } catch (_) {
      /* fallthrough */
    }
    return { subs: [], source: "" };
  }

  async function resolveUrl(track, aid, cid, source) {
    const lan = String(track.lan || "");
    let url = formatSubtitleUrl(track.subtitle_url || "");
    if (!url && lan.startsWith("ai-") && aid) {
      try {
        url = await aiSubtitleStat(aid, cid);
        if (url) return { url, source: "ai_stat" };
      } catch (_) {
        /* ignore */
      }
    }
    return { url, source };
  }

  function currentPageNumber() {
    try {
      return Math.max(1, parseInt(new URL(location.href).searchParams.get("p") || "1", 10) || 1);
    } catch (_) {
      return 1;
    }
  }

  function routeVideoKey(bvid, page) {
    return `${String(bvid || "").toUpperCase()}:P${Math.max(1, Number(page) || 1)}`;
  }

  function runtimeVideoView(bvid) {
    const key = String(bvid || "").toUpperCase();
    const candidates = [
      pageWindow.__INITIAL_STATE__?.videoData,
      pageWindow.__INITIAL_STATE__?.videoInfo,
      pageWindow.__INITIAL_STATE__?.epInfo,
    ];
    for (const view of candidates) {
      if (view && String(view.bvid || "").toUpperCase() === key && (view.cid || view.pages?.length)) {
        return view;
      }
    }
    return null;
  }

  function runtimeSubtitleTracks(meta) {
    const roots = [pageWindow.__playinfo__, pageWindow.__PLAYINFO__, pageWindow.__PLAYER_CONFIG__];
    for (let root of roots) {
      if (!root) continue;
      if (typeof root === "string") {
        try { root = JSON.parse(root); } catch (_) { continue; }
      }
      const runtimeCid = Number(root?.data?.cid || root?.cid || pageWindow.__INITIAL_STATE__?.videoData?.cid || 0);
      if (runtimeCid && meta?.cid && runtimeCid !== Number(meta.cid)) continue;
      const candidates = [
        root?.data?.subtitle?.subtitles,
        root?.subtitle?.subtitles,
        root?.data?.data?.subtitle?.subtitles,
      ];
      for (const tracks of candidates) {
        if (!Array.isArray(tracks) || !tracks.length) continue;
        return tracks.map((track) => ({
          ...track,
          subtitle_url: formatSubtitleUrl(track.subtitle_url || ""),
        }));
      }
    }
    return null;
  }

  function openCacheDatabase() {
    if (state.cacheDbPromise) return state.cacheDbPromise;
    state.cacheDbPromise = new Promise((resolve, reject) => {
      if (!globalThis.indexedDB) return reject(new Error("IndexedDB unavailable"));
      const req = indexedDB.open(CACHE_DB_NAME, CACHE_DB_VERSION);
      req.onupgradeneeded = () => {
        const db = req.result;
        if (!db.objectStoreNames.contains(CACHE_STORE_NAME)) {
          db.createObjectStore(CACHE_STORE_NAME, { keyPath: "key" });
        }
      };
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error || new Error("cache database open failed"));
    }).catch((error) => {
      state.cacheDbPromise = null;
      throw error;
    });
    return state.cacheDbPromise;
  }

  async function persistentCacheRead(key) {
    try {
      const db = await openCacheDatabase();
      const record = await new Promise((resolve, reject) => {
        const tx = db.transaction(CACHE_STORE_NAME, "readonly");
        const req = tx.objectStore(CACHE_STORE_NAME).get(key);
        req.onsuccess = () => resolve(req.result || null);
        req.onerror = () => reject(req.error);
      });
      if (!record) return null;
      if (Number(record.expiresAt || 0) < Date.now()) {
        persistentCacheDelete(key).catch(() => {});
        return null;
      }
      return record;
    } catch (_) {
      return null;
    }
  }

  async function persistentCacheWrite(key, value, ttlMs = SUBTITLE_CACHE_TTL_MS) {
    const record = {
      key,
      value,
      updatedAt: Date.now(),
      expiresAt: Date.now() + ttlMs,
    };
    try {
      const db = await openCacheDatabase();
      await new Promise((resolve, reject) => {
        const tx = db.transaction(CACHE_STORE_NAME, "readwrite");
        tx.objectStore(CACHE_STORE_NAME).put(record);
        tx.oncomplete = resolve;
        tx.onerror = () => reject(tx.error);
      });
      state.cacheChannel?.postMessage({ type: "cache-updated", key, updatedAt: record.updatedAt });
    } catch (_) {
      /* private mode / quota: memory cache remains available */
    }
    return record;
  }

  async function persistentCacheDelete(key) {
    try {
      const db = await openCacheDatabase();
      await new Promise((resolve, reject) => {
        const tx = db.transaction(CACHE_STORE_NAME, "readwrite");
        tx.objectStore(CACHE_STORE_NAME).delete(key);
        tx.oncomplete = resolve;
        tx.onerror = () => reject(tx.error);
      });
    } catch (_) { /* ignore */ }
  }

  function initCacheChannel() {
    if (state.cacheChannel || typeof BroadcastChannel === "undefined") return;
    try {
      state.cacheChannel = new BroadcastChannel(CACHE_CHANNEL_NAME);
      state.cacheChannel.addEventListener("message", (event) => {
        const msg = event.data;
        if (!msg || msg.type !== "cache-updated" || !String(msg.key).startsWith("subtitle:")) return;
        const shortKey = String(msg.key).slice("subtitle:".length);
        state.fastSubtitleCache.delete(shortKey);
      });
    } catch (_) { /* optional */ }
  }

  async function fetchVideoViewFast(bvid, signal, { forceNetwork = false } = {}) {
    const key = String(bvid || "").toUpperCase();

    if (!forceNetwork) {
      const runtime = runtimeVideoView(key);
      if (runtime) {
        lruSet(state.fastViewCache, key, runtime);
        sessionCacheWrite("view", key, runtime);
        return { value: runtime, cacheLevel: "L0 页面态" };
      }
      const memory = lruGet(state.fastViewCache, key);
      if (memory) return { value: memory, cacheLevel: "L1 内存" };
      const session = sessionCacheRead("view", key, VIEW_CACHE_TTL_MS);
      if (session) {
        lruSet(state.fastViewCache, key, session);
        return { value: session, cacheLevel: "L2 会话" };
      }
    }

    const payload = await requestJsonFast(
      `https://api.bilibili.com/x/web-interface/view?bvid=${encodeURIComponent(bvid)}`,
      { signal },
    );
    if (payload?.code !== 0 || !payload?.data) {
      throw new Error(payload?.message || "视频信息接口返回失败");
    }
    lruSet(state.fastViewCache, key, payload.data);
    sessionCacheWrite("view", key, payload.data);
    return { value: payload.data, cacheLevel: "NET 视频详情" };
  }

  async function fetchSubtitleTracksFast(meta, signal, { forceNetwork = false } = {}) {
    const cacheKey = `${meta.bvid}:${meta.cid}`;
    if (!forceNetwork) {
      const runtime = runtimeSubtitleTracks(meta);
      if (runtime) {
        lruSet(state.fastTrackCache, cacheKey, runtime);
        sessionCacheWrite("tracks", cacheKey, runtime);
        return { tracks: runtime, cacheLevel: "L0 播放器态" };
      }
      const memory = lruGet(state.fastTrackCache, cacheKey);
      if (memory) return { tracks: memory, cacheLevel: "L1 内存" };
      const session = sessionCacheRead("tracks", cacheKey, TRACK_CACHE_TTL_MS);
      if (session) {
        lruSet(state.fastTrackCache, cacheKey, session);
        return { tracks: session, cacheLevel: "L2 会话" };
      }
    }

    const params = new URLSearchParams({ bvid: meta.bvid, cid: String(meta.cid) });
    if (meta.aid) params.set("aid", String(meta.aid));
    const endpoints = [
      `https://api.bilibili.com/x/player/wbi/v2?${params}`,
      `https://api.bilibili.com/x/player/v2?${params}`,
    ];
    let lastError = null;
    for (const endpoint of endpoints) {
      try {
        const payload = await requestJsonFast(endpoint, { signal });
        if (payload?.code === 0) {
          const tracks = payload?.data?.subtitle?.subtitles;
          if (Array.isArray(tracks)) {
            const normalized = tracks.map((track) => ({
              ...track,
              subtitle_url: formatSubtitleUrl(track.subtitle_url || ""),
            }));
            lruSet(state.fastTrackCache, cacheKey, normalized);
            sessionCacheWrite("tracks", cacheKey, normalized);
            return { tracks: normalized, cacheLevel: "NET 字幕轨道" };
          }
        }
        lastError = new Error(payload?.message || "字幕轨道接口返回失败");
      } catch (error) {
        if (error?.name === "AbortError") throw error;
        lastError = error;
      }
    }
    throw lastError || new Error("无法取得字幕轨道");
  }

  function preferredTrackIndex(tracks) {
    const chosen = pickTrack(tracks);
    const index = tracks.indexOf(chosen);
    return index >= 0 ? index : 0;
  }

  async function fetchTrackBodyFast(base, tracks, trackIndex, signal, { forceNetwork = false } = {}) {
    const track = tracks[trackIndex];
    if (!track) throw new Error("字幕轨道不存在");
    const langKey = String(track.lan || trackIndex || "default");
    const cacheKey = `${base.bvid}:${base.cid}:${langKey}`;

    if (!forceNetwork) {
      const memory = lruGet(state.fastTrackBodyCache, cacheKey);
      if (memory) return { ...memory, source: "L1 内存字幕", cacheLevel: "L1 内存" };
      const persistent = await persistentCacheRead(`subtitle-track:${cacheKey}`);
      if (persistent?.value) {
        const result = { ...persistent.value, cacheStale: Date.now() - persistent.updatedAt > SUBTITLE_REVALIDATE_MS };
        lruSet(state.fastTrackBodyCache, cacheKey, result);
        return { ...result, source: "L3 持久字幕", cacheLevel: "L3 IndexedDB" };
      }
    }

    const lan = String(track.lan || "");
    let url = formatSubtitleUrl(track.subtitle_url || "");
    if (!url && lan.startsWith("ai-") && base.aid) {
      url = await aiSubtitleStat(base.aid, base.cid);
    }
    if (!url) return { ...base, status: "empty", lan, tracks, activeTrackIndex: trackIndex, source: "NET 无地址" };

    const bodyJson = await requestJsonFast(url, { signal });
    const body = bodyJson && typeof bodyJson === "object" ? bodyJson.body : null;
    if (!Array.isArray(body) || !body.length) {
      return { ...base, status: "empty", lan, tracks, activeTrackIndex: trackIndex, source: "NET 空字幕" };
    }
    const cues = dedupeCues(toCues(body));
    const result = {
      ...base,
      status: "ok",
      cue_count: cues.length,
      lan,
      lan_doc: track.lan_doc || lan,
      data: cues,
      tracks,
      activeTrackIndex: trackIndex,
      source: "NET 直读",
      cacheLevel: "NET",
      cacheStale: false,
    };
    lruSet(state.fastTrackBodyCache, cacheKey, result);
    persistentCacheWrite(`subtitle-track:${cacheKey}`, result).catch(() => {});
    return result;
  }

  async function fetchCurrentSubtitleFast(bvid, page = 1, signal, { forceNetwork = false } = {}) {
    bvid = extractBvid(bvid) || String(bvid || "").trim();
    if (!bvid) throw new Error("empty bvid");

    const viewHit = await fetchVideoViewFast(bvid, signal, { forceNetwork });
    const view = viewHit.value;
    if (isChargeExclusiveBlocked(view)) {
      return { bvid, status: "empty", error: "charge_exclusive_blocked", source: viewHit.cacheLevel };
    }

    const pages = Array.isArray(view.pages) ? view.pages : [];
    const pageNo = Math.max(1, Math.min(Number(page) || 1, Math.max(1, pages.length)));
    const part = pages[pageNo - 1] || null;
    const cid = Number(part?.cid || view.cid) || null;
    const aid = Number(view.aid) || null;
    const title = part?.part && pages.length > 1
      ? `${view.title || bvid} - P${pageNo}【${part.part}】`
      : String(view.title || bvid);
    const author = String(view.owner?.name || "");
    const base = { bvid: view.bvid || bvid, aid, cid, title, author, pages, page: pageNo };
    if (!cid) return { ...base, status: "error", error: "no cid", source: viewHit.cacheLevel };

    const preferredKey = `${base.bvid}:${cid}`;
    if (!forceNetwork) {
      const memory = lruGet(state.fastSubtitleCache, preferredKey);
      if (memory) return { ...memory, source: "L1 内存字幕", cacheLevel: "L1 内存" };
      const persistent = await persistentCacheRead(`subtitle:${preferredKey}`);
      if (persistent?.value) {
        const result = {
          ...persistent.value,
          source: "L3 持久字幕",
          cacheLevel: "L3 IndexedDB",
          cacheStale: Date.now() - persistent.updatedAt > SUBTITLE_REVALIDATE_MS,
        };
        lruSet(state.fastSubtitleCache, preferredKey, result);
        return result;
      }
    }

    const trackHit = await fetchSubtitleTracksFast(base, signal, { forceNetwork });
    const tracks = trackHit.tracks;
    if (!tracks.length) return { ...base, status: "empty", tracks: [], source: trackHit.cacheLevel };
    const activeTrackIndex = preferredTrackIndex(tracks);
    const result = await fetchTrackBodyFast(base, tracks, activeTrackIndex, signal, { forceNetwork });
    const finalResult = {
      ...result,
      cachePath: `${viewHit.cacheLevel} → ${trackHit.cacheLevel} → ${result.cacheLevel || result.source}`,
    };
    if (finalResult.status === "ok") {
      lruSet(state.fastSubtitleCache, preferredKey, finalResult);
      persistentCacheWrite(`subtitle:${preferredKey}`, finalResult).catch(() => {});
    }
    return finalResult;
  }

  async function fetchSubtitle(bvid, page = 1) {
    bvid = extractBvid(bvid) || String(bvid || "").trim();
    if (!bvid) return { bvid: "", status: "error", error: "empty bvid" };

    let detail;
    try {
      detail = await viewDetail(bvid);
    } catch (e) {
      return { bvid, status: "error", error: `view/detail: ${e.message || e}` };
    }
    if (detail.code !== 0) {
      return {
        bvid,
        status: "error",
        error: `view/detail code=${detail.code} ${detail.message || ""}`,
      };
    }

    const view = (detail.data && detail.data.View) || {};
    if (isChargeExclusiveBlocked(view)) {
      return { bvid, status: "empty", error: "charge_exclusive_blocked" };
    }

    let aid = view.aid || 0;
    aid = Number(aid) || null;
    const cid = resolveCid(view, page);
    const title = String(view.title || "");
    const author = String((view.owner && view.owner.name) || "");
    const pages = Array.isArray(view.pages) ? view.pages : [];
    const base = { bvid, aid, cid, title, author, pages, page };

    if (cid == null) return { ...base, status: "error", error: "no cid" };

    const { subs, source: src0 } = await collectTracks(aid, cid, bvid);
    if (!subs.length) return { ...base, status: "empty" };

    const track = pickTrack(subs);
    if (!track) return { ...base, status: "empty" };

    const lan = String(track.lan || "");
    const { url, source } = await resolveUrl(track, aid, cid, src0);
    if (!url) return { ...base, status: "empty", lan };

    let bodyJson;
    try {
      bodyJson = await httpJson(url);
    } catch (e) {
      return {
        ...base,
        status: "error",
        lan,
        error: `subtitle body: ${e.message || e}`,
      };
    }

    const body = bodyJson && typeof bodyJson === "object" ? bodyJson.body : null;
    if (!Array.isArray(body) || !body.length) {
      return { ...base, status: "empty", lan };
    }

    const cues = toCues(body);
    return {
      ...base,
      status: "ok",
      cue_count: cues.length,
      lan,
      data: cues,
      source,
    };
  }

  // ─── list sources ───────────────────────────────────────────────────────
  /** @returns {Promise<{items: Array, hasMore: boolean, meta?: object}>} */
  async function fetchListPage(ctx, page, pageSize) {
    if (ctx.type === "user") {
      const [img, sub] = await getWbiKeys();
      const q = encWbi(
        {
          mid: ctx.mid,
          pn: page,
          ps: pageSize,
          tid: 0,
          keyword: "",
          order: "pubdate",
          web_location: 1550101,
          order_avoided: true,
        },
        img,
        sub,
      );
      const result = await httpJson(
        `https://api.bilibili.com/x/space/wbi/arc/search?${q}`,
        { Referer: `https://space.bilibili.com/${ctx.mid}`, Origin: "https://space.bilibili.com" },
      );
      if (result.code !== 0) {
        throw new Error(result.message || `user list code=${result.code}`);
      }
      const vlist = (result.data && result.data.list && result.data.list.vlist) || [];
      const pageInfo = (result.data && result.data.page) || {};
      const items = vlist.map((v) => ({
        bvid: v.bvid,
        aid: v.aid,
        title: v.title || "",
        author: v.author || "",
        page: 1,
      }));
      const hasMore =
        pageInfo.pn && pageInfo.ps && pageInfo.count != null
          ? pageInfo.pn * pageInfo.ps < pageInfo.count
          : vlist.length >= pageSize;
      return { items, hasMore, meta: { count: pageInfo.count } };
    }

    if (ctx.type === "favorite") {
      const result = await httpJson(
        `https://api.bilibili.com/x/v3/fav/resource/list?media_id=${ctx.media_id}&pn=${page}&ps=${Math.min(pageSize, 20)}&platform=web&t=${Date.now()}`,
        { Referer: "https://space.bilibili.com/", Origin: "https://www.bilibili.com" },
      );
      if (result.code !== 0) {
        throw new Error(result.message || `fav list code=${result.code}`);
      }
      const medias = (result.data && result.data.medias) || [];
      const items = medias
        .filter((v) => v && (v.type === 2 || v.bvid || v.bv_id)) // type 2 = video
        .map((v) => ({
          bvid: v.bvid || v.bv_id || "",
          aid: v.id || v.aid || 0,
          title: v.title || "",
          author: (v.upper && v.upper.name) || v.author || "",
          page: 1,
        }))
        .filter((v) => v.bvid);
      return {
        items,
        hasMore: result.data && result.data.has_more === true,
        meta: { title: (result.data && result.data.info && result.data.info.title) || "" },
      };
    }

    if (ctx.type === "collection") {
      const result = await httpJson(
        `https://api.bilibili.com/x/polymer/web-space/seasons_archives_list?mid=${ctx.mid}&season_id=${ctx.season_id}&sort_reverse=false&page_num=${page}&page_size=${pageSize}&web_location=333.1387`,
        {
          Referer: `https://space.bilibili.com/${ctx.mid}/channel/collectiondetail?sid=${ctx.season_id}`,
          Origin: "https://space.bilibili.com",
        },
      );
      if (result.code !== 0) {
        throw new Error(result.message || `collection code=${result.code}`);
      }
      const archives = (result.data && result.data.archives) || [];
      const pageInfo = (result.data && result.data.page) || {};
      const items = archives.map((v) => ({
        bvid: v.bvid,
        aid: v.aid,
        title: v.title || "",
        author: "",
        page: 1,
      }));
      let hasMore = false;
      if (pageInfo.total != null) {
        hasMore = page * pageSize < pageInfo.total;
      } else {
        hasMore = archives.length >= pageSize;
      }
      return {
        items,
        hasMore,
        meta: {
          total: pageInfo.total,
          name:
            (result.data && result.data.meta && result.data.meta.name) ||
            (result.data && result.data.meta && result.data.meta.title) ||
            "",
        },
      };
    }

    if (ctx.type === "search") {
      const [img, sub] = await getWbiKeys();
      const q = encWbi(
        {
          search_type: "video",
          keyword: ctx.keyword,
          order: ctx.order || "totalrank",
          page,
          page_size: 42,
        },
        img,
        sub,
      );
      const result = await httpJson(
        `https://api.bilibili.com/x/web-interface/wbi/search/type?${q}`,
        { Referer: "https://search.bilibili.com/", Origin: "https://search.bilibili.com" },
      );
      if (result.code !== 0) {
        throw new Error(result.message || `search code=${result.code}`);
      }
      const rows = (result.data && result.data.result) || [];
      const items = rows
        .map((v) => ({
          bvid: v.bvid || extractBvid(v.arcurl || ""),
          aid: v.aid || v.id || 0,
          title: stripHtml(v.title) || "未知标题",
          author: stripHtml(v.author || v.up_name) || "",
          page: 1,
        }))
        .filter((v) => v.bvid);
      const numPages = (result.data && result.data.numPages) || 0;
      const hasMore = numPages ? page < numPages : items.length >= 42;
      return {
        items,
        hasMore,
        meta: {
          numResults: (result.data && result.data.numResults) || 0,
          keyword: ctx.keyword,
        },
      };
    }

    throw new Error(`unsupported list type: ${ctx.type}`);
  }

  async function loadAllListItems(ctx, { maxPages, pageSize, onProgress, delayMs }) {
    const all = [];
    const seen = new Set();
    let page = 1;
    let hasMore = true;
    let meta = {};
    while (hasMore && page <= maxPages) {
      if (onProgress) onProgress(`拉取列表 ${page}/${maxPages}…（已 ${all.length}）`);
      const res = await fetchListPage(ctx, page, pageSize);
      meta = Object.assign(meta, res.meta || {});
      for (const it of res.items) {
        const key = it.bvid + "#" + (it.page || 1);
        if (!it.bvid || seen.has(key)) continue;
        seen.add(key);
        all.push(it);
      }
      hasMore = res.hasMore;
      if (!hasMore) break;
      page += 1;
      if (hasMore && page <= maxPages) await sleep(delayMs || 300);
    }
    return { items: all, meta, pagesFetched: Math.min(page, maxPages), truncated: hasMore };
  }

  async function loadVideoAsItems(bvid, expandAllParts) {
    // page=1 仅用于拿 View/pages 元信息；字幕在批量阶段再按 page 拉
    const r = await fetchSubtitle(bvid, 1);
    if (r.status === "error" && !r.pages?.length && !r.title) {
      throw new Error(r.error || "无法获取视频信息");
    }
    const pages = r.pages || [];
    if (expandAllParts && pages.length > 1) {
      return {
        items: pages.map((p, i) => ({
          bvid: r.bvid || bvid,
          aid: r.aid,
          title: `${r.title || bvid} - P${i + 1}【${p.part || ""}】`,
          author: r.author || "",
          page: i + 1,
          part: p.part || "",
        })),
        meta: { title: r.title, author: r.author, multip: true },
        pages,
      };
    }
    if (expandAllParts && pages.length <= 1) {
      return {
        items: [
          {
            bvid: r.bvid || bvid,
            aid: r.aid,
            title: r.title || bvid,
            author: r.author || "",
            page: 1,
          },
        ],
        meta: {
          title: r.title,
          author: r.author,
          multip: false,
          hint: "该稿只有 1P，已按单视频处理",
        },
        pages,
      };
    }
    return {
      items: [
        {
          bvid: r.bvid || bvid,
          aid: r.aid,
          title: r.title || bvid,
          author: r.author || "",
          page: 1,
        },
      ],
      meta: { title: r.title, author: r.author, multip: pages.length > 1 },
      pages,
    };
  }

  // ─── state ──────────────────────────────────────────────────────────────
  const state = {
    open: false,
    busy: false,
    cancel: false,
    mode: "auto", // auto | video | selection | user | favorite | collection | search
    autoCtx: null,
    ctx: null,
    items: [], // { bvid, title, author, page, selected, status?, cues?, error? }
    meta: {},
    delayMs: DEFAULT_DELAY_MS,
    maxPages: DEFAULT_MAX_PAGES,
    ui: null, // geometry + dock
    ai: null, // loaded config
    aiBusy: false,
    aiAbort: false,
    aiRaw: "", // streaming markdown buffer
    aiXhr: null, // active GM_xmlhttpRequest handle
    aiAbortController: null, // page fetch AbortController
    aiStickBottom: true, // 仅「跟随模式」时 paint 才改 scrollTop
    aiUserReading: false, // 用户主动离开底部后锁住，禁止自动回粘
    aiProgScroll: false, // 程序化滚动中，忽略 scroll 事件回写
    aiPaintRaf: 0,
    aiPaintTimer: 0,
    aiPendingText: "",
    aiRenderedText: "",
    aiStreamTextNode: null,
    renderEpoch: 0,
    renderLibs: { core: false, highlight: false, mermaid: false, katex: false },
    mermaidObserver: null,
    mermaidRenderSeq: 0,
    mermaidQueue: Promise.resolve(),
    mermaidRepairing: false,
    aiSourceBvids: [],
    autoCaptureEnabled: loadAutoCaptureSetting(),
    autoCaptureKey: "",
    autoCaptureEpoch: 0,
    autoCaptureTimer: 0,
    autoCaptureAbortController: null,
    autoAnalyzeEnabled: loadAutoAnalyzeSetting(),
    autoAnalyzeKey: "",
    autoAnalyzePendingKey: "",
    autoAnalyzeTimer: 0,
    fastViewCache: new Map(),
    fastTrackCache: new Map(),
    fastSubtitleCache: new Map(),
    fastTrackBodyCache: new Map(),
    cacheDbPromise: null,
    cacheChannel: null,
    transcriptItemKey: "",
    transcriptQuery: "",
    transcriptFilteredIndexes: null,
    transcriptActiveCueIndex: -1,
    transcriptTrackIndex: -1,
    transcriptAutoFollow: loadTranscriptFollowSetting(),
    autoEnablePlayerSubtitle: loadPlayerSubtitleSetting(),
    playerSubtitleOperation: null,
    transcriptUserScrollUntil: 0,
    transcriptVideoAbort: null,
    transcriptSwitchAbort: null,
    transcriptRenderEpoch: 0,
  };

  // ─── UI geometry persistence ────────────────────────────────────────────
  function defaultUiGeom() {
    const w = Math.min(560, Math.max(MIN_W, window.innerWidth - 32));
    const h = Math.min(820, Math.max(MIN_H, window.innerHeight - 32));
    return {
      x: Math.max(8, window.innerWidth - w - 16),
      y: Math.max(8, Math.floor((window.innerHeight - h) / 2)),
      w,
      h,
      dock: null, // null | 'left' | 'right'
      dockExpanded: false,
      view: "ai", // ai | subs | settings
      noteFont: 17,
      noteMode: "deep",
    };
  }

  function loadUiGeom() {
    try {
      const raw = localStorage.getItem(UI_STORE_KEY);
      if (!raw) return defaultUiGeom();
      const o = JSON.parse(raw);
      const d = defaultUiGeom();
      return {
        x: Number.isFinite(o.x) ? o.x : d.x,
        y: Number.isFinite(o.y) ? o.y : d.y,
        w: Math.max(MIN_W, Number(o.w) || d.w),
        h: Math.max(MIN_H, Number(o.h) || d.h),
        dock: o.dock === "left" || o.dock === "right" ? o.dock : null,
        dockExpanded: false,
        view: ["ai", "subs", "settings"].includes(o.view) ? o.view : "ai",
        noteFont: Math.max(NOTE_FONT_MIN, Math.min(NOTE_FONT_MAX, Number(o.noteFont) || 17)),
        noteMode: NOTE_MODE_OPTIONS.includes(o.noteMode) ? o.noteMode : "deep",
      };
    } catch (_) {
      return defaultUiGeom();
    }
  }

  function saveUiGeom() {
    if (!state.ui) return;
    try {
      localStorage.setItem(
        UI_STORE_KEY,
        JSON.stringify({
          x: state.ui.x,
          y: state.ui.y,
          w: state.ui.w,
          h: state.ui.h,
          dock: state.ui.dock,
          view: state.ui.view || "ai",
          noteFont: state.ui.noteFont || 17,
          noteMode: state.ui.noteMode || "deep",
        }),
      );
    } catch (_) {
      /* ignore quota */
    }
  }

  function clampUiToViewport(ui) {
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    ui.w = Math.min(Math.max(MIN_W, ui.w), Math.max(MIN_W, vw - 8));
    ui.h = Math.min(Math.max(MIN_H, ui.h), Math.max(MIN_H, vh - 8));
    ui.x = Math.min(Math.max(0, ui.x), Math.max(0, vw - ui.w));
    ui.y = Math.min(Math.max(0, ui.y), Math.max(0, vh - 48));
    return ui;
  }

  // ─── UI (Catppuccin Mocha floating panel) ───────────────────────────────
  function injectStyles() {
    // Catppuccin Mocha — https://catppuccin.com/palette/ (userstyle tokens)
    GM_addStyle(`
      #${PANEL_ID} {
        /* Catppuccin Mocha */
        --ctp-rosewater: #f5e0dc;
        --ctp-flamingo: #f2cdcd;
        --ctp-pink: #f5c2e7;
        --ctp-mauve: #cba6f7;
        --ctp-red: #f38ba8;
        --ctp-maroon: #eba0ac;
        --ctp-peach: #fab387;
        --ctp-yellow: #f9e2af;
        --ctp-green: #a6e3a1;
        --ctp-teal: #94e2d5;
        --ctp-sky: #89dceb;
        --ctp-sapphire: #74c7ec;
        --ctp-blue: #89b4fa;
        --ctp-lavender: #b4befe;
        --ctp-text: #cdd6f4;
        --ctp-subtext1: #bac2de;
        --ctp-subtext0: #a6adc8;
        --ctp-overlay2: #9399b2;
        --ctp-overlay1: #7f849c;
        --ctp-overlay0: #6c7086;
        --ctp-surface2: #585b70;
        --ctp-surface1: #45475a;
        --ctp-surface0: #313244;
        --ctp-base: #1e1e2e;
        --ctp-mantle: #181825;
        --ctp-crust: #11111b;

        position: fixed;
        top: 0;
        left: 0;
        width: 0;
        height: 0;
        z-index: 2147483646;
        --bsb-note-font: 17px;
        font-family: Inter, ui-sans-serif, system-ui, -apple-system, "Segoe UI",
          "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
        font-size: 12px;
        font-synthesis: none;
        isolation: isolate;
        contain: style;
        color: var(--ctp-text);
        line-height: 1.45;
        pointer-events: none;
      }
      #${PANEL_ID} * { box-sizing: border-box; }

      #${PANEL_ID} .bsb-fab,
      #${PANEL_ID} .bsb-sidebar,
      #${PANEL_ID} .bsb-dock-tab {
        pointer-events: auto;
      }

      #${PANEL_ID} .bsb-fab {
        position: fixed;
        right: 14px;
        bottom: 88px;
        width: 46px;
        height: 46px;
        border-radius: 14px;
        border: 1px solid color-mix(in srgb, var(--ctp-lavender) 45%, transparent);
        cursor: pointer;
        background: color-mix(in srgb, var(--ctp-mantle) 55%, transparent);
        color: var(--ctp-lavender);
        font-weight: 700;
        font-size: 12px;
        letter-spacing: 0.04em;
        box-shadow: 0 8px 28px color-mix(in srgb, var(--ctp-crust) 55%, transparent);
        backdrop-filter: blur(14px) saturate(1.2);
        -webkit-backdrop-filter: blur(14px) saturate(1.2);
        display: flex;
        align-items: center;
        justify-content: center;
        transition: transform .15s ease, border-color .15s ease, color .15s ease,
          background .15s ease, opacity .15s ease;
      }
      #${PANEL_ID} .bsb-fab:hover {
        transform: translateY(-1px);
        color: var(--ctp-base);
        background: color-mix(in srgb, var(--ctp-lavender) 88%, transparent);
        border-color: var(--ctp-lavender);
      }
      #${PANEL_ID}.open .bsb-fab,
      #${PANEL_ID}.docked .bsb-fab {
        opacity: 0;
        visibility: hidden;
        pointer-events: none;
      }

      /* 悬浮玻璃工作台 */
      #${PANEL_ID} .bsb-sidebar {
        position: fixed;
        display: none;
        flex-direction: column;
        overflow: hidden;
        border-radius: 18px;
        border: 1px solid color-mix(in srgb, var(--ctp-overlay0) 32%, transparent);
        background: color-mix(in srgb, var(--ctp-base) 72%, transparent);
        backdrop-filter: blur(22px) saturate(1.4);
        -webkit-backdrop-filter: blur(22px) saturate(1.4);
        box-shadow:
          0 24px 64px color-mix(in srgb, var(--ctp-crust) 55%, transparent),
          0 0 0 1px color-mix(in srgb, var(--ctp-lavender) 8%, transparent),
          inset 0 1px 0 color-mix(in srgb, var(--ctp-overlay2) 22%, transparent);
        min-width: ${MIN_W}px;
        min-height: ${MIN_H}px;
        contain: layout paint style;
      }
      #${PANEL_ID}.open:not(.docked) .bsb-sidebar {
        display: flex;
      }
      /* 贴边收起时：主面板隐藏，只留 dock-tab；展开时显示 */
      #${PANEL_ID}.docked.dock-expanded .bsb-sidebar {
        display: flex;
      }

      /* 贴边标签 */
      #${PANEL_ID} .bsb-dock-tab {
        display: none;
        position: fixed;
        top: 50%;
        transform: translateY(-50%);
        width: ${DOCK_EDGE_PX}px;
        padding: 14px 0;
        writing-mode: vertical-rl;
        text-orientation: mixed;
        letter-spacing: 0.18em;
        font-size: 12px;
        font-weight: 650;
        color: var(--ctp-lavender);
        cursor: pointer;
        user-select: none;
        border: 1px solid color-mix(in srgb, var(--ctp-lavender) 40%, transparent);
        background: color-mix(in srgb, var(--ctp-mantle) 62%, transparent);
        backdrop-filter: blur(14px) saturate(1.2);
        -webkit-backdrop-filter: blur(14px) saturate(1.2);
        box-shadow: 0 8px 28px color-mix(in srgb, var(--ctp-crust) 40%, transparent);
        z-index: 1;
      }
      #${PANEL_ID}.docked .bsb-dock-tab { display: flex; align-items: center; justify-content: center; }
      #${PANEL_ID}.docked[data-dock="right"] .bsb-dock-tab {
        right: 0;
        border-radius: 12px 0 0 12px;
        border-right: none;
      }
      #${PANEL_ID}.docked[data-dock="left"] .bsb-dock-tab {
        left: 0;
        border-radius: 0 12px 12px 0;
        border-left: none;
      }
      #${PANEL_ID} .bsb-dock-tab:hover {
        color: var(--ctp-base);
        background: color-mix(in srgb, var(--ctp-lavender) 85%, transparent);
      }

      #${PANEL_ID} .bsb-head {
        display: flex;
        align-items: center;
        justify-content: space-between;
        padding: 12px 14px 10px;
        flex-shrink: 0;
        gap: 10px;
        border-bottom: 1px solid color-mix(in srgb, var(--ctp-surface0) 80%, transparent);
        background:
          linear-gradient(180deg,
            color-mix(in srgb, var(--ctp-mantle) 70%, transparent) 0%,
            color-mix(in srgb, var(--ctp-base) 35%, transparent) 100%);
        cursor: grab;
        user-select: none;
        touch-action: none;
      }
      #${PANEL_ID} .bsb-head:active { cursor: grabbing; }
      #${PANEL_ID} .bsb-head .bsb-head-title {
        flex: 1;
        min-width: 0;
        display: flex;
        align-items: center;
        flex-wrap: wrap;
        gap: 6px;
      }
      #${PANEL_ID} .bsb-logo {
        width: 28px; height: 28px; border-radius: 9px;
        display: inline-flex; align-items: center; justify-content: center;
        font-size: 11px; font-weight: 800; letter-spacing: -0.02em;
        color: var(--ctp-crust);
        background: linear-gradient(135deg, var(--ctp-lavender), var(--ctp-mauve));
        box-shadow: 0 4px 14px color-mix(in srgb, var(--ctp-mauve) 35%, transparent);
      }
      #${PANEL_ID} .bsb-head strong {
        font-size: 14px;
        font-weight: 700;
        color: var(--ctp-text);
        letter-spacing: 0.01em;
      }
      #${PANEL_ID} .bsb-head .bsb-ver {
        font-size: 10px;
        color: var(--ctp-overlay1);
        font-weight: 500;
        padding: 2px 7px;
        border-radius: 999px;
        background: color-mix(in srgb, var(--ctp-surface0) 55%, transparent);
      }
      #${PANEL_ID} .bsb-flavor {
        display: none;
      }
      /* 主导航 */
      #${PANEL_ID} .bsb-nav {
        display: flex;
        gap: 4px;
        padding: 8px 12px;
        flex-shrink: 0;
        border-bottom: 1px solid color-mix(in srgb, var(--ctp-surface0) 70%, transparent);
        background: color-mix(in srgb, var(--ctp-mantle) 35%, transparent);
      }
      #${PANEL_ID} .bsb-nav button {
        flex: 1;
        height: 36px;
        border: none;
        border-radius: 11px;
        cursor: pointer;
        font-size: 12.5px;
        font-weight: 650;
        color: var(--ctp-subtext0);
        background: transparent;
        transition: background .15s, color .15s, box-shadow .15s;
        letter-spacing: 0.01em;
      }
      #${PANEL_ID} .bsb-nav button:hover {
        color: var(--ctp-text);
        background: color-mix(in srgb, var(--ctp-surface0) 45%, transparent);
      }
      #${PANEL_ID} .bsb-nav button.active {
        color: var(--ctp-crust);
        background: linear-gradient(135deg,
          color-mix(in srgb, var(--ctp-lavender) 92%, transparent),
          color-mix(in srgb, var(--ctp-mauve) 88%, transparent));
        box-shadow: 0 6px 18px color-mix(in srgb, var(--ctp-mauve) 28%, transparent);
      }
      #${PANEL_ID} .bsb-head-actions {
        display: flex;
        align-items: center;
        gap: 4px;
        flex-shrink: 0;
      }
      #${PANEL_ID} .bsb-icon-btn {
        width: 28px;
        height: 28px;
        border-radius: 8px;
        background: color-mix(in srgb, var(--ctp-surface0) 55%, transparent);
        border: 1px solid color-mix(in srgb, var(--ctp-surface1) 60%, transparent);
        color: var(--ctp-subtext0);
        cursor: pointer;
        font-size: 14px;
        line-height: 1;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        transition: color .12s, background .12s, border-color .12s;
      }
      #${PANEL_ID} .bsb-icon-btn:hover {
        color: var(--ctp-lavender);
        border-color: color-mix(in srgb, var(--ctp-lavender) 40%, transparent);
        background: color-mix(in srgb, var(--ctp-lavender) 12%, transparent);
      }
      #${PANEL_ID} .bsb-icon-btn.bsb-close:hover {
        color: var(--ctp-red);
        border-color: color-mix(in srgb, var(--ctp-red) 40%, transparent);
        background: color-mix(in srgb, var(--ctp-red) 12%, transparent);
      }

      /* 拉伸手柄 */
      #${PANEL_ID} .bsb-resize {
        position: absolute;
        z-index: 3;
        background: transparent;
      }
      #${PANEL_ID} .bsb-resize.n { top: 0; left: 10px; right: 10px; height: 6px; cursor: ns-resize; }
      #${PANEL_ID} .bsb-resize.s { bottom: 0; left: 10px; right: 10px; height: 6px; cursor: ns-resize; }
      #${PANEL_ID} .bsb-resize.e { right: 0; top: 10px; bottom: 10px; width: 6px; cursor: ew-resize; }
      #${PANEL_ID} .bsb-resize.w { left: 0; top: 10px; bottom: 10px; width: 6px; cursor: ew-resize; }
      #${PANEL_ID} .bsb-resize.ne { top: 0; right: 0; width: 12px; height: 12px; cursor: nesw-resize; }
      #${PANEL_ID} .bsb-resize.nw { top: 0; left: 0; width: 12px; height: 12px; cursor: nwse-resize; }
      #${PANEL_ID} .bsb-resize.se { bottom: 0; right: 0; width: 14px; height: 14px; cursor: nwse-resize; }
      #${PANEL_ID} .bsb-resize.sw { bottom: 0; left: 0; width: 12px; height: 12px; cursor: nesw-resize; }
      #${PANEL_ID} .bsb-resize.se::after {
        content: "";
        position: absolute;
        right: 3px;
        bottom: 3px;
        width: 8px;
        height: 8px;
        border-right: 2px solid color-mix(in srgb, var(--ctp-overlay1) 70%, transparent);
        border-bottom: 2px solid color-mix(in srgb, var(--ctp-overlay1) 70%, transparent);
        border-radius: 0 0 2px 0;
      }
      #${PANEL_ID}.docked .bsb-resize { display: none; }
      #${PANEL_ID} .bsb-sidebar.dragging,
      #${PANEL_ID} .bsb-sidebar.resizing {
        transition: none !important;
        user-select: none;
      }

      /* 内容区：三工作区切换 */
      #${PANEL_ID} .bsb-body {
        padding: 0;
        overflow: hidden;
        display: flex;
        flex-direction: column;
        min-height: 0;
        flex: 1;
      }
      #${PANEL_ID} .bsb-view {
        display: none;
        flex-direction: column;
        gap: 10px;
        padding: 12px 14px 10px;
        min-height: 0;
        flex: 1 1 0;
        overflow: hidden;
      }
      #${PANEL_ID} .bsb-view.active { display: flex; }
      /* AI 页：画布吃掉剩余高度（给绝对定位阅读器当基准） */
      #${PANEL_ID} .bsb-view[data-view-panel="ai"] {
        gap: 8px;
      }
      #${PANEL_ID} .bsb-view[data-view-panel="ai"] .bsb-ai-canvas-wrap {
        flex: 1 1 0;
        min-height: 260px;
      }

      #${PANEL_ID} .bsb-badge {
        display: inline-flex; align-items: center; gap: 4px;
        background: color-mix(in srgb, var(--ctp-sapphire) 16%, transparent);
        color: var(--ctp-sapphire);
        border: 1px solid color-mix(in srgb, var(--ctp-sapphire) 28%, transparent);
        border-radius: 999px;
        padding: 3px 10px;
        font-weight: 650;
        font-size: 11px;
      }
      #${PANEL_ID} .bsb-badge.manual {
        background: color-mix(in srgb, var(--ctp-mauve) 16%, transparent);
        color: var(--ctp-mauve);
        border-color: color-mix(in srgb, var(--ctp-mauve) 28%, transparent);
      }
      #${PANEL_ID} .bsb-meta {
        margin-top: 3px;
        color: var(--ctp-subtext0);
        word-break: break-all;
        font-size: 11px;
        line-height: 1.4;
      }
      #${PANEL_ID} .bsb-mode-row {
        display: flex; flex-wrap: wrap; gap: 8px; align-items: center;
      }
      #${PANEL_ID} .bsb-mode-row label {
        display: inline-flex; align-items: center; gap: 6px; color: var(--ctp-subtext1);
      }
      #${PANEL_ID} .bsb-mode-row select,
      #${PANEL_ID} .bsb-field select {
        height: 32px; min-width: 132px; border-radius: 10px;
        border: 1px solid color-mix(in srgb, var(--ctp-surface2) 55%, transparent);
        background: color-mix(in srgb, var(--ctp-mantle) 65%, transparent);
        color: var(--ctp-text); padding: 0 10px; font-size: 12px; outline: none; cursor: pointer;
      }
      #${PANEL_ID} .bsb-mode-row select:focus {
        border-color: color-mix(in srgb, var(--ctp-mauve) 55%, transparent);
        box-shadow: 0 0 0 3px color-mix(in srgb, var(--ctp-mauve) 18%, transparent);
      }
      #${PANEL_ID} .bsb-auto-hint {
        font-size: 11px; color: var(--ctp-overlay1); flex: 1; min-width: 100px;
      }
      #${PANEL_ID} .bsb-auto-hint strong { color: var(--ctp-teal); font-weight: 650; }

      /* 通用按钮 */
      #${PANEL_ID} .bsb-btn {
        height: 34px; border-radius: 10px;
        border: 1px solid color-mix(in srgb, var(--ctp-surface2) 55%, transparent);
        background: color-mix(in srgb, var(--ctp-surface0) 50%, transparent);
        cursor: pointer; font-size: 12.5px; padding: 0 12px; color: var(--ctp-text);
        font-weight: 550; transition: background .12s, border-color .12s, color .12s, transform .12s;
      }
      #${PANEL_ID} .bsb-btn:hover:not(:disabled) {
        border-color: color-mix(in srgb, var(--ctp-lavender) 45%, transparent);
        color: var(--ctp-lavender);
        background: color-mix(in srgb, var(--ctp-surface1) 40%, transparent);
      }
      #${PANEL_ID} .bsb-btn:active:not(:disabled) { transform: scale(0.98); }
      #${PANEL_ID} .bsb-btn:disabled { opacity: .42; cursor: not-allowed; }
      #${PANEL_ID} .bsb-btn.primary {
        background: linear-gradient(135deg,
          color-mix(in srgb, var(--ctp-blue) 90%, transparent),
          color-mix(in srgb, var(--ctp-lavender) 85%, transparent));
        border-color: transparent; color: var(--ctp-crust); font-weight: 700;
        box-shadow: 0 6px 16px color-mix(in srgb, var(--ctp-blue) 25%, transparent);
      }
      #${PANEL_ID} .bsb-btn.primary:hover:not(:disabled) {
        filter: brightness(1.06); color: var(--ctp-crust);
      }
      #${PANEL_ID} .bsb-btn.accent {
        background: linear-gradient(135deg, var(--ctp-mauve), var(--ctp-pink));
        border-color: transparent; color: var(--ctp-crust); font-weight: 750;
        box-shadow: 0 8px 22px color-mix(in srgb, var(--ctp-mauve) 32%, transparent);
      }
      #${PANEL_ID} .bsb-btn.accent:hover:not(:disabled) {
        filter: brightness(1.05); color: var(--ctp-crust);
      }
      #${PANEL_ID} .bsb-btn.danger {
        color: var(--ctp-red);
        border-color: color-mix(in srgb, var(--ctp-red) 38%, transparent);
        background: color-mix(in srgb, var(--ctp-red) 12%, transparent);
      }
      #${PANEL_ID} .bsb-btn.ghost {
        background: transparent;
        border-color: color-mix(in srgb, var(--ctp-surface1) 50%, transparent);
      }
      #${PANEL_ID} .bsb-toolbar,
      #${PANEL_ID} .bsb-actions {
        display: flex; flex-wrap: wrap; gap: 6px; align-items: center;
      }
      #${PANEL_ID} .bsb-actions {
        display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 6px; flex-shrink: 0;
      }
      #${PANEL_ID} .bsb-toolbar button,
      #${PANEL_ID} .bsb-actions button {
        height: 34px; border-radius: 10px;
        border: 1px solid color-mix(in srgb, var(--ctp-surface2) 55%, transparent);
        background: color-mix(in srgb, var(--ctp-surface0) 50%, transparent);
        cursor: pointer; font-size: 12px; padding: 0 10px; color: var(--ctp-text);
      }
      #${PANEL_ID} .bsb-toolbar button.primary,
      #${PANEL_ID} .bsb-actions button.primary {
        background: color-mix(in srgb, var(--ctp-blue) 80%, transparent);
        border-color: transparent; color: var(--ctp-crust); font-weight: 650;
      }
      #${PANEL_ID} .bsb-toolbar button.danger {
        color: var(--ctp-red);
        border-color: color-mix(in srgb, var(--ctp-red) 40%, transparent);
        background: color-mix(in srgb, var(--ctp-red) 10%, transparent);
      }
      #${PANEL_ID} .bsb-toolbar button:disabled,
      #${PANEL_ID} .bsb-actions button:disabled { opacity: .45; cursor: not-allowed; }

      #${PANEL_ID} .bsb-opts {
        display: flex; flex-wrap: wrap; gap: 10px; align-items: center; color: var(--ctp-subtext1);
      }
      #${PANEL_ID} .bsb-opts label { display: inline-flex; align-items: center; gap: 5px; font-size: 11.5px; }
      #${PANEL_ID} .bsb-opts input[type="number"] {
        width: 58px; height: 28px;
        border: 1px solid color-mix(in srgb, var(--ctp-surface2) 60%, transparent);
        border-radius: 8px; padding: 0 6px; font-size: 12px; color: var(--ctp-text);
        background: color-mix(in srgb, var(--ctp-mantle) 55%, transparent); outline: none;
      }
      #${PANEL_ID} .bsb-opts input[type="checkbox"] { accent-color: var(--ctp-mauve); }

      #${PANEL_ID} .bsb-list {
        border: 1px solid color-mix(in srgb, var(--ctp-surface1) 50%, transparent);
        border-radius: 14px; overflow: auto; flex: 1; min-height: 0;
        background: color-mix(in srgb, var(--ctp-mantle) 55%, transparent);
      }
      #${PANEL_ID} .bsb-list table { width: 100%; border-collapse: collapse; }
      #${PANEL_ID} .bsb-list th, #${PANEL_ID} .bsb-list td {
        padding: 8px 10px;
        border-bottom: 1px solid color-mix(in srgb, var(--ctp-surface0) 65%, transparent);
        text-align: left; vertical-align: top;
      }
      #${PANEL_ID} .bsb-list th {
        position: sticky; top: 0; z-index: 1; font-weight: 650; font-size: 11px;
        color: var(--ctp-subtext1);
        background: color-mix(in srgb, var(--ctp-surface0) 85%, transparent);
        backdrop-filter: blur(10px); -webkit-backdrop-filter: blur(10px);
      }
      #${PANEL_ID} .bsb-list tr:hover td {
        background: color-mix(in srgb, var(--ctp-surface0) 40%, transparent);
      }
      #${PANEL_ID} .bsb-list .bsb-t {
        max-width: 240px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
        color: var(--ctp-text); font-weight: 500;
      }
      #${PANEL_ID} .bsb-list .st-ok { color: var(--ctp-green); font-weight: 650; }
      #${PANEL_ID} .bsb-list .st-empty { color: var(--ctp-peach); }
      #${PANEL_ID} .bsb-list .st-err { color: var(--ctp-red); }
      #${PANEL_ID} .bsb-list .st-wait { color: var(--ctp-overlay1); }
      #${PANEL_ID} .bsb-list input[type="checkbox"] { accent-color: var(--ctp-mauve); }
      #${PANEL_ID} .bsb-list .bsb-open-transcript {
        height: 26px; padding: 0 8px; border-radius: 7px; cursor: pointer;
        border: 1px solid color-mix(in srgb, var(--ctp-sapphire) 38%, transparent);
        background: color-mix(in srgb, var(--ctp-sapphire) 12%, transparent);
        color: var(--ctp-sapphire); font-size: 10.5px; white-space: nowrap;
      }
      #${PANEL_ID} .bsb-view[data-view-panel="subs"] {
        overflow-y: auto; overscroll-behavior: contain;
      }
      #${PANEL_ID} .bsb-view[data-view-panel="subs"] .bsb-list {
        flex: 0 0 auto; min-height: 82px; max-height: 150px;
      }
      #${PANEL_ID} .bsb-transcript-shell {
        flex: 1 0 260px; min-height: 220px; overflow: hidden;
        display: flex; flex-direction: column;
        border: 1px solid color-mix(in srgb, var(--ctp-surface1) 55%, transparent);
        border-radius: 14px; background: color-mix(in srgb, var(--ctp-base) 62%, transparent);
        contain: layout paint style;
      }
      #${PANEL_ID} .bsb-transcript-head {
        display: flex; align-items: center; justify-content: space-between; gap: 8px;
        padding: 9px 10px 7px; border-bottom: 1px solid color-mix(in srgb, var(--ctp-surface0) 70%, transparent);
      }
      #${PANEL_ID} .bsb-transcript-title { min-width: 0; display: grid; gap: 2px; }
      #${PANEL_ID} .bsb-transcript-title strong {
        font-size: 12px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
      }
      #${PANEL_ID} .bsb-transcript-title span { font-size: 10px; color: var(--ctp-overlay1); }
      #${PANEL_ID} .bsb-transcript-tools {
        padding: 8px 10px; display: grid; grid-template-columns: minmax(120px,1fr) auto auto auto;
        gap: 6px; align-items: center; border-bottom: 1px solid color-mix(in srgb, var(--ctp-surface0) 62%, transparent);
      }
      #${PANEL_ID} .bsb-transcript-search {
        height: 31px; display: grid; grid-template-columns: auto minmax(0,1fr) auto;
        align-items: center; gap: 6px; padding: 0 8px; border-radius: 9px;
        border: 1px solid color-mix(in srgb, var(--ctp-surface2) 58%, transparent);
        background: color-mix(in srgb, var(--ctp-mantle) 60%, transparent);
      }
      #${PANEL_ID} .bsb-transcript-search input {
        min-width: 0; width: 100%; border: 0; outline: 0; background: transparent;
        color: var(--ctp-text); font-size: 11.5px;
      }
      #${PANEL_ID} .bsb-transcript-count { font-size: 10px; color: var(--ctp-overlay1); font-variant-numeric: tabular-nums; }
      #${PANEL_ID} .bsb-transcript-track {
        max-width: 118px; height: 31px; border-radius: 9px; padding: 0 7px;
        border: 1px solid color-mix(in srgb, var(--ctp-surface2) 58%, transparent);
        background: color-mix(in srgb, var(--ctp-mantle) 60%, transparent); color: var(--ctp-text); font-size: 10.5px;
      }
      #${PANEL_ID} .bsb-transcript-follow { font-size: 10.5px; color: var(--ctp-subtext0); white-space: nowrap; }
      #${PANEL_ID} .bsb-transcript-follow input { accent-color: var(--ctp-sapphire); }
      #${PANEL_ID} .bsb-transcript-refresh {
        height: 31px; padding: 0 8px; border-radius: 9px; cursor: pointer;
        border: 1px solid color-mix(in srgb, var(--ctp-surface2) 58%, transparent);
        background: color-mix(in srgb, var(--ctp-surface0) 50%, transparent); color: var(--ctp-text); font-size: 10.5px;
      }
      #${PANEL_ID} .bsb-transcript-list {
        flex: 1 1 0; min-height: 0; overflow-y: auto; overscroll-behavior: contain;
        padding: 4px 7px 14px; scroll-padding-block: 38%;
      }
      #${PANEL_ID} .bsb-transcript-row {
        content-visibility: auto; contain-intrinsic-size: auto 58px; contain: layout paint style;
        display: grid; grid-template-columns: 52px minmax(0,1fr); gap: 8px; align-items: start;
        padding: 8px 7px; border-radius: 9px; border-left: 3px solid transparent;
      }
      #${PANEL_ID} .bsb-transcript-row:hover { background: color-mix(in srgb, var(--ctp-surface0) 45%, transparent); }
      #${PANEL_ID} .bsb-transcript-row.active {
        background: color-mix(in srgb, var(--ctp-sapphire) 12%, transparent);
        border-left-color: var(--ctp-sapphire);
      }
      #${PANEL_ID} .bsb-transcript-time {
        border: 0; background: transparent; color: var(--ctp-sapphire); cursor: pointer;
        padding: 2px 0; text-align: left; font-size: 10.5px; font-variant-numeric: tabular-nums;
      }
      #${PANEL_ID} .bsb-transcript-text {
        margin: 0; color: var(--ctp-subtext1); font-size: 12px; line-height: 1.62; overflow-wrap: anywhere;
      }
      #${PANEL_ID} .bsb-transcript-row.active .bsb-transcript-text { color: var(--ctp-text); font-weight: 600; }
      #${PANEL_ID} .bsb-transcript-text mark {
        background: color-mix(in srgb, var(--ctp-yellow) 48%, transparent); color: inherit; border-radius: 3px; padding: 0 .08em;
      }
      #${PANEL_ID} .bsb-transcript-empty {
        min-height: 120px; display: grid; place-items: center; text-align: center;
        color: var(--ctp-overlay1); font-size: 11.5px; line-height: 1.55; padding: 20px;
      }
      @media (max-width: 640px) {
        #${PANEL_ID} .bsb-transcript-tools { grid-template-columns: minmax(0,1fr) auto; }
        #${PANEL_ID} .bsb-transcript-follow { display: none; }
      }

      #${PANEL_ID} .bsb-empty {
        padding: 36px 16px; text-align: center; color: var(--ctp-overlay1);
        display: flex; flex-direction: column; align-items: center; gap: 8px;
      }
      #${PANEL_ID} .bsb-empty .bsb-empty-ico {
        width: 48px; height: 48px; border-radius: 16px;
        display: flex; align-items: center; justify-content: center;
        font-size: 22px;
        background: color-mix(in srgb, var(--ctp-surface0) 55%, transparent);
        border: 1px solid color-mix(in srgb, var(--ctp-surface1) 45%, transparent);
      }
      #${PANEL_ID} .bsb-empty strong { color: var(--ctp-subtext1); font-size: 13px; }
      #${PANEL_ID} .bsb-empty span { font-size: 12px; max-width: 280px; line-height: 1.45; }

      /* ── AI 工作区（主画布） ── */
      #${PANEL_ID} .bsb-ai-hero {
        display: flex; align-items: flex-start; justify-content: space-between;
        gap: 12px; flex-shrink: 0;
      }
      #${PANEL_ID} .bsb-ai-hero h2 {
        margin: 0; font-size: 16px; font-weight: 750; color: var(--ctp-text);
        letter-spacing: -0.01em;
      }
      #${PANEL_ID} .bsb-ai-hero p {
        margin: 4px 0 0; font-size: 11.5px; color: var(--ctp-subtext0); line-height: 1.4;
      }
      #${PANEL_ID} .bsb-ai-hero-actions {
        display: flex; gap: 6px; flex-shrink: 0; align-items: center;
      }
      #${PANEL_ID} .bsb-ai-hero-actions .bsb-btn.accent {
        height: 40px; padding: 0 16px; font-size: 13px; border-radius: 12px;
      }
      #${PANEL_ID} .bsb-chips {
        display: flex; flex-wrap: wrap; gap: 6px; flex-shrink: 0;
      }
      #${PANEL_ID} .bsb-chip {
        display: inline-flex; align-items: center; gap: 5px;
        height: 26px; padding: 0 10px; border-radius: 999px; font-size: 11px; font-weight: 600;
        color: var(--ctp-subtext1);
        background: color-mix(in srgb, var(--ctp-surface0) 55%, transparent);
        border: 1px solid color-mix(in srgb, var(--ctp-surface1) 45%, transparent);
      }
      #${PANEL_ID} .bsb-chip em {
        font-style: normal; color: var(--ctp-lavender); font-weight: 700;
      }
      #${PANEL_ID} .bsb-chip.ok { color: var(--ctp-green); border-color: color-mix(in srgb, var(--ctp-green) 30%, transparent); }
      #${PANEL_ID} .bsb-chip.warn { color: var(--ctp-peach); }

      #${PANEL_ID} .bsb-ai-canvas-wrap {
        /* 关键：绝对填充，保证内部一定有固定高度可滚动 */
        flex: 1 1 auto;
        min-height: 280px;
        position: relative;
        border-radius: 16px;
        overflow: hidden;
        border: 1px solid color-mix(in srgb, var(--ctp-surface1) 45%, transparent);
        background:
          radial-gradient(120% 80% at 100% 0%,
            color-mix(in srgb, var(--ctp-mauve) 10%, transparent), transparent 55%),
          color-mix(in srgb, var(--ctp-crust) 62%, transparent);
        box-shadow: inset 0 1px 0 color-mix(in srgb, var(--ctp-overlay2) 10%, transparent);
      }
      #${PANEL_ID} .bsb-ai-canvas-bar {
        position: absolute; top: 0; left: 0; right: 0; height: 40px;
        z-index: 2;
        display: flex; align-items: center; justify-content: space-between; gap: 8px;
        padding: 0 12px;
        border-bottom: 1px solid color-mix(in srgb, var(--ctp-surface0) 70%, transparent);
        background: color-mix(in srgb, var(--ctp-mantle) 82%, transparent);
        backdrop-filter: blur(10px);
        -webkit-backdrop-filter: blur(10px);
        font-size: 11px; color: var(--ctp-overlay1); font-weight: 600;
        letter-spacing: 0.04em; text-transform: uppercase;
      }
      #${PANEL_ID} .bsb-ai-canvas-bar .bsb-bar-left {
        display: inline-flex; align-items: center; gap: 8px; min-width: 0;
      }
      #${PANEL_ID} .bsb-live-dot {
        width: 7px; height: 7px; border-radius: 50%;
        background: var(--ctp-overlay0); flex-shrink: 0;
      }
      #${PANEL_ID} .bsb-ai-stream.streaming .bsb-live-dot {
        background: var(--ctp-green);
        box-shadow: 0 0 0 3px color-mix(in srgb, var(--ctp-green) 25%, transparent);
        animation: bsb-pulse 1.1s ease-in-out infinite;
      }
      #${PANEL_ID} .bsb-ai-canvas-bar .bsb-bar-actions {
        display: inline-flex; gap: 4px; align-items: center; flex-shrink: 0;
      }
      #${PANEL_ID} .bsb-ai-canvas-bar .bsb-mini {
        height: 24px; padding: 0 8px; border-radius: 7px; font-size: 10px;
        letter-spacing: 0; text-transform: none; font-weight: 650;
        border: 1px solid color-mix(in srgb, var(--ctp-surface2) 50%, transparent);
        background: color-mix(in srgb, var(--ctp-surface0) 40%, transparent);
        color: var(--ctp-subtext0); cursor: pointer;
      }
      #${PANEL_ID} .bsb-ai-canvas-bar .bsb-mini:hover {
        color: var(--ctp-lavender); border-color: color-mix(in srgb, var(--ctp-lavender) 40%, transparent);
      }
      #${PANEL_ID} .bsb-ai-canvas-bar .bsb-mini.on {
        color: var(--ctp-teal);
        border-color: color-mix(in srgb, var(--ctp-teal) 40%, transparent);
        background: color-mix(in srgb, var(--ctp-teal) 12%, transparent);
      }
      #${PANEL_ID} .bsb-ai-stream {
        position: absolute;
        top: 40px; left: 0; right: 0; bottom: 0;
        overflow: hidden;
      }
      #${PANEL_ID} .bsb-ai-stream .bsb-ai-raw { display: none !important; }

      /*
       * 唯一滚动层：绝对定位铺满画布，height 明确，overflow-y: scroll
       * （flex 链常导致“看起来能滚其实高度在变、滚动无效”）
       */
      #${PANEL_ID} .bsb-ai-md {
        position: absolute;
        inset: 0;
        overflow-y: scroll !important;
        overflow-x: hidden !important;
        padding: 32px 28px 88px;
        box-sizing: border-box;
        font-size: var(--bsb-note-font);
        line-height: 1.9;
        letter-spacing: 0.03em;
        color: var(--ctp-text);
        scroll-behavior: auto;
        overscroll-behavior: contain;
        overflow-anchor: none; /* 禁止浏览器滚动锚定与 stick 互抢 */
        -webkit-overflow-scrolling: touch;
        touch-action: pan-y;
        font-family: "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei UI",
          "Microsoft YaHei", "Noto Sans SC", "Source Han Sans SC", system-ui, sans-serif;
      }
      #${PANEL_ID} .bsb-ai-content {
        max-width: 40em;
        margin: 0 auto;
        min-height: min-content;
        overflow-anchor: none;
      }
      #${PANEL_ID} .bsb-ai-stream-body {
        margin: 0;
        padding: 0;
        border: none;
        background: transparent;
        white-space: pre-wrap;
        word-break: break-word;
        overflow-wrap: anywhere;
        font-size: 17px;
        line-height: 2.15;
        letter-spacing: 0.04em;
        color: var(--ctp-text);
        font-family: inherit;
        display: block;
        overflow-anchor: none;
      }
      #${PANEL_ID} .bsb-ai-caret {
        display: inline-block;
        width: 0.5em; height: 1.1em;
        margin-left: 3px;
        vertical-align: text-bottom;
        background: var(--ctp-lavender);
        border-radius: 1px;
        animation: bsb-caret 1s step-end infinite;
      }
      @keyframes bsb-caret {
        0%, 100% { opacity: 1; }
        50% { opacity: 0; }
      }
      #${PANEL_ID} .bsb-ai-anchor {
        height: 24px; width: 100%; pointer-events: none; flex-shrink: 0;
      }
      /* 浮层：不在底部时跳到最新（ChatGPT 风格） */
      #${PANEL_ID} .bsb-jump-latest {
        position: absolute;
        right: 14px; bottom: 14px;
        z-index: 5;
        display: none;
        align-items: center; gap: 6px;
        height: 36px; padding: 0 14px;
        border-radius: 999px;
        border: 1px solid color-mix(in srgb, var(--ctp-lavender) 40%, transparent);
        background: color-mix(in srgb, var(--ctp-mantle) 88%, transparent);
        color: var(--ctp-lavender);
        font-size: 12px; font-weight: 700;
        cursor: pointer;
        box-shadow: 0 8px 24px color-mix(in srgb, var(--ctp-crust) 45%, transparent);
        backdrop-filter: blur(12px);
        -webkit-backdrop-filter: blur(12px);
        pointer-events: auto;
      }
      #${PANEL_ID} .bsb-jump-latest.show { display: inline-flex; }
      #${PANEL_ID} .bsb-jump-latest:hover {
        color: var(--ctp-crust);
        background: color-mix(in srgb, var(--ctp-lavender) 88%, transparent);
      }

      /* Markdown 阅读优化：更大间距、更松段落 */
      #${PANEL_ID} .bsb-ai-md h1,
      #${PANEL_ID} .bsb-ai-md h2,
      #${PANEL_ID} .bsb-ai-md h3,
      #${PANEL_ID} .bsb-ai-md h4 {
        color: var(--ctp-lavender);
        font-weight: 700;
        letter-spacing: 0.01em;
        line-height: 1.35;
      }
      #${PANEL_ID} .bsb-ai-md h1 {
        font-size: 1.5em; margin: 1.6em 0 0.8em;
        padding-bottom: 0.4em;
        border-bottom: 1px solid color-mix(in srgb, var(--ctp-surface1) 50%, transparent);
      }
      #${PANEL_ID} .bsb-ai-md h1:first-child { margin-top: 0.15em; }
      #${PANEL_ID} .bsb-ai-md h2 {
        font-size: 1.28em; margin: 1.55em 0 0.7em; color: var(--ctp-mauve);
      }
      #${PANEL_ID} .bsb-ai-md h3 {
        font-size: 1.12em; margin: 1.4em 0 0.6em; color: var(--ctp-sapphire);
      }
      #${PANEL_ID} .bsb-ai-md p {
        margin: 1.15em 0;
        line-height: 1.95;
      }
      #${PANEL_ID} .bsb-ai-md ul,
      #${PANEL_ID} .bsb-ai-md ol {
        margin: 1.1em 0;
        padding-left: 1.7em;
      }
      #${PANEL_ID} .bsb-ai-md li {
        margin: 0.65em 0;
        line-height: 1.95;
        padding-left: 0.25em;
      }
      #${PANEL_ID} .bsb-ai-md li > p { margin: 0.35em 0; }
      #${PANEL_ID} .bsb-ai-md a { color: var(--ctp-blue); text-decoration: none; }
      #${PANEL_ID} .bsb-ai-md a:hover { text-decoration: underline; }
      #${PANEL_ID} .bsb-ai-md strong { color: var(--ctp-rosewater); font-weight: 700; }
      #${PANEL_ID} .bsb-ai-md em { color: var(--ctp-subtext1); }
      #${PANEL_ID} .bsb-ai-md blockquote {
        margin: 1.1em 0;
        padding: 0.75em 1.1em;
        border-left: 3px solid var(--ctp-mauve);
        color: var(--ctp-subtext0);
        background: color-mix(in srgb, var(--ctp-surface0) 28%, transparent);
        border-radius: 0 12px 12px 0;
        line-height: 1.85;
      }
      #${PANEL_ID} .bsb-ai-md table {
        border-collapse: separate; border-spacing: 0;
        width: 100%; margin: 1.1em 0; font-size: 13.5px;
        overflow: hidden; border-radius: 12px;
        border: 1px solid color-mix(in srgb, var(--ctp-surface1) 55%, transparent);
      }
      #${PANEL_ID} .bsb-ai-md th, #${PANEL_ID} .bsb-ai-md td {
        border-bottom: 1px solid color-mix(in srgb, var(--ctp-surface1) 45%, transparent);
        padding: 10px 12px;
        line-height: 1.55;
      }
      #${PANEL_ID} .bsb-ai-md tr:last-child td { border-bottom: none; }
      #${PANEL_ID} .bsb-ai-md th {
        background: color-mix(in srgb, var(--ctp-surface0) 55%, transparent);
        color: var(--ctp-subtext1); font-weight: 650;
      }
      #${PANEL_ID} .bsb-ai-md pre {
        margin: 1.1em 0; padding: 14px 16px; border-radius: 14px;
        overflow: auto; max-height: 420px;
        background: #11111b;
        border: 1px solid color-mix(in srgb, var(--ctp-surface1) 40%, transparent);
        line-height: 1.55;
      }
      #${PANEL_ID} .bsb-ai-md code {
        font-family: "JetBrains Mono", "Fira Code", ui-monospace, monospace;
        font-size: 0.9em;
      }
      #${PANEL_ID} .bsb-ai-md :not(pre) > code {
        background: color-mix(in srgb, var(--ctp-surface0) 60%, transparent);
        padding: 0.15em 0.4em; border-radius: 6px; color: var(--ctp-peach);
      }
      #${PANEL_ID} .bsb-ai-md .hljs { background: transparent; color: var(--ctp-text); }
      #${PANEL_ID} .bsb-ai-md .mermaid {
        margin: 1.25em 0;
        min-height: 86px;
        text-align: left;
      }
      #${PANEL_ID} .bsb-mermaid-error {
        overflow: hidden;
        border: 1px solid color-mix(in srgb, var(--ctp-red) 42%, var(--ctp-surface1));
        border-radius: 12px;
        background: color-mix(in srgb, var(--ctp-mantle) 78%, transparent);
      }
      #${PANEL_ID} .bsb-mermaid-error-head {
        min-height: 42px;
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 10px;
        padding: 7px 10px 7px 13px;
        color: var(--ctp-peach);
        border-bottom: 1px solid color-mix(in srgb, var(--ctp-red) 24%, transparent);
      }
      #${PANEL_ID} .bsb-mermaid-error details { padding: 9px 12px 12px; }
      #${PANEL_ID} .bsb-mermaid-error summary { cursor: pointer; color: var(--ctp-overlay1); }
      #${PANEL_ID} .bsb-mermaid-error pre { margin: 10px 0 0; max-height: 280px; overflow: auto; }
      #${PANEL_ID} .bsb-mermaid-card {
        position: relative;
        display: grid;
        grid-template-rows: auto minmax(0, 1fr);
        min-width: 0;
        overflow: hidden;
        border-radius: 15px;
        border: 1px solid color-mix(in srgb, var(--ctp-blue) 30%, var(--ctp-surface1));
        background: #181825;
        box-shadow: 0 12px 30px rgba(0, 0, 0, .22);
        content-visibility: visible;
        contain: layout paint style;
      }
      #${PANEL_ID} .bsb-mermaid-toolbar {
        position: sticky;
        top: 0;
        z-index: 3;
        min-height: 40px;
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 8px;
        padding: 6px 8px 6px 11px;
        border-bottom: 1px solid rgba(137, 180, 250, .2);
        background: rgba(24, 24, 37, .96);
        color: #bac2de;
        backdrop-filter: blur(10px);
        -webkit-backdrop-filter: blur(10px);
      }
      #${PANEL_ID} .bsb-mermaid-title {
        min-width: 0;
        display: inline-flex;
        align-items: center;
        gap: 7px;
        color: #cdd6f4;
        font-size: 11px;
        font-weight: 700;
        letter-spacing: .03em;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
      }
      #${PANEL_ID} .bsb-mermaid-title::before {
        content: "◇";
        color: #89b4fa;
      }
      #${PANEL_ID} .bsb-mermaid-card .bsb-mermaid-title {
        font-variant-numeric: tabular-nums;
      }
      #${PANEL_ID} .bsb-mermaid-tools {
        flex: 0 0 auto;
        display: inline-flex;
        align-items: center;
        gap: 3px;
      }
      #${PANEL_ID} .bsb-mermaid-tool {
        appearance: none;
        min-width: 29px;
        height: 28px;
        padding: 0 7px;
        border: 1px solid transparent;
        border-radius: 7px;
        background: transparent;
        color: #a6adc8;
        font: 650 11px/1 system-ui, sans-serif;
        cursor: pointer;
      }
      #${PANEL_ID} .bsb-mermaid-tool:hover,
      #${PANEL_ID} .bsb-mermaid-tool:focus-visible {
        color: #f5e0dc;
        background: #313244;
        border-color: #45475a;
        outline: none;
      }
      #${PANEL_ID} .bsb-mermaid-scale {
        min-width: 42px;
        color: #89b4fa;
        text-align: center;
        font: 700 10px/1 ui-monospace, monospace;
        font-variant-numeric: tabular-nums;
      }
      #${PANEL_ID} .bsb-mermaid-viewport {
        position: relative;
        min-height: 220px;
        max-height: min(68vh, 720px);
        overflow: auto;
        overscroll-behavior: contain;
        scrollbar-width: thin;
        padding: 18px;
        background:
          linear-gradient(rgba(69, 71, 90, .18) 1px, transparent 1px),
          linear-gradient(90deg, rgba(69, 71, 90, .18) 1px, transparent 1px),
          #181825;
        background-size: 24px 24px;
      }
      #${PANEL_ID} .bsb-mermaid-stage {
        width: var(--bsb-mermaid-width, 760px);
        min-width: 1px;
        margin: 0 auto;
        transform-origin: top left;
      }
      #${PANEL_ID} .bsb-mermaid-svg {
        display: block;
        width: 100% !important;
        height: auto !important;
        max-width: none !important;
        overflow: visible;
        shape-rendering: geometricPrecision;
        text-rendering: geometricPrecision;
      }
      #${PANEL_ID} .bsb-mermaid-svg text,
      #${PANEL_ID} .bsb-mermaid-svg .label,
      #${PANEL_ID} .bsb-mermaid-svg .nodeLabel,
      #${PANEL_ID} .bsb-mermaid-svg .edgeLabel {
        font-family: Inter, "PingFang SC", "Microsoft YaHei", system-ui, sans-serif !important;
        -webkit-font-smoothing: antialiased;
      }
      #${PANEL_ID} .bsb-mermaid-hint {
        position: absolute;
        right: 10px;
        bottom: 8px;
        z-index: 2;
        pointer-events: none;
        padding: 3px 7px;
        border-radius: 6px;
        background: rgba(17, 17, 27, .78);
        color: #7f849c;
        font-size: 9px;
      }
      #${PANEL_ID} .bsb-mermaid-modal {
        position: fixed;
        inset: 0;
        z-index: 2147483647;
        pointer-events: auto;
        display: grid;
        place-items: stretch;
        padding: 18px;
        background: rgba(10, 10, 16, .88);
        backdrop-filter: blur(10px);
        -webkit-backdrop-filter: blur(10px);
      }
      #${PANEL_ID} .bsb-mermaid-modal .bsb-mermaid-card {
        width: 100%;
        height: 100%;
        max-width: none;
        border-radius: 14px;
        box-shadow: 0 25px 90px rgba(0, 0, 0, .55);
      }
      #${PANEL_ID} .bsb-mermaid-modal .bsb-mermaid-viewport {
        max-height: none;
        min-height: 0;
      }
      #${PANEL_ID} .bsb-mermaid-card[data-fit="fit"] .bsb-mermaid-stage {
        margin-inline: auto;
      }
      @media (max-width: 560px) {
        #${PANEL_ID} .bsb-mermaid-viewport { padding: 12px; min-height: 190px; }
        #${PANEL_ID} .bsb-mermaid-title { display: none; }
        #${PANEL_ID} .bsb-mermaid-modal { padding: 0; }
        #${PANEL_ID} .bsb-mermaid-modal .bsb-mermaid-card { border-radius: 0; }
      }
      /* KaTeX 数学公式（深色面板） */
      #${PANEL_ID} .bsb-ai-md .katex {
        color: var(--ctp-text);
        font-size: 1.12em;
      }
      #${PANEL_ID} .bsb-ai-md .bsb-katex-inline {
        display: inline;
        padding: 0 0.1em;
      }
      #${PANEL_ID} .bsb-ai-md .bsb-katex-display {
        display: block;
        margin: 1.2em 0;
        padding: 14px 12px;
        overflow-x: auto;
        overflow-y: hidden;
        text-align: center;
        border-radius: 12px;
        background: color-mix(in srgb, var(--ctp-mantle) 55%, transparent);
        border: 1px solid color-mix(in srgb, var(--ctp-surface0) 45%, transparent);
      }
      #${PANEL_ID} .bsb-ai-md .bsb-katex-display .katex-display {
        margin: 0;
      }
      #${PANEL_ID} .bsb-ai-md .katex-error {
        color: var(--ctp-red) !important;
      }
      #${PANEL_ID} .bsb-ai-md .bsb-math-fallback {
        color: var(--ctp-peach);
        white-space: pre-wrap;
        font-family: "JetBrains Mono", ui-monospace, monospace;
        font-size: 0.92em;
      }
      #${PANEL_ID} .bsb-ai-md pre.bsb-math-fallback {
        margin: 1em 0;
        padding: 12px 14px;
        border-radius: 12px;
        background: color-mix(in srgb, var(--ctp-mantle) 55%, transparent);
        border: 1px solid color-mix(in srgb, var(--ctp-peach) 28%, transparent);
      }
      #${PANEL_ID} .bsb-ai-md .bsb-code-lang {
        display: block; font-size: 10px; color: var(--ctp-overlay1);
        margin-bottom: 10px; text-transform: lowercase; letter-spacing: 0.06em;
      }
      #${PANEL_ID} .bsb-ai-md hr {
        border: none; height: 1px; margin: 1.6em 0;
        background: color-mix(in srgb, var(--ctp-surface1) 55%, transparent);
      }
      /* 阅读与渲染优化 */
      #${PANEL_ID}.ai-busy .bsb-sidebar,
      #${PANEL_ID} .bsb-sidebar.dragging,
      #${PANEL_ID} .bsb-sidebar.resizing {
        backdrop-filter: none;
        -webkit-backdrop-filter: none;
      }
      #${PANEL_ID} .bsb-ai-mode-row {
        display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
        margin: 0 0 10px;
      }
      #${PANEL_ID} .bsb-ai-mode-row label {
        display: inline-flex; align-items: center; gap: 7px;
        color: var(--ctp-subtext0); font-size: 11px;
      }
      #${PANEL_ID} .bsb-ai-mode-row select {
        min-width: 108px; border-radius: 9px; padding: 6px 9px;
        border: 1px solid color-mix(in srgb, var(--ctp-surface2) 55%, transparent);
        background: var(--ctp-mantle); color: var(--ctp-text); outline: none;
      }
      #${PANEL_ID} .bsb-ai-content {
        overflow-anchor: none;
        text-rendering: optimizeLegibility;
      }
      #${PANEL_ID} .bsb-ai-content > :not(.bsb-toc) {
        content-visibility: auto;
        contain-intrinsic-size: auto 160px;
      }
      #${PANEL_ID} .bsb-ai-md p,
      #${PANEL_ID} .bsb-ai-md li { text-wrap: pretty; }
      #${PANEL_ID} .bsb-time-link {
        appearance: none; display: inline-flex; align-items: center;
        margin: 0 .14em; padding: .12em .46em; border-radius: 999px;
        border: 1px solid color-mix(in srgb, var(--ctp-blue) 35%, transparent);
        background: color-mix(in srgb, var(--ctp-blue) 12%, transparent);
        color: var(--ctp-blue); font: 600 .78em/1.5 ui-monospace, monospace;
        cursor: pointer; vertical-align: .08em;
      }
      #${PANEL_ID} .bsb-time-link:hover,
      #${PANEL_ID} .bsb-time-link:focus-visible {
        background: color-mix(in srgb, var(--ctp-blue) 22%, transparent);
        outline: 2px solid color-mix(in srgb, var(--ctp-blue) 32%, transparent);
      }
      #${PANEL_ID} .bsb-toc {
        margin: 0 0 1.35em; padding: 10px 12px; border-radius: 12px;
        border: 1px solid color-mix(in srgb, var(--ctp-surface1) 55%, transparent);
        background: color-mix(in srgb, var(--ctp-mantle) 55%, transparent);
        content-visibility: visible;
      }
      #${PANEL_ID} .bsb-toc summary {
        cursor: pointer; color: var(--ctp-lavender); font-weight: 700;
      }
      #${PANEL_ID} .bsb-toc nav { display: grid; gap: 5px; margin-top: 9px; }
      #${PANEL_ID} .bsb-toc button {
        appearance: none; border: 0; background: transparent; color: var(--ctp-subtext1);
        text-align: left; cursor: pointer; padding: 3px 5px; border-radius: 6px;
        overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
      }
      #${PANEL_ID} .bsb-toc button[data-level="3"] { padding-left: 20px; }
      #${PANEL_ID} .bsb-toc button:hover { color: var(--ctp-text); background: var(--ctp-surface0); }
      #${PANEL_ID} .mermaid[data-bsb-state="pending"]::before {
        content: "图表将在进入视区时渲染"; color: var(--ctp-overlay1); font-size: 11px;
      }
      @media (max-width: 560px) {
        #${PANEL_ID}.open:not(.docked) .bsb-sidebar {
          left: 0 !important; top: 0 !important; width: 100vw !important; height: 100dvh !important;
          border-radius: 0;
        }
        #${PANEL_ID} .bsb-ai-hero { align-items: flex-start; }
      }
      @media (prefers-reduced-motion: reduce) {
        #${PANEL_ID} *, #${PANEL_ID} *::before, #${PANEL_ID} *::after {
          animation-duration: .001ms !important; animation-iteration-count: 1 !important;
          transition-duration: .001ms !important; scroll-behavior: auto !important;
        }
      }

      /* 设置页 */
      #${PANEL_ID} .bsb-settings {
        overflow: auto; flex: 1; min-height: 0;
        display: flex; flex-direction: column; gap: 12px;
        padding-right: 2px;
      }
      #${PANEL_ID} .bsb-card {
        border-radius: 14px; padding: 12px 14px;
        border: 1px solid color-mix(in srgb, var(--ctp-surface1) 45%, transparent);
        background: color-mix(in srgb, var(--ctp-mantle) 45%, transparent);
      }
      #${PANEL_ID} .bsb-card h3 {
        margin: 0 0 10px; font-size: 12px; font-weight: 700;
        color: var(--ctp-subtext1); letter-spacing: 0.04em; text-transform: uppercase;
      }
      #${PANEL_ID} .bsb-ai-config label,
      #${PANEL_ID} .bsb-field label {
        display: flex; flex-direction: column; gap: 4px;
        font-size: 11.5px; color: var(--ctp-subtext1); margin-bottom: 8px;
      }
      #${PANEL_ID} .bsb-ai-config input,
      #${PANEL_ID} .bsb-ai-config textarea,
      #${PANEL_ID} .bsb-field input {
        width: 100%; border-radius: 10px;
        border: 1px solid color-mix(in srgb, var(--ctp-surface2) 55%, transparent);
        background: color-mix(in srgb, var(--ctp-base) 60%, transparent);
        color: var(--ctp-text); padding: 8px 10px; font-size: 12.5px; font-family: inherit;
        outline: none;
      }
      #${PANEL_ID} .bsb-ai-config input:focus,
      #${PANEL_ID} .bsb-ai-config textarea:focus {
        border-color: color-mix(in srgb, var(--ctp-mauve) 50%, transparent);
        box-shadow: 0 0 0 3px color-mix(in srgb, var(--ctp-mauve) 16%, transparent);
      }
      #${PANEL_ID} .bsb-ai-config textarea {
        min-height: 88px; resize: vertical; line-height: 1.45;
      }
      #${PANEL_ID} .bsb-ai-config .row2 {
        display: grid; grid-template-columns: 1fr 1fr; gap: 8px;
      }
      #${PANEL_ID} .bsb-ai-cfg-actions {
        display: flex; gap: 8px; flex-wrap: wrap; margin-top: 4px;
      }

      /* 底栏状态 */
      #${PANEL_ID} .bsb-statusbar {
        flex-shrink: 0;
        display: flex; align-items: center; gap: 8px;
        padding: 8px 14px 10px;
        border-top: 1px solid color-mix(in srgb, var(--ctp-surface0) 75%, transparent);
        background: color-mix(in srgb, var(--ctp-mantle) 50%, transparent);
        font-size: 11.5px; color: var(--ctp-subtext0);
        min-height: 36px;
      }
      #${PANEL_ID} .bsb-status-dot {
        width: 7px; height: 7px; border-radius: 50%; flex-shrink: 0;
        background: var(--ctp-overlay0);
        box-shadow: 0 0 0 3px color-mix(in srgb, var(--ctp-overlay0) 20%, transparent);
      }
      #${PANEL_ID} .bsb-status-dot.ok {
        background: var(--ctp-green);
        box-shadow: 0 0 0 3px color-mix(in srgb, var(--ctp-green) 22%, transparent);
      }
      #${PANEL_ID} .bsb-status-dot.err {
        background: var(--ctp-red);
        box-shadow: 0 0 0 3px color-mix(in srgb, var(--ctp-red) 22%, transparent);
      }
      #${PANEL_ID} .bsb-status-dot.busy {
        background: var(--ctp-yellow);
        box-shadow: 0 0 0 3px color-mix(in srgb, var(--ctp-yellow) 22%, transparent);
        animation: bsb-pulse 1.2s ease-in-out infinite;
      }
      @keyframes bsb-pulse {
        0%, 100% { opacity: 1; }
        50% { opacity: 0.45; }
      }
      #${PANEL_ID} .bsb-status {
        flex: 1; min-width: 0; word-break: break-word; line-height: 1.35;
      }
      #${PANEL_ID} .bsb-status.ok { color: var(--ctp-green); }
      #${PANEL_ID} .bsb-status.err { color: var(--ctp-red); }

      /* 滚动条 */
      #${PANEL_ID} .bsb-list::-webkit-scrollbar,
      #${PANEL_ID} .bsb-ai-md::-webkit-scrollbar,
      #${PANEL_ID} .bsb-settings::-webkit-scrollbar { width: 8px; height: 8px; }
      #${PANEL_ID} .bsb-list::-webkit-scrollbar-thumb,
      #${PANEL_ID} .bsb-ai-md::-webkit-scrollbar-thumb,
      #${PANEL_ID} .bsb-settings::-webkit-scrollbar-thumb {
        background: color-mix(in srgb, var(--ctp-surface2) 65%, transparent);
        border-radius: 8px;
      }
      #${PANEL_ID} .bsb-list::-webkit-scrollbar-thumb:hover,
      #${PANEL_ID} .bsb-ai-md::-webkit-scrollbar-thumb:hover {
        background: var(--ctp-overlay0);
      }
    `);
  }

  function applyPanelGeometry() {
    const root = document.getElementById(PANEL_ID);
    if (!root || !state.ui) return;
    const sidebar = root.querySelector(".bsb-sidebar");
    if (!sidebar) return;
    const ui = clampUiToViewport(state.ui);
    root.style.setProperty("--bsb-note-font", `${Math.max(NOTE_FONT_MIN, Math.min(NOTE_FONT_MAX, Number(ui.noteFont) || 17))}px`);

    root.classList.toggle("open", !!state.open);
    root.classList.toggle("docked", !!ui.dock);
    root.classList.toggle("dock-expanded", !!(ui.dock && ui.dockExpanded));
    if (ui.dock) root.setAttribute("data-dock", ui.dock);
    else root.removeAttribute("data-dock");

    if (ui.dock && !ui.dockExpanded) {
      // 贴边收起：主面板不占位
      sidebar.style.left = "";
      sidebar.style.top = "";
      sidebar.style.width = "";
      sidebar.style.height = "";
    } else {
      // 悬浮 或 贴边展开
      if (ui.dock === "right") {
        sidebar.style.left = Math.max(0, window.innerWidth - ui.w - 8) + "px";
        sidebar.style.top = Math.max(8, Math.min(ui.y, window.innerHeight - ui.h - 8)) + "px";
      } else if (ui.dock === "left") {
        sidebar.style.left = "8px";
        sidebar.style.top = Math.max(8, Math.min(ui.y, window.innerHeight - ui.h - 8)) + "px";
      } else {
        sidebar.style.left = ui.x + "px";
        sidebar.style.top = ui.y + "px";
      }
      sidebar.style.width = ui.w + "px";
      sidebar.style.height = ui.h + "px";
      sidebar.style.right = "auto";
      sidebar.style.bottom = "auto";
    }

    sidebar.setAttribute(
      "aria-hidden",
      state.open && (!ui.dock || ui.dockExpanded) ? "false" : "true",
    );
    const fab = root.querySelector(".bsb-fab");
    if (fab) fab.setAttribute("aria-expanded", state.open || !!ui.dock ? "true" : "false");
  }

  function bindPanelChrome(root) {
    const sidebar = root.querySelector(".bsb-sidebar");
    const fab = root.querySelector(".bsb-fab");
    const head = root.querySelector(".bsb-head");
    const dockTab = root.querySelector(".bsb-dock-tab");
    let hideTimer = null;

    state.ui = loadUiGeom();

    function setOpen(open) {
      state.open = open;
      if (open) {
        // 从收起打开时，若已 dock 则展开；否则悬浮
        if (state.ui.dock) state.ui.dockExpanded = true;
        refreshContextUI();
      } else {
        closeMermaidFullscreen();
        state.ui.dock = null;
        state.ui.dockExpanded = false;
      }
      applyPanelGeometry();
      saveUiGeom();
    }

    function setDock(side) {
      // side: 'left' | 'right' | null
      if (side) {
        closeMermaidFullscreen();
        state.open = true;
        state.ui.dock = side;
        state.ui.dockExpanded = false;
        if (side === "right") {
          state.ui.x = Math.max(0, window.innerWidth - state.ui.w - 12);
        } else {
          state.ui.x = 12;
        }
      } else {
        state.ui.dock = null;
        state.ui.dockExpanded = false;
        state.open = true;
      }
      applyPanelGeometry();
      saveUiGeom();
      setStatus(
        side
          ? `已贴边收起（${side === "right" ? "右" : "左"}侧）· 点标签展开`
          : "已取消贴边 · 悬浮模式",
      );
    }

    function toggleDockExpanded(force) {
      if (!state.ui.dock) return;
      state.ui.dockExpanded =
        typeof force === "boolean" ? force : !state.ui.dockExpanded;
      state.open = true;
      applyPanelGeometry();
      if (state.ui.dockExpanded) refreshContextUI();
    }

    function scheduleAutoHide() {
      if (!state.ui.dock || !state.ui.dockExpanded) return;
      clearTimeout(hideTimer);
      hideTimer = setTimeout(() => {
        if (!state.ui.dock) return;
        // 指针仍在面板/标签上则不收
        const hover = root.querySelector(".bsb-sidebar:hover, .bsb-dock-tab:hover");
        if (hover) {
          scheduleAutoHide();
          return;
        }
        state.ui.dockExpanded = false;
        applyPanelGeometry();
      }, 700);
    }

    fab.addEventListener("click", () => {
      if (state.ui.dock) {
        toggleDockExpanded(true);
      } else {
        setOpen(!state.open);
      }
    });

    root.querySelector(".bsb-close").addEventListener("click", (e) => {
      e.stopPropagation();
      setOpen(false);
    });
    root.querySelector('[data-act="dock"]').addEventListener("click", (e) => {
      e.stopPropagation();
      if (state.ui.dock) {
        setDock(null);
      } else {
        // 靠近哪边就贴哪边，默认右
        const mid = state.ui.x + state.ui.w / 2;
        setDock(mid < window.innerWidth / 2 ? "left" : "right");
      }
    });
    root.querySelector('[data-act="collapse"]').addEventListener("click", (e) => {
      e.stopPropagation();
      if (!state.ui.dock) {
        const mid = state.ui.x + state.ui.w / 2;
        setDock(mid < window.innerWidth / 2 ? "left" : "right");
      } else {
        toggleDockExpanded(false);
      }
    });

    dockTab.addEventListener("click", () => toggleDockExpanded(true));
    dockTab.addEventListener("mouseenter", () => {
      clearTimeout(hideTimer);
      if (state.ui.dock && !state.ui.dockExpanded) toggleDockExpanded(true);
    });
    sidebar.addEventListener("mouseenter", () => clearTimeout(hideTimer));
    sidebar.addEventListener("mouseleave", () => scheduleAutoHide());
    dockTab.addEventListener("mouseleave", () => scheduleAutoHide());

    // ── drag ──
    let drag = null;
    head.addEventListener("pointerdown", (e) => {
      if (e.button !== 0) return;
      if (e.target.closest("button, select, input, a, label")) return;
      if (state.ui.dock) {
        // 贴边时拖标题：取消贴边进入悬浮
        state.ui.dock = null;
        state.ui.dockExpanded = false;
        state.open = true;
      }
      drag = {
        pid: e.pointerId,
        ox: e.clientX - state.ui.x,
        oy: e.clientY - state.ui.y,
      };
      head.setPointerCapture(e.pointerId);
      sidebar.classList.add("dragging");
      e.preventDefault();
    });
    head.addEventListener("pointermove", (e) => {
      if (!drag || e.pointerId !== drag.pid) return;
      state.ui.x = e.clientX - drag.ox;
      state.ui.y = e.clientY - drag.oy;
      clampUiToViewport(state.ui);
      applyPanelGeometry();
    });
    function endDrag(e) {
      if (!drag || (e && e.pointerId !== drag.pid)) return;
      drag = null;
      sidebar.classList.remove("dragging");
      // 贴边吸附
      if (state.ui.x <= DOCK_SNAP_PX) {
        setDock("left");
      } else if (state.ui.x + state.ui.w >= window.innerWidth - DOCK_SNAP_PX) {
        setDock("right");
      } else {
        saveUiGeom();
      }
    }
    head.addEventListener("pointerup", endDrag);
    head.addEventListener("pointercancel", endDrag);

    // ── resize ──
    let resize = null;
    root.querySelectorAll(".bsb-resize").forEach((handle) => {
      handle.addEventListener("pointerdown", (e) => {
        if (e.button !== 0 || state.ui.dock) return;
        const dir = handle.getAttribute("data-dir");
        resize = {
          pid: e.pointerId,
          dir,
          sx: e.clientX,
          sy: e.clientY,
          ox: state.ui.x,
          oy: state.ui.y,
          ow: state.ui.w,
          oh: state.ui.h,
        };
        handle.setPointerCapture(e.pointerId);
        sidebar.classList.add("resizing");
        e.preventDefault();
        e.stopPropagation();
      });
      handle.addEventListener("pointermove", (e) => {
        if (!resize || e.pointerId !== resize.pid) return;
        const dx = e.clientX - resize.sx;
        const dy = e.clientY - resize.sy;
        let { x, y, w, h } = {
          x: resize.ox,
          y: resize.oy,
          w: resize.ow,
          h: resize.oh,
        };
        const d = resize.dir;
        if (d.includes("e")) w = resize.ow + dx;
        if (d.includes("s")) h = resize.oh + dy;
        if (d.includes("w")) {
          w = resize.ow - dx;
          x = resize.ox + dx;
        }
        if (d.includes("n")) {
          h = resize.oh - dy;
          y = resize.oy + dy;
        }
        if (w < MIN_W) {
          if (d.includes("w")) x = resize.ox + (resize.ow - MIN_W);
          w = MIN_W;
        }
        if (h < MIN_H) {
          if (d.includes("n")) y = resize.oy + (resize.oh - MIN_H);
          h = MIN_H;
        }
        state.ui.x = x;
        state.ui.y = y;
        state.ui.w = w;
        state.ui.h = h;
        clampUiToViewport(state.ui);
        applyPanelGeometry();
      });
      function endResize(e) {
        if (!resize || (e && e.pointerId !== resize.pid)) return;
        resize = null;
        sidebar.classList.remove("resizing");
        saveUiGeom();
      }
      handle.addEventListener("pointerup", endResize);
      handle.addEventListener("pointercancel", endResize);
    });

    window.addEventListener("resize", () => {
      if (!state.ui) return;
      clampUiToViewport(state.ui);
      applyPanelGeometry();
    });

    // 若上次是 dock，启动时只显示贴边标签
    if (state.ui.dock) {
      state.open = true;
      state.ui.dockExpanded = false;
    }
    applyPanelGeometry();
    // 恢复工作区（AI / 字幕 / 设置）
    try {
      setWorkspace(state.ui.view || "ai", { silent: true });
    } catch (_) {
      /* ensurePanel may not be fully wired yet */
    }

    // expose for external refresh
    root._bsbSetOpen = setOpen;
    root._bsbSetDock = setDock;
  }

  function ensurePanel() {
    let root = document.getElementById(PANEL_ID);
    if (root) return root;
    injectStyles();
    root = document.createElement("div");
    root.id = PANEL_ID;
    root.setAttribute("data-ctp-flavor", "mocha");
    root.innerHTML = `
      <button type="button" class="bsb-dock-tab" title="展开 SubBatch 工作台">AI · CC</button>
      <aside class="bsb-sidebar" role="complementary" aria-label="Bili SubBatch Workspace" aria-hidden="true">
        <div class="bsb-resize n" data-dir="n"></div>
        <div class="bsb-resize s" data-dir="s"></div>
        <div class="bsb-resize e" data-dir="e"></div>
        <div class="bsb-resize w" data-dir="w"></div>
        <div class="bsb-resize ne" data-dir="ne"></div>
        <div class="bsb-resize nw" data-dir="nw"></div>
        <div class="bsb-resize se" data-dir="se"></div>
        <div class="bsb-resize sw" data-dir="sw"></div>
        <div class="bsb-head">
          <div class="bsb-head-title">
            <span class="bsb-logo">CC</span>
            <strong>SubBatch</strong>
            <span class="bsb-ver">v${SCRIPT_VERSION}</span>
          </div>
          <div class="bsb-head-actions">
            <button type="button" class="bsb-icon-btn" data-act="dock" title="贴边收起">⧉</button>
            <button type="button" class="bsb-icon-btn" data-act="collapse" title="收起到侧边">—</button>
            <button type="button" class="bsb-icon-btn bsb-close" title="关闭" aria-label="关闭">×</button>
          </div>
        </div>
        <nav class="bsb-nav" aria-label="工作区">
          <button type="button" data-view="ai" class="active">AI 笔记</button>
          <button type="button" data-view="subs">字幕库</button>
          <button type="button" data-view="settings">设置</button>
        </nav>
        <div class="bsb-body">
          <!-- AI 主画布 -->
          <section class="bsb-view active" data-view-panel="ai">
            <div class="bsb-ai-hero">
              <div>
                <h2>AI 字幕笔记</h2>
                <p>勾选字幕 → 一键分析 · Markdown / 代码高亮 / Mermaid</p>
              </div>
              <div class="bsb-ai-hero-actions">
                <button type="button" class="bsb-btn danger" data-act="ai-stop" style="display:none">停止</button>
                <button type="button" class="bsb-btn accent" data-act="ai-send" title="分析已勾选字幕">开始分析</button>
              </div>
            </div>
            <div class="bsb-chips" data-role="ai-chips">
              <span class="bsb-chip">选中 <em data-role="chip-sel">0</em></span>
              <span class="bsb-chip">有字幕 <em data-role="chip-ok">0</em></span>
              <span class="bsb-chip" data-role="chip-model">model</span>
            </div>
            <div class="bsb-ai-mode-row">
              <label>笔记模式
                <select data-role="note-mode">
                  <option value="deep" selected>深度笔记</option>
                  <option value="concise">精炼摘要</option>
                  <option value="study">学习指南</option>
                  <option value="action">行动清单</option>
                  <option value="mermaid">全 Mermaid 学习图谱</option>
                </select>
              </label>
              <span class="bsb-chip" data-role="note-mode-hint">时间戳证据 · 安全渲染 · 按需图表</span>
            </div>
            <div class="bsb-ai-canvas-wrap">
              <div class="bsb-ai-canvas-bar">
                <span class="bsb-bar-left">
                  <span class="bsb-live-dot" aria-hidden="true"></span>
                  <span>Output</span>
                  <span data-role="ai-canvas-meta">就绪</span>
                </span>
                <span class="bsb-bar-actions">
                  <button type="button" class="bsb-mini on" data-act="ai-stick" title="跟随最新 / 暂停跟随（上滑也会自动暂停）">粘底</button>
                  <button type="button" class="bsb-mini" data-act="ai-copy" title="复制当前输出">复制</button>
                  <button type="button" class="bsb-mini" data-act="ai-export" title="导出 Markdown">导出</button>
                  <button type="button" class="bsb-mini" data-act="ai-font-dec" title="减小正文字号">A−</button>
                  <button type="button" class="bsb-mini" data-act="ai-font-inc" title="增大正文字号">A+</button>
                  <button type="button" class="bsb-mini" data-act="ai-top" title="回到顶部">顶部</button>
                </span>
              </div>
              <div class="bsb-ai-stream" data-role="ai-stream">
                <pre class="bsb-ai-raw" data-role="ai-raw" hidden></pre>
                <div class="bsb-ai-md" data-role="ai-md">
                  <div class="bsb-ai-content" data-role="ai-content" aria-label="AI 笔记输出">
                    <div class="bsb-empty">
                      <div class="bsb-empty-ico">✦</div>
                      <strong>还没有分析结果</strong>
                      <span>在「字幕库」扫描并勾选，再点「开始分析」。生成中上滑即可自由阅读（不会被拽回底部）；跟随时点「↓ 最新」。</span>
                    </div>
                  </div>
                  <div class="bsb-ai-anchor" data-role="ai-anchor"></div>
                </div>
                <button type="button" class="bsb-jump-latest" data-act="ai-jump" title="跳到最新输出">↓ 最新</button>
              </div>
            </div>
          </section>

          <!-- 字幕库 -->
          <section class="bsb-view" data-view-panel="subs">
            <div>
              <span class="bsb-badge" data-role="type">—</span>
              <div class="bsb-meta" data-role="ctx">—</div>
            </div>
            <div class="bsb-mode-row">
              <label>模式
                <select data-role="mode" title="自动识别或强制类型">
                  <option value="auto" selected>自动识别</option>
                  <option value="video">单个视频</option>
                  <option value="selection">视频选集</option>
                  <option value="user">个人主页</option>
                  <option value="favorite">收藏夹</option>
                  <option value="collection">合集</option>
                  <option value="search">搜索页</option>
                </select>
              </label>
              <span class="bsb-auto-hint" data-role="auto-hint">识别：—</span>
            </div>
            <div class="bsb-toolbar">
              <button type="button" class="primary" data-act="scan">扫描当前页</button>
              <button type="button" data-act="sel-all">全选</button>
              <button type="button" data-act="sel-none">全不选</button>
              <button type="button" class="danger" data-act="cancel" style="display:none">停止</button>
              <button type="button" data-act="ai-send" title="用勾选项跑 AI">送去 AI</button>
            </div>
            <div class="bsb-opts">
              <label class="bsb-auto-capture"><input type="checkbox" data-role="auto-capture" checked> 打开视频自动抓字幕</label>
              <label><input type="checkbox" data-role="auto-analyze" checked> 抓到字幕后自动分析</label>
              <label><input type="checkbox" data-role="player-subtitle" checked> 自动开启播放器字幕</label>
              <label>最多页 <input type="number" data-role="max-pages" min="1" max="100" value="${DEFAULT_MAX_PAGES}"></label>
              <label>间隔ms <input type="number" data-role="delay" min="0" max="5000" step="50" value="${DEFAULT_DELAY_MS}"></label>
            </div>
            <div class="bsb-list" data-role="list">
              <div class="bsb-empty">
                <div class="bsb-empty-ico">≡</div>
                <strong>字幕库为空</strong>
                <span>视频页会自动抓取；其他页面点「扫描当前页」加载列表</span>
              </div>
            </div>
            <section class="bsb-transcript-shell" aria-label="当前视频字幕时间轴">
              <div class="bsb-transcript-head">
                <div class="bsb-transcript-title">
                  <strong data-role="transcript-title">当前视频字幕</strong>
                  <span data-role="transcript-meta">等待自动读取 · 四级缓存已启用</span>
                </div>
              </div>
              <div class="bsb-transcript-tools">
                <label class="bsb-transcript-search" title="检索当前视频字幕">
                  <span>⌕</span>
                  <input type="search" data-role="transcript-search" placeholder="检索字幕…" autocomplete="off">
                  <span class="bsb-transcript-count" data-role="transcript-count"></span>
                </label>
                <select class="bsb-transcript-track" data-role="transcript-track" aria-label="字幕语言" disabled>
                  <option>自动</option>
                </select>
                <label class="bsb-transcript-follow"><input type="checkbox" data-role="transcript-follow" checked> 跟随播放</label>
                <button type="button" class="bsb-transcript-refresh" data-act="transcript-refresh" title="忽略缓存重新读取">刷新</button>
              </div>
              <div class="bsb-transcript-list" data-role="transcript-list">
                <div class="bsb-transcript-empty">打开有字幕的视频后会自动显示全文。<br>点击时间即可跳转到播放器对应位置。</div>
              </div>
            </section>
            <div class="bsb-actions">
              <button type="button" class="primary" data-act="dl-srt">下载 SRT</button>
              <button type="button" data-act="dl-txt">下载 TXT</button>
              <button type="button" data-act="copy">复制全文</button>
              <button type="button" data-act="copy-bvid">复制 BV</button>
              <button type="button" data-act="dl-ok-only">再下成功项</button>
              <button type="button" data-act="clear">清空</button>
            </div>
          </section>

          <!-- 设置 -->
          <section class="bsb-view" data-view-panel="settings">
            <div class="bsb-settings">
              <div class="bsb-card">
                <h3>OpenAI 兼容 API</h3>
                <div class="bsb-ai-config" data-role="ai-config">
                  <label>Base URL（含 /v1）
                    <input type="text" data-ai="baseUrl" placeholder="https://api.example.com/v1" autocomplete="off">
                  </label>
                  <label>API Key
                    <input type="password" data-ai="apiKey" placeholder="sk-..." autocomplete="off">
                  </label>
                  <label>Model
                    <input type="text" data-ai="model" placeholder="gpt-4o-mini" autocomplete="off">
                  </label>
                  <div class="row2">
                    <label>Temperature
                      <input type="number" data-ai="temperature" min="0" max="2" step="0.1">
                    </label>
                    <label>Max tokens
                      <input type="number" data-ai="maxTokens" min="256" max="128000" step="256">
                    </label>
                  </div>
                  <label style="flex-direction:row;align-items:center;gap:8px;margin-bottom:10px">
                    <input type="checkbox" data-ai="stream" style="width:auto" checked>
                    流式输出（默认开：防止 client_gone 断连；仅调试可关）
                  </label>
                  <label>System 提示词
                    <textarea data-ai="systemPrompt" rows="4"></textarea>
                  </label>
                  <label>User 模板（{{modeInstruction}} {{title}} {{bvid}} {{author}} {{subtitle}}）
                    <textarea data-ai="userPromptTemplate" rows="5"></textarea>
                  </label>
                  <div class="bsb-ai-cfg-actions">
                    <button type="button" class="bsb-btn primary" data-act="ai-save">保存配置</button>
                    <button type="button" class="bsb-btn ghost" data-act="ai-reset">恢复默认</button>
                  </div>
                </div>
              </div>
              <div class="bsb-card">
                <h3>交互提示</h3>
                <p style="margin:0;font-size:12px;color:var(--ctp-subtext0);line-height:1.55">
                  拖标题栏移动 · 拖边角拉伸 · 贴左右边自动收起。<br>
                  AI 笔记为主画布；字幕库负责扫描与导出；密钥优先存入油猴隔离存储。
                </p>
              </div>
            </div>
          </section>
        </div>
        <div class="bsb-statusbar">
          <span class="bsb-status-dot" data-role="status-dot"></span>
          <div class="bsb-status" data-role="status" role="status" aria-live="polite">就绪 · AI 工作台</div>
        </div>
      </aside>
      <button type="button" class="bsb-fab" title="SubBatch 工作台" aria-expanded="false">CC</button>
    `;
    document.documentElement.appendChild(root);
    bindPanelChrome(root);

    root.querySelectorAll("[data-view]").forEach((btn) => {
      btn.addEventListener("click", () => setWorkspace(btn.getAttribute("data-view")));
    });

    root.querySelector('[data-role="max-pages"]').addEventListener("change", (e) => {
      state.maxPages = Math.max(1, Math.min(100, Number(e.target.value) || DEFAULT_MAX_PAGES));
    });
    root.querySelector('[data-role="delay"]').addEventListener("change", (e) => {
      state.delayMs = Math.max(0, Math.min(5000, Number(e.target.value) || DEFAULT_DELAY_MS));
    });
    const autoCaptureInput = root.querySelector('[data-role="auto-capture"]');
    if (autoCaptureInput) {
      autoCaptureInput.checked = state.autoCaptureEnabled;
      autoCaptureInput.addEventListener("change", (e) => {
        state.autoCaptureEnabled = !!e.target.checked;
        saveAutoCaptureSetting(state.autoCaptureEnabled);
        if (state.autoCaptureEnabled) {
          setStatus("已开启：打开视频自动抓字幕");
          scheduleAutoCapture("setting-enabled", 0);
        } else {
          state.autoCaptureAbortController?.abort();
          clearTimeout(state.autoCaptureTimer);
          setStatus("已关闭自动抓取；仍可手动扫描", "ok");
        }
      });
    }
    const autoAnalyzeInput = root.querySelector('[data-role="auto-analyze"]');
    if (autoAnalyzeInput) {
      autoAnalyzeInput.checked = state.autoAnalyzeEnabled;
      autoAnalyzeInput.addEventListener("change", (e) => {
        state.autoAnalyzeEnabled = !!e.target.checked;
        saveAutoAnalyzeSetting(state.autoAnalyzeEnabled);
        clearTimeout(state.autoAnalyzeTimer);
        state.autoAnalyzePendingKey = "";
        if (state.autoAnalyzeEnabled) {
          state.autoAnalyzeKey = "";
          const item = currentTranscriptItem();
          if (item?.subStatus === "ok" && item.data?.length) {
            scheduleAutoAnalyze(item, routeVideoKey(item.bvid, item.page || 1), "setting-enabled", 0);
          } else {
            setStatus("已开启：抓到字幕后自动开始分析");
          }
        } else {
          setStatus("已关闭自动分析；仍可点击“开始分析”", "ok");
        }
      });
    }
    const playerSubtitleInput = root.querySelector('[data-role="player-subtitle"]');
    if (playerSubtitleInput) {
      playerSubtitleInput.checked = state.autoEnablePlayerSubtitle;
      playerSubtitleInput.addEventListener("change", (e) => {
        state.autoEnablePlayerSubtitle = !!e.target.checked;
        storageSet(PLAYER_SUBTITLE_STORE_KEY, state.autoEnablePlayerSubtitle ? "true" : "false");
        if (state.autoEnablePlayerSubtitle) enablePlayerSubtitle(currentTranscriptItem()).catch(() => {});
      });
    }

    root.querySelector('[data-role="mode"]').addEventListener("change", (e) => {
      state.mode = e.target.value || "auto";
      refreshContextUI();
      setStatus(
        state.mode === "auto"
          ? "已切回自动识别（默认偏单个视频）"
          : `已手动指定：${TYPE_LABEL[state.mode] || state.mode}`,
      );
    });

    const noteModeSel = root.querySelector('[data-role="note-mode"]');
    if (noteModeSel) {
      const initialMode = NOTE_MODE_OPTIONS.includes(state.ui?.noteMode)
        ? state.ui.noteMode
        : "deep";
      noteModeSel.value = initialMode;
      updateNoteModeUi(root, initialMode);
      noteModeSel.addEventListener("change", (e) => {
        const mode = NOTE_MODE_OPTIONS.includes(e.target.value) ? e.target.value : "deep";
        if (state.ui) state.ui.noteMode = mode;
        updateNoteModeUi(root, mode);
        saveUiGeom();
        setStatus(`笔记模式：${noteModeLabel(mode)}`);
      });
    }

    const listBox = root.querySelector('[data-role="list"]');
    if (listBox) {
      listBox.addEventListener("change", (e) => {
        const cb = e.target.closest?.('input[type="checkbox"][data-i]');
        if (!cb) return;
        const i = Number(cb.getAttribute("data-i"));
        if (state.items[i]) state.items[i].selected = cb.checked;
        refreshAiChips();
      });
      listBox.addEventListener("click", (e) => {
        const open = e.target.closest?.("[data-transcript-i]");
        if (!open) return;
        const item = state.items[Number(open.dataset.transcriptI)];
        if (item) selectTranscriptItem(item, { focusSearch: true });
      });
    }

    const transcriptSearch = root.querySelector('[data-role="transcript-search"]');
    transcriptSearch?.addEventListener("input", debounce((e) => {
      state.transcriptQuery = String(e.target.value || "").trim();
      renderTranscriptPanel();
    }, 80));
    root.querySelector('[data-role="transcript-track"]')?.addEventListener("change", (e) => {
      switchTranscriptTrack(Number(e.target.value)).catch((error) => {
        if (error?.name !== "AbortError") setStatus(`切换字幕失败: ${error.message || error}`, "err");
      });
    });
    const followInput = root.querySelector('[data-role="transcript-follow"]');
    if (followInput) {
      followInput.checked = state.transcriptAutoFollow;
      followInput.addEventListener("change", (e) => {
        state.transcriptAutoFollow = !!e.target.checked;
        storageSet(TRANSCRIPT_FOLLOW_STORE_KEY, state.transcriptAutoFollow ? "true" : "false");
        if (state.transcriptAutoFollow) updateTranscriptActiveCue(currentVideoTime(), true);
      });
    }
    const transcriptList = root.querySelector('[data-role="transcript-list"]');
    transcriptList?.addEventListener("click", (e) => {
      const timeButton = e.target.closest?.("[data-transcript-time]");
      if (!timeButton) return;
      seekTranscriptTime(Number(timeButton.dataset.transcriptTime));
    });
    transcriptList?.addEventListener("wheel", () => {
      state.transcriptUserScrollUntil = Date.now() + 3500;
    }, { passive: true });
    transcriptList?.addEventListener("touchmove", () => {
      state.transcriptUserScrollUntil = Date.now() + 3500;
    }, { passive: true });

    root.addEventListener("click", (e) => {
      const mermaidTool = e.target.closest?.("[data-mmd-act]");
      if (mermaidTool) {
        e.preventDefault();
        handleMermaidTool(mermaidTool);
        return;
      }
      const ts = e.target.closest?.(".bsb-time-link");
      if (!ts) return;
      e.preventDefault();
      seekToVideoTimestamp(
        Number(ts.dataset.seconds),
        ts.dataset.bvid || "",
        Number(ts.dataset.page) || 1,
      );
    });
    root.addEventListener("wheel", (e) => {
      const viewport = e.target.closest?.(".bsb-mermaid-viewport");
      if (!viewport || !e.ctrlKey) return;
      const card = viewport.closest(".bsb-mermaid-card");
      if (!card) return;
      e.preventDefault();
      setMermaidScale(card, getMermaidScale(card) + (e.deltaY < 0 ? 0.12 : -0.12));
    }, { passive: false });
    root.addEventListener("keydown", (e) => {
      if (e.key === "Escape") closeMermaidFullscreen();
    });

    root.querySelectorAll("button[data-act]").forEach((btn) => {
      const act = btn.getAttribute("data-act");
      if (act === "dock" || act === "collapse") return;
      btn.addEventListener("click", () => onAction(act));
    });

    fillAiConfigForm(root);
    bindAiScrollBehavior(root);
    setWorkspace((state.ui && state.ui.view) || "ai", { silent: true });
    refreshAiChips();
    return root;
  }

  function setWorkspace(view, opts) {
    const v = ["ai", "subs", "settings"].includes(view) ? view : "ai";
    const root = ensurePanel();
    if (state.ui) state.ui.view = v;
    root.querySelectorAll(".bsb-nav [data-view]").forEach((b) => {
      b.classList.toggle("active", b.getAttribute("data-view") === v);
    });
    root.querySelectorAll("[data-view-panel]").forEach((p) => {
      p.classList.toggle("active", p.getAttribute("data-view-panel") === v);
    });
    if (!opts?.silent) {
      saveUiGeom();
      if (v === "ai") refreshAiChips();
      if (v === "subs") {
        renderTranscriptPanel();
        bindTranscriptVideoEvents();
      }
      if (v === "settings") fillAiConfigForm(root);
    }
  }

  function refreshAiChips() {
    const root = document.getElementById(PANEL_ID);
    if (!root) return;
    const sel = state.items.filter((it) => it.selected).length;
    const ok = state.items.filter((it) => it.selected && it.subStatus === "ok").length;
    const elSel = root.querySelector('[data-role="chip-sel"]');
    const elOk = root.querySelector('[data-role="chip-ok"]');
    const elModel = root.querySelector('[data-role="chip-model"]');
    if (elSel) elSel.textContent = String(sel);
    if (elOk) elOk.textContent = String(ok);
    if (elModel) {
      const cfg = state.ai || loadAiConfig();
      elModel.textContent = (cfg.model || "model").slice(0, 28);
    }
  }

  function setStatus(text, cls) {
    const el = document.querySelector(`#${PANEL_ID} [data-role="status"]`);
    if (!el) return;
    el.textContent = text;
    el.classList.remove("ok", "err");
    if (cls) el.classList.add(cls);
    const dot = document.querySelector(`#${PANEL_ID} [data-role="status-dot"]`);
    if (dot) {
      dot.classList.remove("ok", "err", "busy");
      if (cls === "ok") dot.classList.add("ok");
      else if (cls === "err") dot.classList.add("err");
      else if (state.busy || state.aiBusy) dot.classList.add("busy");
    }
    const meta = document.querySelector(`#${PANEL_ID} [data-role="ai-canvas-meta"]`);
    if (meta && (state.ui?.view === "ai" || !state.ui)) {
      meta.textContent = (text || "").slice(0, 48);
    }
  }

  function setBusy(busy) {
    state.busy = busy;
    const root = ensurePanel();
    const allow = new Set(["cancel", "ai-stop", "dock", "collapse"]);
    root.querySelectorAll("button[data-act]").forEach((b) => {
      const act = b.getAttribute("data-act");
      if (act === "cancel") {
        b.style.display = busy && !state.aiBusy ? "" : "none";
        b.disabled = false;
      } else if (allow.has(act)) {
        b.disabled = false;
      } else {
        b.disabled = !!busy;
      }
    });
  }

  function refreshContextUI() {
    const root = ensurePanel();
    const modeSel = root.querySelector('[data-role="mode"]');
    if (modeSel) {
      // 下拉为源：用户手动选择会保留；非法值回退 state.mode
      if (MODE_OPTIONS.includes(modeSel.value)) state.mode = modeSel.value;
      else modeSel.value = state.mode || "auto";
    }

    const auto = detectContext(location.href);
    state.autoCtx = auto;
    const ctx = resolveContext();
    state.ctx = ctx;

    const badge = root.querySelector('[data-role="type"]');
    badge.textContent =
      (ctx.source === "manual" ? "手动 · " : "自动 · ") +
      (TYPE_LABEL[ctx.type] || ctx.type);
    badge.classList.toggle("manual", ctx.source === "manual");

    root.querySelector('[data-role="ctx"]').textContent = formatCtxBits(ctx);

    const hint = root.querySelector('[data-role="auto-hint"]');
    if (hint) {
      const autoLabel = TYPE_LABEL[auto.type] || auto.type;
      if (state.mode === "auto") {
        hint.innerHTML = `识别：<strong>${escapeHtml(autoLabel)}</strong>`;
      } else {
        hint.innerHTML = `自动本会是：<strong>${escapeHtml(autoLabel)}</strong>（已手动覆盖）`;
      }
    }
  }

  function renderList() {
    const box = document.querySelector(`#${PANEL_ID} [data-role="list"]`);
    if (!box) return;
    if (!state.items.length) {
      box.innerHTML = `<div class="bsb-empty">列表为空 · 视频页将自动抓取，其他页面可手动扫描</div>`;
      renderTranscriptPanel();
      return;
    }
    const rows = state.items
      .map((it, i) => {
        const st = it.subStatus || "wait";
        const stClass =
          st === "ok" ? "st-ok" : st === "empty" ? "st-empty" : st === "error" ? "st-err" : "st-wait";
        const stText =
          st === "ok"
            ? `${it.cue_count || 0}条`
            : st === "empty"
              ? "无字幕"
              : st === "error"
                ? "失败"
                : "—";
        const active = routeVideoKey(it.bvid, it.page || 1) === state.transcriptItemKey;
        return `<tr data-i="${i}">
          <td><input type="checkbox" data-i="${i}" ${it.selected ? "checked" : ""}></td>
          <td class="bsb-t" title="${escapeAttr(it.title)}">${escapeHtml(it.title || it.bvid)}</td>
          <td>${escapeHtml(it.bvid)}${it.page > 1 ? " P" + it.page : ""}</td>
          <td class="${stClass}">${stText}</td>
          <td><button type="button" class="bsb-open-transcript" data-transcript-i="${i}" ${st !== "ok" ? "disabled" : ""}>${active ? "查看中" : "字幕"}</button></td>
        </tr>`;
      })
      .join("");
    box.innerHTML = `<table>
      <thead><tr>
        <th style="width:28px"></th><th>标题</th><th style="width:96px">BV</th><th style="width:48px">状态</th><th style="width:56px"></th>
      </tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
    refreshAiChips();
    renderTranscriptPanel();
  }

  function waitForPlayerElement(selector, timeout = 8000) {
    return new Promise((resolve, reject) => {
      const existing = document.querySelector(selector);
      if (existing) return resolve(existing);
      const observer = new MutationObserver(() => {
        const element = document.querySelector(selector);
        if (!element) return;
        observer.disconnect();
        clearTimeout(timer);
        resolve(element);
      });
      const timer = window.setTimeout(() => {
        observer.disconnect();
        reject(new Error(`等待播放器元素超时: ${selector}`));
      }, timeout);
      observer.observe(document.documentElement, { childList: true, subtree: true });
    });
  }

  async function enablePlayerSubtitle(item) {
    if (!state.autoEnablePlayerSubtitle) return;
    if (state.playerSubtitleOperation) return state.playerSubtitleOperation;
    state.playerSubtitleOperation = (async () => {
      const button = await waitForPlayerElement(PLAYER_SUBTITLE_SELECTORS.button, 9000);
      let panel = document.querySelector(PLAYER_SUBTITLE_SELECTORS.panel);
      if (!panel || panel.offsetParent === null) {
        button.click();
        panel = await waitForPlayerElement(PLAYER_SUBTITLE_SELECTORS.panel, 2500);
        await sleep(100);
      }
      const active = panel.querySelector(PLAYER_SUBTITLE_SELECTORS.active);
      if (!active) {
        const items = Array.from(panel.querySelectorAll(PLAYER_SUBTITLE_SELECTORS.item));
        if (!items.length) throw new Error("播放器没有可开启字幕");
        const wanted = String(item?.lan || "");
        const target = (wanted && items.find((node) => node.dataset.lan === wanted))
          || items.find((node) => /^(ai-zh|zh-CN|zh-Hans|zh)$/i.test(node.dataset.lan || ""))
          || items[0];
        target.click();
        await sleep(140);
      }
      if (panel.offsetParent !== null) button.click();
    })().catch((error) => {
      console.debug("[bili-subbatch] player subtitle not enabled", error?.message || error);
    }).finally(() => {
      state.playerSubtitleOperation = null;
    });
    return state.playerSubtitleOperation;
  }

  function formatTranscriptTime(seconds, withHours = false) {
    const total = Math.max(0, Math.floor(Number(seconds) || 0));
    const h = Math.floor(total / 3600);
    const m = Math.floor((total % 3600) / 60);
    const sec = total % 60;
    if (withHours || h > 0) {
      return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}`;
    }
    return `${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}`;
  }

  function currentTranscriptItem() {
    if (state.transcriptItemKey) {
      const exact = state.items.find((item) => routeVideoKey(item.bvid, item.page || 1) === state.transcriptItemKey);
      return exact || null;
    }
    return state.items.find((item) => item.subStatus === "ok" && item.data?.length) || null;
  }

  function selectTranscriptItem(item, { focusSearch = false } = {}) {
    if (!item) return;
    state.transcriptItemKey = routeVideoKey(item.bvid, item.page || 1);
    state.transcriptQuery = "";
    state.transcriptFilteredIndexes = null;
    state.transcriptActiveCueIndex = -1;
    const root = ensurePanel();
    const input = root.querySelector('[data-role="transcript-search"]');
    if (input) input.value = "";
    renderList();
    bindTranscriptVideoEvents();
    updateTranscriptActiveCue(currentVideoTime(), true);
    if (focusSearch) {
      setWorkspace("subs");
      input?.focus();
    }
  }

  function appendTranscriptHighlightedText(container, text, query) {
    if (!query) {
      container.textContent = text;
      return;
    }
    const source = String(text || "");
    const lower = source.toLocaleLowerCase();
    const needle = query.toLocaleLowerCase();
    let cursor = 0;
    let index = lower.indexOf(needle);
    while (index >= 0) {
      if (index > cursor) container.append(document.createTextNode(source.slice(cursor, index)));
      const mark = document.createElement("mark");
      mark.textContent = source.slice(index, index + query.length);
      container.append(mark);
      cursor = index + query.length;
      index = lower.indexOf(needle, cursor);
    }
    if (cursor < source.length) container.append(document.createTextNode(source.slice(cursor)));
  }

  function populateTranscriptTrackSelect(item) {
    const select = document.querySelector(`#${PANEL_ID} [data-role="transcript-track"]`);
    if (!select) return;
    const tracks = Array.isArray(item?.tracks) ? item.tracks : [];
    select.replaceChildren();
    if (!tracks.length) {
      select.appendChild(new Option(item?.lan_doc || item?.lan || "默认字幕", "0"));
      select.disabled = true;
      return;
    }
    tracks.forEach((track, index) => {
      select.appendChild(new Option(track.lan_doc || track.lan || `字幕 ${index + 1}`, String(index)));
    });
    const active = Number.isInteger(item.activeTrackIndex) ? item.activeTrackIndex : preferredTrackIndex(tracks);
    state.transcriptTrackIndex = active;
    select.value = String(Math.max(0, active));
    select.disabled = tracks.length <= 1;
  }

  function filterTranscriptIndexes(cues, query) {
    const needle = String(query || "").trim().toLocaleLowerCase();
    const indexes = [];
    (cues || []).forEach((cue, index) => {
      if (!needle || String(cue?.content || "").toLocaleLowerCase().includes(needle)) indexes.push(index);
    });
    return indexes;
  }

  function renderTranscriptPanel() {
    const root = document.getElementById(PANEL_ID);
    if (!root) return;
    const list = root.querySelector('[data-role="transcript-list"]');
    const title = root.querySelector('[data-role="transcript-title"]');
    const meta = root.querySelector('[data-role="transcript-meta"]');
    const count = root.querySelector('[data-role="transcript-count"]');
    if (!list || !title || !meta || !count) return;

    const item = currentTranscriptItem();
    if (!item || item.subStatus !== "ok" || !item.data?.length) {
      title.textContent = "当前视频字幕";
      meta.textContent = state.items.some((x) => x.subStatus === "wait")
        ? "正在通过四级缓存与直读链路加载…"
        : "等待有字幕的视频";
      count.textContent = "";
      populateTranscriptTrackSelect(null);
      list.innerHTML = `<div class="bsb-transcript-empty">打开有字幕的视频后会自动显示全文。<br>点击时间即可跳转到播放器对应位置。</div>`;
      return;
    }

    if (!state.transcriptItemKey) state.transcriptItemKey = routeVideoKey(item.bvid, item.page || 1);
    title.textContent = item.title || `${item.bvid} 字幕`;
    meta.textContent = `${item.cue_count || item.data.length} 条 · ${item.lan_doc || item.lan || "字幕"} · ${item.cachePath || item.source || "已加载"}`;
    populateTranscriptTrackSelect(item);

    const query = state.transcriptQuery.trim();
    const indexes = filterTranscriptIndexes(item.data, query);
    state.transcriptFilteredIndexes = query ? indexes : null;
    count.textContent = query ? `${indexes.length}/${item.data.length}` : String(item.data.length);

    const epoch = ++state.transcriptRenderEpoch;
    const fragment = document.createDocumentFragment();
    for (const index of indexes) {
      const cue = item.data[index];
      const row = document.createElement("div");
      row.className = "bsb-transcript-row";
      if (index === state.transcriptActiveCueIndex) row.classList.add("active");
      row.dataset.cueIndex = String(index);

      const time = document.createElement("button");
      time.type = "button";
      time.className = "bsb-transcript-time";
      time.dataset.transcriptTime = String(cue.from_sec ?? parseSeconds(cue.from));
      time.textContent = formatTranscriptTime(cue.from_sec ?? parseSeconds(cue.from));
      time.title = `跳转到 ${formatTranscriptTime(cue.from_sec ?? parseSeconds(cue.from), true)}`;

      const text = document.createElement("p");
      text.className = "bsb-transcript-text";
      appendTranscriptHighlightedText(text, cue.content || "", query);
      row.append(time, text);
      fragment.appendChild(row);
    }
    if (epoch !== state.transcriptRenderEpoch) return;
    list.replaceChildren(fragment);
    updateTranscriptActiveCue(currentVideoTime(), true);
  }

  function transcriptCueIndexAt(cues, time) {
    let low = 0;
    let high = cues.length - 1;
    let candidate = -1;
    while (low <= high) {
      const mid = (low + high) >> 1;
      const from = Number(cues[mid].from_sec ?? parseSeconds(cues[mid].from));
      if (time < from) high = mid - 1;
      else { candidate = mid; low = mid + 1; }
    }
    if (candidate < 0) return -1;
    const cue = cues[candidate];
    const to = Number(cue.to_sec ?? parseSeconds(cue.to));
    return time <= to + 0.2 ? candidate : -1;
  }

  function currentVideoTime() {
    return Number(document.querySelector("video")?.currentTime || 0);
  }

  function updateTranscriptActiveCue(time, force = false) {
    const item = currentTranscriptItem();
    if (!item?.data?.length) return;
    const index = transcriptCueIndexAt(item.data, Number(time) || 0);
    const root = document.getElementById(PANEL_ID);
    if (index < 0) {
      root?.querySelector(".bsb-transcript-row.active")?.classList.remove("active");
      state.transcriptActiveCueIndex = -1;
      return;
    }
    if (!force && index === state.transcriptActiveCueIndex) return;
    root?.querySelector(".bsb-transcript-row.active")?.classList.remove("active");
    state.transcriptActiveCueIndex = index;
    const row = root?.querySelector(`.bsb-transcript-row[data-cue-index="${index}"]`);
    row?.classList.add("active");
    if (!row || !state.transcriptAutoFollow || state.ui?.view !== "subs") return;
    if (!force && Date.now() < state.transcriptUserScrollUntil) return;
    const behavior = matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth";
    row.scrollIntoView({ block: "center", behavior });
  }

  function bindTranscriptVideoEvents() {
    state.transcriptVideoAbort?.abort();
    const video = document.querySelector("video");
    if (!video) return;
    const controller = new AbortController();
    state.transcriptVideoAbort = controller;
    const update = () => updateTranscriptActiveCue(video.currentTime);
    video.addEventListener("timeupdate", update, { passive: true, signal: controller.signal });
    video.addEventListener("seeking", update, { passive: true, signal: controller.signal });
    video.addEventListener("loadedmetadata", update, { passive: true, signal: controller.signal });
    update();
  }

  function seekTranscriptTime(seconds) {
    const item = currentTranscriptItem();
    if (!item || !Number.isFinite(seconds)) return;
    seekToVideoTimestamp(seconds, item.bvid, item.page || 1);
    updateTranscriptActiveCue(seconds, true);
  }

  async function switchTranscriptTrack(index) {
    const item = currentTranscriptItem();
    if (!item?.tracks?.length || !item.tracks[index]) return;
    state.transcriptSwitchAbort?.abort();
    const controller = new AbortController();
    state.transcriptSwitchAbort = controller;
    setStatus(`正在切换到 ${item.tracks[index].lan_doc || item.tracks[index].lan || "字幕"}…`);
    const base = {
      bvid: item.bvid, aid: item.aid, cid: item.cid, title: item.title,
      author: item.author, pages: item.pages || [], page: item.page || 1,
    };
    const result = await fetchTrackBodyFast(base, item.tracks, index, controller.signal);
    if (controller.signal.aborted) return;
    Object.assign(item, result, { selected: item.selected !== false });
    lruSet(state.fastSubtitleCache, `${item.bvid}:${item.cid}`, item);
    persistentCacheWrite(`subtitle:${item.bvid}:${item.cid}`, item).catch(() => {});
    state.transcriptQuery = "";
    const input = ensurePanel().querySelector('[data-role="transcript-search"]');
    if (input) input.value = "";
    renderList();
    setStatus(`已切换 ${item.lan_doc || item.lan} · ${item.cue_count} 条`, "ok");
  }

  async function refreshCurrentTranscript() {
    const item = currentTranscriptItem();
    const ctx = detectContext(location.href);
    const bvid = item?.bvid || ctx.bvid;
    const page = item?.page || ctx.page || 1;
    if (!bvid) return setStatus("当前页面没有可刷新的视频", "err");
    state.autoCaptureKey = "";
    state.autoAnalyzeKey = "";
    state.autoAnalyzePendingKey = "";
    clearTimeout(state.autoAnalyzeTimer);
    state.fastSubtitleCache.delete(`${bvid}:${item?.cid || ""}`);
    await autoCaptureCurrentVideo("manual-refresh", { forceNetwork: true, requestedBvid: bvid, requestedPage: page });
  }

  function escapeHtml(s) {
    return String(s || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }
  function escapeAttr(s) {
    return escapeHtml(s).replace(/'/g, "&#39;");
  }

  function selectedItems() {
    return state.items.filter((it) => it.selected);
  }

  async function onAction(act) {
    if (act === "cancel") {
      state.cancel = true;
      setStatus("正在停止…");
      return;
    }
    if (act === "ai-stop") {
      state.aiAbort = true;
      try {
        if (state.aiAbortController) state.aiAbortController.abort();
      } catch (_) {
        /* */
      }
      try {
        if (state.aiXhr && typeof state.aiXhr.abort === "function") {
          state.aiXhr.abort();
        }
      } catch (_) {
        /* */
      }
      setStatus("正在停止 AI…");
      return;
    }
    if (act === "ai-toggle") {
      toggleAiPanel();
      return;
    }
    if (act === "ai-save") {
      saveAiConfigFromForm();
      setStatus("AI 配置已保存到本机", "ok");
      if (state.autoAnalyzeEnabled) {
        const item = currentTranscriptItem();
        if (item?.subStatus === "ok" && item.data?.length) {
          state.autoAnalyzeKey = "";
          scheduleAutoAnalyze(item, routeVideoKey(item.bvid, item.page || 1), "config-saved", 80);
        }
      }
      return;
    }
    if (act === "ai-reset") {
      // Restore defaults but never re-seed a secret key from the binary
      const prev = loadAiConfig();
      state.ai = {
        ...AI_DEFAULTS,
        // keep user's stored key if any; AI_DEFAULTS.apiKey is always ""
        apiKey: prev.apiKey || "",
      };
      saveAiConfig(state.ai);
      fillAiConfigForm(ensurePanel());
      setStatus("已恢复 AI 默认配置（API Key 保留本机已存值）", "ok");
      return;
    }
    if (act === "ai-send") {
      await doAiAnalyze();
      return;
    }
    if (act === "ai-stick") {
      if (state.aiStickBottom && !state.aiUserReading) {
        // 关跟随 → 进入阅读锁
        detachAiFollow("toggle");
        setStatus("已暂停跟随 · 可自由滚动 · 点「↓ 最新」回到底部");
      } else {
        resumeAiFollow();
        setStatus("跟随最新输出");
      }
      return;
    }
    if (act === "ai-jump") {
      resumeAiFollow();
      setStatus("已跳到最新");
      return;
    }
    if (act === "ai-copy") {
      const text = state.aiRaw || "";
      if (!text.trim()) {
        setStatus("没有可复制的内容", "err");
        return;
      }
      clipboardWrite(text);
      setStatus("已复制 AI 输出", "ok");
      return;
    }
    if (act === "ai-export") {
      if (!state.aiRaw.trim()) return setStatus("没有可导出的笔记", "err");
      const bvid = state.aiSourceBvids[0] || "bilibili";
      downloadText(`${safeFilename(bvid + "_AI笔记")}.md`, state.aiRaw);
      setStatus("已导出 Markdown", "ok");
      return;
    }
    if (act === "ai-font-dec" || act === "ai-font-inc") {
      const delta = act === "ai-font-inc" ? 1 : -1;
      const current = Number(state.ui?.noteFont || 17);
      const next = Math.max(NOTE_FONT_MIN, Math.min(NOTE_FONT_MAX, current + delta));
      if (state.ui) state.ui.noteFont = next;
      ensurePanel().style.setProperty("--bsb-note-font", `${next}px`);
      saveUiGeom();
      setStatus(`正文字号 ${next}px`);
      return;
    }
    if (act === "ai-top") {
      scrollAiToTop();
      setStatus("已回到顶部");
      return;
    }
    if (act === "transcript-refresh") {
      await refreshCurrentTranscript();
      return;
    }
    if (state.busy && act !== "cancel") return;

    if (act === "clear") {
      state.items = [];
      state.meta = {};
      state.transcriptItemKey = "";
      state.transcriptQuery = "";
      state.transcriptActiveCueIndex = -1;
      renderList();
      setStatus("已清空");
      return;
    }
    if (act === "sel-all") {
      state.items.forEach((it) => (it.selected = true));
      renderList();
      return;
    }
    if (act === "sel-none") {
      state.items.forEach((it) => (it.selected = false));
      renderList();
      return;
    }
    if (act === "scan") {
      await doScan();
      return;
    }
    if (act === "copy-bvid") {
      const list = selectedItems();
      if (!list.length) {
        setStatus("请先勾选视频", "err");
        return;
      }
      const text = list.map((it) => it.bvid).filter(Boolean).join("\n");
      clipboardWrite(text);
      setStatus(`已复制 ${list.length} 个 BV`, "ok");
      return;
    }
    if (act === "dl-srt" || act === "dl-txt" || act === "copy" || act === "dl-ok-only") {
      await doBatch(act);
    }
  }

  function clipboardWrite(text) {
    if (typeof GM_setClipboard === "function") GM_setClipboard(text, "text");
    else if (navigator.clipboard) navigator.clipboard.writeText(text);
  }

  // ─── AI config / stream / render ────────────────────────────────────────
  function storageGet(key, fallback) {
    try {
      if (typeof GM_getValue === "function") {
        const v = GM_getValue(key, null);
        if (v != null && v !== "") return v;
      }
    } catch (_) { /* ignore */ }
    try {
      const legacy = localStorage.getItem(key);
      if (legacy != null) return legacy;
    } catch (_) { /* ignore */ }
    return fallback;
  }

  function storageSet(key, value) {
    let storedByGm = false;
    try {
      if (typeof GM_setValue === "function") {
        GM_setValue(key, value);
        storedByGm = true;
      }
    } catch (_) { /* ignore */ }
    // 密钥优先只进入 userscript 隔离存储；仅无 GM API 时才退回页面 localStorage。
    if (!storedByGm) {
      try { localStorage.setItem(key, value); } catch (_) { /* ignore */ }
    } else {
      try { localStorage.removeItem(key); } catch (_) { /* migrate legacy */ }
    }
  }

  function loadAiConfig() {
    try {
      const raw = storageGet(AI_STORE_KEY, null);
      if (!raw) return { ...AI_DEFAULTS };
      const o = typeof raw === "string" ? JSON.parse(raw) : raw;
      return {
        baseUrl: String(o.baseUrl || AI_DEFAULTS.baseUrl).replace(/\/+$/, ""),
        apiKey: String(o.apiKey != null ? o.apiKey : AI_DEFAULTS.apiKey),
        model: String(o.model || AI_DEFAULTS.model),
        temperature: Number.isFinite(Number(o.temperature))
          ? Number(o.temperature)
          : AI_DEFAULTS.temperature,
        maxTokens: Number(o.maxTokens) || AI_DEFAULTS.maxTokens,
        stream: !(o.stream === false || o.stream === "false" || o.stream === 0),
        systemPrompt: String(o.systemPrompt || AI_DEFAULTS.systemPrompt),
        userPromptTemplate: String(
          o.userPromptTemplate || AI_DEFAULTS.userPromptTemplate,
        ),
      };
    } catch (_) {
      return { ...AI_DEFAULTS };
    }
  }

  function saveAiConfig(cfg) {
    try {
      storageSet(AI_STORE_KEY, JSON.stringify(cfg));
    } catch (_) {
      /* ignore */
    }
    state.ai = cfg;
  }

  function fillAiConfigForm(root) {
    if (!root) return;
    state.ai = loadAiConfig();
    const cfg = state.ai;
    const set = (k, v) => {
      const el = root.querySelector(`[data-ai="${k}"]`);
      if (el) el.value = v;
    };
    set("baseUrl", cfg.baseUrl);
    set("apiKey", cfg.apiKey);
    set("model", cfg.model);
    set("temperature", cfg.temperature);
    set("maxTokens", cfg.maxTokens);
    set("systemPrompt", cfg.systemPrompt);
    set("userPromptTemplate", cfg.userPromptTemplate);
    const streamEl = root.querySelector('[data-ai="stream"]');
    if (streamEl) streamEl.checked = !!cfg.stream;
  }

  function saveAiConfigFromForm() {
    const root = ensurePanel();
    const get = (k) => root.querySelector(`[data-ai="${k}"]`)?.value ?? "";
    const streamEl = root.querySelector('[data-ai="stream"]');
    const cfg = {
      baseUrl: String(get("baseUrl") || AI_DEFAULTS.baseUrl).replace(/\/+$/, ""),
      apiKey: String(get("apiKey") || ""),
      model: String(get("model") || AI_DEFAULTS.model),
      temperature: Number(get("temperature")) || 0,
      maxTokens: Number(get("maxTokens")) || AI_DEFAULTS.maxTokens,
      stream: !!(streamEl && streamEl.checked),
      systemPrompt: String(get("systemPrompt") || ""),
      userPromptTemplate: String(get("userPromptTemplate") || "{{subtitle}}"),
    };
    saveAiConfig(cfg);
    return cfg;
  }

  function toggleAiPanel(forceShow) {
    const root = ensurePanel();
    // v0.7：AI 是独立全高工作区，不再挤在底部小条
    if (forceShow === false) {
      setWorkspace("subs");
      return;
    }
    setWorkspace("ai");
    if (state.ui && state.ui.h < 640) {
      state.ui.h = Math.min(860, Math.max(640, state.ui.h));
      state.ui.w = Math.max(state.ui.w, 480);
      clampUiToViewport(state.ui);
      applyPanelGeometry();
      saveUiGeom();
    }
    refreshAiChips();
    fillAiConfigForm(root);
  }

  function setAiBusy(busy) {
    state.aiBusy = busy;
    const root = ensurePanel();
    root.classList.toggle("ai-busy", !!busy);
    root.querySelectorAll('[data-act="ai-stop"]').forEach((b) => {
      b.style.display = busy ? "" : "none";
    });
    root.querySelectorAll('[data-act="ai-send"]').forEach((b) => {
      b.disabled = busy;
    });
    const stream = root.querySelector('[data-role="ai-stream"]');
    if (stream) stream.classList.toggle("streaming", busy);
  }

  function updateJumpLatestBtn() {
    const root = document.getElementById(PANEL_ID);
    if (!root) return;
    const jump = root.querySelector('[data-act="ai-jump"]');
    const stickBtn = root.querySelector('[data-act="ai-stick"]');
    // 粘底按钮：仅在真正跟随中亮
    if (stickBtn) {
      stickBtn.classList.toggle(
        "on",
        !!state.aiStickBottom && !state.aiUserReading,
      );
    }
    if (!jump) return;
    // 用户阅读锁 或 未跟随时显示「↓ 最新」
    const show =
      (!state.aiStickBottom || state.aiUserReading) &&
      !!(state.aiRaw || state.aiBusy);
    jump.classList.toggle("show", show);
  }

  function applyAiScrollResolved(r) {
    state.aiStickBottom = r.stick;
    state.aiUserReading = r.userReading;
    updateJumpLatestBtn();
  }

  /** 用户主动离开底部：进入阅读锁，后续 paint 绝对不改 scrollTop */
  function detachAiFollow(_reason) {
    applyAiScrollResolved(
      resolveAiScrollState(
        {
          stick: state.aiStickBottom,
          userReading: state.aiUserReading,
          progScroll: state.aiProgScroll,
        },
        { type: "detach" },
      ),
    );
  }

  /** 仅按钮/开始分析：恢复跟随并滚到底 */
  function resumeAiFollow() {
    applyAiScrollResolved(
      resolveAiScrollState(
        {
          stick: state.aiStickBottom,
          userReading: state.aiUserReading,
          progScroll: false,
        },
        { type: "resume" },
      ),
    );
    scrollAiToBottom(true);
  }

  /**
   * v0.8.4 自由滚动硬模型（修「永远滚不动」）：
   *
   * 旧 bug：scroll 事件在距底 <80px 时自动 stick=true → 流式 paint 下一帧拽回底部。
   * 用户刚上滑几像素就被重新粘底，体感永远「在原地」。
   *
   * 新规则：
   * 1) 跟随只由：开始分析 / 「粘底」开 / 「↓ 最新」打开
   * 2) 任意向上 wheel / 上滑 touch / PageUp → 立即 detach，加阅读锁
   * 3) 程序化 scrollTop 带 aiProgScroll 标记，scroll 监听忽略
   * 4) 阅读锁期间 paint **完全不碰** scrollTop
   * 5) 用户自己滚到真正贴底（gap<=12）才解除阅读锁并恢复跟随
   */
  function bindAiScrollBehavior(root) {
    const box = root.querySelector('[data-role="ai-md"]');
    if (!box || box.dataset.bsbScrollBound === "1") return;
    box.dataset.bsbScrollBound = "1";

    const onLeaveBottom = () => {
      if (state.aiUserReading && !state.aiStickBottom) {
        updateJumpLatestBtn();
        return;
      }
      detachAiFollow("gesture");
    };

    // 捕获：向上意图优先于任何 paint
    box.addEventListener(
      "wheel",
      (e) => {
        if (e.deltaY < 0) onLeaveBottom();
      },
      { passive: true, capture: true },
    );
    // 触控板/手指：touchmove 向上
    box.addEventListener(
      "touchstart",
      (e) => {
        const t = e.touches && e.touches[0];
        box._bsbTouchY = t ? t.clientY : null;
      },
      { passive: true, capture: true },
    );
    box.addEventListener(
      "touchmove",
      (e) => {
        const t = e.touches && e.touches[0];
        if (!t || box._bsbTouchY == null) return;
        if (t.clientY - box._bsbTouchY > 8) onLeaveBottom();
        box._bsbTouchY = t.clientY;
      },
      { passive: true, capture: true },
    );
    box.addEventListener(
      "pointerdown",
      () => {
        box._bsbPtrY = null;
      },
      { passive: true },
    );
    box.addEventListener(
      "pointermove",
      (e) => {
        if (e.buttons === 0 && e.pointerType === "mouse") return;
        if (box._bsbPtrY != null && e.clientY - box._bsbPtrY > 8) {
          onLeaveBottom();
        }
        box._bsbPtrY = e.clientY;
      },
      { passive: true },
    );
    box.addEventListener(
      "keydown",
      (e) => {
        if (
          e.key === "ArrowUp" ||
          e.key === "PageUp" ||
          e.key === "Home"
        ) {
          onLeaveBottom();
        }
      },
      true,
    );

    box.addEventListener(
      "scroll",
      () => {
        const gap = box.scrollHeight - box.scrollTop - box.clientHeight;
        const r = resolveAiScrollState(
          {
            stick: state.aiStickBottom,
            userReading: state.aiUserReading,
            progScroll: state.aiProgScroll,
          },
          { type: "scroll", gap },
        );
        applyAiScrollResolved(r);
      },
      { passive: true },
    );
  }

  function scrollAiToBottom(force) {
    if (!force && (!state.aiStickBottom || state.aiUserReading)) {
      updateJumpLatestBtn();
      return;
    }
    const box = document.querySelector(`#${PANEL_ID} [data-role="ai-md"]`);
    if (!box) return;
    state.aiProgScroll = true;
    box.scrollTop = box.scrollHeight;
    requestAnimationFrame(() => {
      box.scrollTop = box.scrollHeight;
      // 再等一帧清标记，吞掉浏览器延迟的 scroll 事件
      requestAnimationFrame(() => {
        state.aiProgScroll = false;
        updateJumpLatestBtn();
      });
    });
  }

  function scrollAiToTop() {
    const box = document.querySelector(`#${PANEL_ID} [data-role="ai-md"]`);
    if (box) {
      state.aiProgScroll = true;
      box.scrollTop = 0;
      requestAnimationFrame(() => {
        state.aiProgScroll = false;
      });
    }
    detachAiFollow("top");
  }

  /** 流式绘制：只更新 text；非跟随模式零碰 scrollTop */
  function paintAiStreamText(full) {
    state.aiPendingText = String(full || "");
    if (state.aiPaintRaf || state.aiPaintTimer) return;

    const run = () => {
      state.aiPaintTimer = 0;
      state.aiPaintRaf = requestAnimationFrame(() => {
        state.aiPaintRaf = 0;
        const root = ensurePanel();
        const box = root.querySelector('[data-role="ai-md"]');
        const content = root.querySelector('[data-role="ai-content"]') || box;
        if (!box || !content) return;
        const text = state.aiPendingText || "…";
        let pre = content.querySelector(".bsb-ai-stream-body");
        let caret = content.querySelector(".bsb-ai-caret");
        if (!pre) {
          content.replaceChildren();
          pre = document.createElement("pre");
          pre.className = "bsb-ai-stream-body";
          state.aiStreamTextNode = document.createTextNode("");
          pre.appendChild(state.aiStreamTextNode);
          content.appendChild(pre);
          state.aiRenderedText = "";
        }
        if (!state.aiStreamTextNode || state.aiStreamTextNode.parentNode !== pre) {
          state.aiStreamTextNode = pre.firstChild || pre.appendChild(document.createTextNode(""));
          state.aiRenderedText = state.aiStreamTextNode.data || "";
        }
        if (state.aiBusy && !caret) {
          caret = document.createElement("span");
          caret.className = "bsb-ai-caret";
          caret.setAttribute("aria-hidden", "true");
          content.appendChild(caret);
        } else if (!state.aiBusy && caret) caret.remove();

        const follow = resolveAiScrollState(
          { stick: state.aiStickBottom, userReading: state.aiUserReading, progScroll: false },
          { type: "paint" },
        ).allowPaintScroll;
        const freezeTop = box.scrollTop;

        // 追加尾部而不是每个 token 重写整段文本，避免累计 O(n²) DOM 写入。
        if (text.startsWith(state.aiRenderedText)) {
          state.aiStreamTextNode.appendData(text.slice(state.aiRenderedText.length));
        } else {
          state.aiStreamTextNode.data = text;
        }
        state.aiRenderedText = text;

        if (follow) {
          state.aiProgScroll = true;
          box.scrollTop = box.scrollHeight;
          requestAnimationFrame(() => { state.aiProgScroll = false; });
        } else if (box.scrollTop !== freezeTop && freezeTop > 0) {
          state.aiProgScroll = true;
          box.scrollTop = freezeTop;
          requestAnimationFrame(() => { state.aiProgScroll = false; });
        }
        updateJumpLatestBtn();
      });
    };
    state.aiPaintTimer = window.setTimeout(run, STREAM_PAINT_INTERVAL_MS);
  }

  function buildSubtitlePayload(items) {
    return items
      .map((it) => {
        const head = `=== ${it.bvid}${it.page > 1 ? " P" + it.page : ""} ${it.title || ""} ===`;
        return `${head}\n${cuesToAiText(it.data || [], it.bvid, it.page || 1)}`;
      })
      .join("\n\n");
  }

  function noteModeLabel(mode) {
    const labels = {
      deep: "深度笔记",
      concise: "精炼摘要",
      study: "学习指南",
      action: "行动清单",
      mermaid: "全 Mermaid 学习图谱",
    };
    return labels[mode] || labels.deep;
  }

  function noteModeHint(mode) {
    return mode === "mermaid"
      ? "多图知识结构 · 学习路径 · 易错点 · 自测闭环"
      : "时间戳证据 · 安全渲染 · 按需图表";
  }

  function updateNoteModeUi(root, mode) {
    const hint = root?.querySelector('[data-role="note-mode-hint"]');
    if (hint) hint.textContent = noteModeHint(mode);
  }

  function noteModeInstruction(mode) {
    const map = {
      concise: "模式：精炼摘要。控制篇幅，保留最重要结论、证据时间戳和少量行动项。",
      study: "模式：学习指南。按先修知识、核心概念、例子、易错点、复习问题组织。",
      action: "模式：行动清单。突出决策、步骤、工具、风险、检查项和下一步。",
      deep: "模式：深度笔记。完整保留论证结构、关键细节、边界条件和可核查时间戳。",
      mermaid: [
        "模式：全 Mermaid 学习图谱。",
        "本模式优先级高于后续模板中的普通摘要、详细笔记、术语、方法和行动清单格式；请把这些内容转化为 Mermaid 图，而不是继续输出普通列表或长段落。",
        "请输出 3—6 个相互独立的 ```mermaid``` 代码块，以多图方式覆盖：全局知识地图、关键概念关系或因果链、方法流程或论证顺序、学习路径与自测闭环；有明确对比、风险或行动内容时再增加对应图。",
        "每个 Mermaid 块前只写一个简短二级标题；除标题和最多一句读图提示外，所有实质信息都必须位于图内，不要在图后重复解释。",
        "所有图只使用 flowchart TD 或 flowchart LR；节点 ID 使用 ASCII 字母和数字；节点文字使用带双引号的标签；每图通常不超过 18 个节点，并确保 Mermaid 10.9.1 可直接解析。",
        "重要节点尽量包含可核查时间戳 [BV号 P号 mm:ss]。学习图必须体现先修知识、理解顺序、易错点、复习问题和应用检查，而不只是把句子机械排列成流程图。",
      ].join("\n"),
    };
    return map[mode] || map.deep;
  }

  function loadScriptOnce(src, globalCheck) {
    return new Promise((resolve, reject) => {
      if (globalCheck && globalCheck()) return resolve();
      const existed = document.querySelector(`script[data-bsb-src="${src}"]`);
      if (existed) {
        if (globalCheck && globalCheck()) return resolve();
        existed.addEventListener("load", () => resolve(), { once: true });
        existed.addEventListener("error", () => reject(new Error("load " + src)), { once: true });
        return;
      }
      const s = document.createElement("script");
      s.src = src; s.async = true; s.dataset.bsbSrc = src;
      s.onload = () => resolve();
      s.onerror = () => reject(new Error("failed to load " + src));
      (document.head || document.documentElement).appendChild(s);
    });
  }

  function loadCssOnce(href) {
    if (document.querySelector(`link[data-bsb-href="${href}"]`)) return;
    const l = document.createElement("link");
    l.rel = "stylesheet"; l.href = href; l.dataset.bsbHref = href;
    (document.head || document.documentElement).appendChild(l);
  }

  async function ensureMarkdownCore() {
    if (state.renderLibs.core) return;
    await Promise.all([
      loadScriptOnce("https://cdn.jsdelivr.net/npm/marked@12.0.2/marked.min.js", () => typeof marked !== "undefined"),
      loadScriptOnce("https://cdn.jsdelivr.net/npm/dompurify@3.4.12/dist/purify.min.js", () => typeof DOMPurify !== "undefined"),
    ]);
    if (typeof marked !== "undefined") {
      marked.setOptions({ gfm: true, breaks: true, mangle: false, headerIds: false });
    }
    state.renderLibs.core = true;
  }

  async function ensureHighlight() {
    if (state.renderLibs.highlight) return;
    loadCssOnce("https://cdn.jsdelivr.net/gh/highlightjs/cdn-release@11.9.0/build/styles/atom-one-dark.min.css");
    await loadScriptOnce("https://cdn.jsdelivr.net/gh/highlightjs/cdn-release@11.9.0/build/highlight.min.js", () => typeof hljs !== "undefined");
    state.renderLibs.highlight = true;
  }

  async function ensureKatex() {
    if (state.renderLibs.katex) return;
    loadCssOnce("https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/katex.min.css");
    await loadScriptOnce("https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/katex.min.js", () => typeof katex !== "undefined");
    state.renderLibs.katex = true;
  }

  async function ensureMermaid() {
    if (state.renderLibs.mermaid) return;
    await loadScriptOnce("https://cdn.jsdelivr.net/npm/mermaid@10.9.1/dist/mermaid.min.js", () => typeof mermaid !== "undefined");
    if (typeof mermaid !== "undefined") {
      mermaid.initialize({
        startOnLoad: false,
        theme: "base",
        securityLevel: "strict",
        fontFamily: 'Inter, "PingFang SC", "Microsoft YaHei", system-ui, sans-serif',
        suppressErrorRendering: true,
        deterministicIds: false,
        themeVariables: {
          darkMode: true,
          background: "#181825",
          primaryColor: "#313244",
          primaryTextColor: "#f5e0dc",
          primaryBorderColor: "#89b4fa",
          secondaryColor: "#45475a",
          secondaryTextColor: "#cdd6f4",
          secondaryBorderColor: "#a6e3a1",
          tertiaryColor: "#1e1e2e",
          tertiaryTextColor: "#cdd6f4",
          tertiaryBorderColor: "#cba6f7",
          lineColor: "#bac2de",
          textColor: "#cdd6f4",
          mainBkg: "#313244",
          nodeBorder: "#89b4fa",
          clusterBkg: "#1e1e2e",
          clusterBorder: "#585b70",
          edgeLabelBackground: "#181825",
          actorBkg: "#313244",
          actorBorder: "#89b4fa",
          actorTextColor: "#f5e0dc",
          signalColor: "#bac2de",
          signalTextColor: "#cdd6f4",
          labelBoxBkgColor: "#313244",
          labelBoxBorderColor: "#89b4fa",
          labelTextColor: "#f5e0dc",
          loopTextColor: "#cdd6f4",
          noteBkgColor: "#45475a",
          noteBorderColor: "#f9e2af",
          noteTextColor: "#f5e0dc",
          fontSize: "16px",
        },
        themeCSS: `
          .nodeLabel, .edgeLabel, .label, text { font-size: 16px !important; }
          .edgeLabel rect { fill: #181825 !important; opacity: .96 !important; }
          .flowchart-link { stroke-width: 2px !important; }
          .marker { fill: #bac2de !important; stroke: #bac2de !important; }
          .cluster rect { rx: 10px; ry: 10px; }
        `,
        flowchart: {
          htmlLabels: false,
          useMaxWidth: false,
          curve: "basis",
          nodeSpacing: 48,
          rankSpacing: 64,
          padding: 16,
        },
        sequence: {
          useMaxWidth: false,
          wrap: true,
          diagramMarginX: 32,
          diagramMarginY: 24,
          actorMargin: 72,
          width: 180,
          height: 72,
          boxMargin: 12,
          messageMargin: 42,
        },
      });
    }
    state.renderLibs.mermaid = true;
  }

  function hasMathSyntax(md) {
    return /```(?:math|latex|tex)|\$\$[\s\S]+?\$\$|\\\[|\\\(|\$[^\n$]+\$/.test(md);
  }

  function hasCodeSyntax(md) {
    return /```(?!mermaid|math|latex|tex)[^\n]*\n/i.test(md);
  }

  function hasMermaidSyntax(md) {
    return /```mermaid\s*\n/i.test(md);
  }

  async function yieldToMain() {
    if (globalThis.scheduler && typeof globalThis.scheduler.yield === "function") {
      await globalThis.scheduler.yield();
    } else {
      await new Promise((resolve) => setTimeout(resolve, 0));
    }
  }

  /** KaTeX → HTML；失败返回 null 走 fallback */
  function katexToHtml(tex, display) {
    if (typeof katex === "undefined" || !katex.renderToString) return null;
    try {
      const html = katex.renderToString(String(tex || ""), {
        displayMode: !!display, throwOnError: false, strict: "ignore", trust: false, output: "html",
      });
      return display ? `<div class="bsb-katex-display">${html}</div>` : `<span class="bsb-katex-inline">${html}</span>`;
    } catch (_) { return null; }
  }

  function simpleMarkdownFallback(md) {
    let html = escapeHtml(md);
    html = html.replace(/```(\w*)\n([\s\S]*?)```/g, (_, lang, code) =>
      `<pre><span class="bsb-code-lang">${escapeHtml(lang || "text")}</span><code>${escapeHtml(code)}</code></pre>`);
    html = html.replace(/^### (.+)$/gm, "<h3>$1</h3>");
    html = html.replace(/^## (.+)$/gm, "<h2>$1</h2>");
    html = html.replace(/^# (.+)$/gm, "<h1>$1</h1>");
    html = html.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
    html = html.replace(/`([^`]+)`/g, "<code>$1</code>");
    html = html.replace(/\n\n/g, "</p><p>");
    return `<p>${html}</p>`;
  }

  function sanitizeRenderedHtml(html) {
    if (typeof DOMPurify === "undefined") return simpleMarkdownFallback(stripHtml(html));
    return DOMPurify.sanitize(html, {
      USE_PROFILES: { html: true },
      SANITIZE_NAMED_PROPS: true,
      FORBID_TAGS: ["script", "style", "iframe", "object", "embed", "form"],
      ADD_ATTR: ["target", "rel", "aria-label", "data-bsb-m"],
    });
  }

  async function replaceHostInBatches(host, html, epoch) {
    const tpl = document.createElement("template");
    tpl.innerHTML = html;
    const nodes = Array.from(tpl.content.childNodes);
    host.replaceChildren();
    for (let i = 0; i < nodes.length; i += RENDER_BATCH_SIZE) {
      if (epoch !== state.renderEpoch) return false;
      const frag = document.createDocumentFragment();
      nodes.slice(i, i + RENDER_BATCH_SIZE).forEach((node) => frag.appendChild(node));
      host.appendChild(frag);
      if (i + RENDER_BATCH_SIZE < nodes.length) await yieldToMain();
    }
    return true;
  }

  function linkifyTimestamps(host) {
    const walker = document.createTreeWalker(host, NodeFilter.SHOW_TEXT);
    const nodes = [];
    while (walker.nextNode()) {
      const p = walker.currentNode.parentElement;
      if (!p || p.closest("pre,code,a,button,.katex,.mermaid,.bsb-toc")) continue;
      if (/\[(?:BV[\w]+\s+)?(?:P\d+\s+)?(?:\d{1,2}:)?\d{1,2}:\d{2}\]/i.test(walker.currentNode.data)) nodes.push(walker.currentNode);
    }
    const re = /\[((BV[\w]+)\s+)?(?:P(\d+)\s+)?((?:\d{1,2}:)?\d{1,2}:\d{2})\]/gi;
    for (const node of nodes) {
      const text = node.data; let last = 0; let m; const frag = document.createDocumentFragment();
      while ((m = re.exec(text))) {
        if (m.index > last) frag.appendChild(document.createTextNode(text.slice(last, m.index)));
        const parts = m[4].split(":").map(Number);
        const sec = parts.length === 3 ? parts[0] * 3600 + parts[1] * 60 + parts[2] : parts[0] * 60 + parts[1];
        const btn = document.createElement("button");
        btn.type = "button"; btn.className = "bsb-time-link";
        btn.dataset.seconds = String(sec); btn.dataset.bvid = m[2] || ""; btn.dataset.page = m[3] || "1";
        btn.textContent = m[0]; btn.title = "跳到视频对应时间";
        frag.appendChild(btn); last = m.index + m[0].length;
      }
      if (last < text.length) frag.appendChild(document.createTextNode(text.slice(last)));
      node.replaceWith(frag);
    }
  }

  function seekToVideoTimestamp(seconds, bvid, page) {
    if (!Number.isFinite(seconds)) return;
    const currentBvid = extractBvid(location.href);
    let currentPage = 1;
    try { currentPage = Math.max(1, Number(new URL(location.href).searchParams.get("p")) || 1); } catch (_) { /* ignore */ }
    const targetPage = Math.max(1, Number(page) || 1);
    const sameVideo = !bvid || !currentBvid || bvid.toLowerCase() === currentBvid.toLowerCase();
    const video = document.querySelector("video");
    if (video && sameVideo && currentPage === targetPage) {
      video.currentTime = Math.max(0, seconds);
      if (video.paused) video.play().catch(() => {});
      video.scrollIntoView({ behavior: "smooth", block: "center" });
      return;
    }
    if (bvid) {
      const u = new URL(`https://www.bilibili.com/video/${bvid}`);
      u.searchParams.set("p", String(targetPage));
      u.searchParams.set("t", String(Math.floor(seconds)));
      window.open(u.toString(), "_blank", "noopener");
    }
  }

  function buildToc(host) {
    const headings = Array.from(host.querySelectorAll("h1,h2,h3")).filter((h) => !h.closest(".bsb-toc"));
    if (headings.length < 2) return;
    const details = document.createElement("details"); details.className = "bsb-toc"; details.open = true;
    const summary = document.createElement("summary"); summary.textContent = `本页目录 · ${headings.length}`;
    const nav = document.createElement("nav");
    headings.forEach((h, i) => {
      h.id = `bsb-note-h-${state.renderEpoch}-${i}`;
      const b = document.createElement("button"); b.type = "button"; b.dataset.level = h.tagName.slice(1);
      b.textContent = h.textContent.trim() || `章节 ${i + 1}`;
      b.addEventListener("click", () => h.scrollIntoView({ behavior: "smooth", block: "start" }));
      nav.appendChild(b);
    });
    details.append(summary, nav); host.prepend(details);
  }

  async function enhanceCodeBlocks(host, epoch) {
    const blocks = Array.from(host.querySelectorAll("pre code"));
    if (!blocks.length) return;
    try { await ensureHighlight(); } catch (e) { console.warn("[bili-subbatch] highlight load", e); return; }
    for (let i = 0; i < blocks.length; i++) {
      if (epoch !== state.renderEpoch) return;
      const block = blocks[i]; const pre = block.parentElement;
      const m = (block.className || "").match(/language-([\w#+-]+)/i);
      if (m && pre && !pre.querySelector(".bsb-code-lang")) {
        const tag = document.createElement("span"); tag.className = "bsb-code-lang"; tag.textContent = m[1]; pre.insertBefore(tag, block);
      }
      try { if (typeof hljs !== "undefined") hljs.highlightElement(block); } catch (_) { /* unknown language */ }
      if (i % 5 === 4) await yieldToMain();
    }
  }

  function parseMermaidViewBox(svg) {
    const raw = String(svg?.getAttribute("viewBox") || "").trim();
    const values = raw.split(/[\s,]+/).map(Number);
    if (values.length === 4 && values.every(Number.isFinite) && values[2] > 0 && values[3] > 0) {
      return { x: values[0], y: values[1], width: values[2], height: values[3] };
    }
    const width = Number.parseFloat(svg?.getAttribute("width")) || 960;
    const height = Number.parseFloat(svg?.getAttribute("height")) || 540;
    return { x: 0, y: 0, width, height };
  }

  function getMermaidScale(card) {
    const scale = Number(card?.dataset?.scale);
    return Number.isFinite(scale) && scale > 0 ? scale : 1;
  }

  function updateMermaidScaleLabel(card) {
    const label = card?.querySelector(".bsb-mermaid-scale");
    if (label) label.textContent = `${Math.round(getMermaidScale(card) * 100)}%`;
  }

  function setMermaidScale(card, scale, mode = "manual") {
    if (!card) return;
    const stage = card.querySelector(".bsb-mermaid-stage");
    if (!stage) return;
    const baseWidth = Number(stage.dataset.baseWidth) || 760;
    const next = Math.max(0.35, Math.min(3, Number(scale) || 1));
    card.dataset.scale = String(next);
    card.dataset.fit = mode;
    stage.style.setProperty("--bsb-mermaid-width", `${Math.max(240, Math.round(baseWidth * next))}px`);
    updateMermaidScaleLabel(card);
  }

  function fitMermaidToViewport(card) {
    const viewport = card?.querySelector(".bsb-mermaid-viewport");
    const stage = card?.querySelector(".bsb-mermaid-stage");
    if (!viewport || !stage) return;
    const baseWidth = Number(stage.dataset.baseWidth) || 760;
    const available = Math.max(240, viewport.clientWidth - 36);
    setMermaidScale(card, Math.min(1.5, available / baseWidth), "fit");
    viewport.scrollLeft = 0;
    viewport.scrollTop = 0;
  }

  function closeMermaidFullscreen() {
    const root = document.getElementById(PANEL_ID);
    const modal = root?.querySelector(".bsb-mermaid-modal");
    if (!modal) return;
    const card = modal.querySelector(".bsb-mermaid-card");
    const placeholder = card?._bsbMermaidPlaceholder;
    if (card && placeholder?.parentNode) {
      card.classList.remove("is-fullscreen");
      placeholder.replaceWith(card);
      card._bsbMermaidPlaceholder = null;
      const full = card.querySelector('[data-mmd-act="fullscreen"]');
      if (full) { full.textContent = "全屏"; full.title = "全屏查看"; }
    }
    modal.remove();
  }

  function openMermaidFullscreen(card) {
    if (!card || card.closest(".bsb-mermaid-modal")) return;
    closeMermaidFullscreen();
    const root = document.getElementById(PANEL_ID);
    if (!root) return;
    const placeholder = document.createComment("bsb-mermaid-placeholder");
    card.before(placeholder);
    card._bsbMermaidPlaceholder = placeholder;
    const modal = document.createElement("div");
    modal.className = "bsb-mermaid-modal";
    modal.setAttribute("role", "dialog");
    modal.setAttribute("aria-modal", "true");
    modal.setAttribute("aria-label", "Mermaid 图表全屏查看");
    modal.addEventListener("click", (e) => { if (e.target === modal) closeMermaidFullscreen(); });
    card.classList.add("is-fullscreen");
    const full = card.querySelector('[data-mmd-act="fullscreen"]');
    if (full) { full.textContent = "退出"; full.title = "退出全屏"; }
    modal.appendChild(card);
    root.appendChild(modal);
    requestAnimationFrame(() => fitMermaidToViewport(card));
  }

  function extractMermaidCode(text) {
    let source = String(text || "").trim();
    const fenced = source.match(/```(?:mermaid)?[ \t]*\r?\n([\s\S]*?)```/i);
    if (fenced) source = fenced[1];
    source = source
      .replace(/^\s*```(?:mermaid)?\s*/i, "")
      .replace(/```\s*$/i, "")
      .replace(/^\s*mermaid\s*\r?\n/i, "")
      .trim();
    return source;
  }

  function replaceMermaidBlockAt(markdown, targetIdx, nextCode) {
    let current = -1;
    let replaced = false;
    const value = String(markdown || "").replace(
      /```mermaid\s*\r?\n([\s\S]*?)```/gi,
      (full) => {
        current += 1;
        if (current !== Number(targetIdx)) return full;
        replaced = true;
        return `\`\`\`mermaid\n${String(nextCode || "").trim()}\n\`\`\``;
      },
    );
    return { value, replaced };
  }

  function persistRepairedMermaid(idx, nextCode) {
    const result = replaceMermaidBlockAt(state.aiRaw, idx, nextCode);
    if (!result.replaced) return false;
    state.aiRaw = result.value;
    // 防止已结束的流式缓冲区以后用旧源码覆盖当前 DOM。
    state.aiRenderedText = state.aiRaw;
    state.aiPendingText = state.aiRaw;
    return true;
  }

  function requestMermaidCodeRepair(code, error, idx) {
    const cfg = loadAiConfig();
    if (!cfg.apiKey) throw new Error("AI 修复需要先在设置中填写 API Key");
    if (!cfg.baseUrl) throw new Error("AI 修复需要先在设置中填写 Base URL");

    const parseError = String(error?.message || error || "未知渲染错误").slice(0, 4000);
    const originalCode = String(code || "").slice(0, 30000);
    const messages = [
      {
        role: "system",
        content:
          "你是 Mermaid 10.9.1 语法修复器。只修复用户给出的单个 Mermaid 图代码。" +
          "保持节点、关系、顺序、文字含义和信息量不变；不得添加字幕或原图中没有的事实。" +
          "允许为兼容 Mermaid 10.9.1 改写引号、括号、节点 ID、换行、标签和箭头语法。" +
          "最终只输出一个 ```mermaid 代码块，不输出解释。",
      },
      {
        role: "user",
        content:
          `请修复第 ${Number(idx) + 1} 张 Mermaid 图。\n\n` +
          `【渲染错误】\n${parseError}\n\n` +
          `【原始 Mermaid】\n\`\`\`mermaid\n${originalCode}\n\`\`\``,
      },
    ];

    return new Promise((resolve, reject) => {
      let latest = "";
      requestChatCompletion({
        baseUrl: cfg.baseUrl,
        apiKey: cfg.apiKey,
        model: cfg.model,
        temperature: 0.1,
        maxTokens: Math.max(1200, Math.min(4096, Number(cfg.maxTokens) || 4096)),
        messages,
        stream: cfg.stream !== false,
        onStatus(msg) {
          setStatus(`Mermaid AI 修复 · ${msg}`);
        },
        onDelta(_delta, full, parts) {
          latest = String(parts?.content || full || latest);
        },
        onDone(full, parts) {
          const repaired = extractMermaidCode(parts?.content || full || latest);
          if (!repaired) {
            reject(new Error("AI 没有返回可用的 Mermaid 代码"));
            return;
          }
          resolve(repaired);
        },
        onError(err) {
          reject(err instanceof Error ? err : new Error(String(err || "AI 修复失败")));
        },
      });
    });
  }

  async function handleMermaidTool(button) {
    const action = button?.dataset?.mmdAct;
    const card = button?.closest(".bsb-mermaid-card");

    if (action === "retry") {
      const host = button.closest?.(".mermaid[data-bsb-m]") || card?._bsbMermaidHost;
      const job = host?._bsbMermaidJob || card?._bsbMermaidJob;
      if (!host || !job) {
        setStatus("重绘失败：没有找到该图对应的 Mermaid 源码", "err");
        return;
      }
      if (state.aiBusy) {
        setStatus("当前 AI 笔记仍在生成，请完成或停止后再修复 Mermaid", "err");
        return;
      }
      if (state.mermaidRepairing) {
        setStatus("已有 Mermaid 图正在重绘", "err");
        return;
      }

      if (card?.closest(".bsb-mermaid-modal")) closeMermaidFullscreen();
      state.mermaidRepairing = true;
      button.disabled = true;
      button.textContent = "重试中…";

      const originalCode = String(job.code || "");
      let activeCode = originalCode;
      try {
        // 第一步：原代码本地重试一次，解决偶发的库加载/并发问题，不消耗 AI。
        setStatus(`Mermaid 图 ${job.idx + 1} · 正在用原代码重试…`);
        const local = await renderMermaidNode(host, originalCode, job.idx, job.epoch, {
          force: true,
          maxAttempts: 1,
          showError: false,
          repaired: !!job.repaired,
          originalCode: job.originalCode || originalCode,
        });
        if (local.ok) {
          setStatus(`Mermaid 图 ${job.idx + 1} 已重新渲染；代码未改变`, "ok");
          return;
        }

        // 第二步：原代码稳定失败，说明大概率是语法/兼容性问题；只让 AI 修复此代码块。
        button.textContent = "AI 修复中…";
        setStatus(`Mermaid 图 ${job.idx + 1} 本地重试失败，正在修复代码…`);
        activeCode = await requestMermaidCodeRepair(originalCode, local.error, job.idx);
        if (activeCode.trim() === originalCode.trim()) {
          throw new Error("AI 返回的 Mermaid 代码与原代码相同，未完成修复");
        }

        // 第三步：只有修复后的代码真实通过 Mermaid 渲染，才写回完整笔记源码。
        button.textContent = "验证中…";
        const repairedResult = await renderMermaidNode(host, activeCode, job.idx, job.epoch, {
          force: true,
          maxAttempts: 2,
          showError: false,
          repaired: true,
          originalCode,
        });
        if (!repairedResult.ok) throw repairedResult.error || new Error("修复后的代码仍无法渲染");

        const persisted = persistRepairedMermaid(job.idx, activeCode);
        const currentJob = host._bsbMermaidJob;
        if (currentJob) {
          currentJob.code = activeCode;
          currentJob.repaired = true;
          currentJob.originalCode = originalCode;
          currentJob.persisted = persisted;
        }
        setStatus(
          `Mermaid 图 ${job.idx + 1} 修复成功：代码已替换并重新渲染${persisted ? "，已写回笔记源码" : ""}`,
          "ok",
        );
      } catch (err) {
        console.error("[bili-subbatch] mermaid repair", err);
        host.dataset.bsbState = "error";
        host._bsbMermaidJob = {
          code: originalCode,
          idx: job.idx,
          epoch: job.epoch,
          originalCode: job.originalCode || originalCode,
          repaired: false,
        };
        renderMermaidError(host, originalCode, err, job.idx);
        setStatus(`Mermaid 图 ${job.idx + 1} 重绘失败：${err?.message || err}`, "err");
      } finally {
        state.mermaidRepairing = false;
        if (button.isConnected) {
          button.disabled = false;
          button.textContent = "重绘";
        }
      }
      return;
    }

    if (!card) return;
    const viewport = card.querySelector(".bsb-mermaid-viewport");
    if (action === "fit") fitMermaidToViewport(card);
    else if (action === "actual") setMermaidScale(card, 1, "actual");
    else if (action === "zoom-in") setMermaidScale(card, getMermaidScale(card) + 0.15);
    else if (action === "zoom-out") setMermaidScale(card, getMermaidScale(card) - 0.15);
    else if (action === "fullscreen") {
      if (card.closest(".bsb-mermaid-modal")) closeMermaidFullscreen();
      else openMermaidFullscreen(card);
    }
    if (viewport && action === "actual") { viewport.scrollLeft = 0; viewport.scrollTop = 0; }
  }

  function buildMermaidCard(svg, idx, host) {
    svg.classList.add("bsb-mermaid-svg");
    svg.removeAttribute("width");
    svg.removeAttribute("height");
    svg.setAttribute("preserveAspectRatio", "xMinYMin meet");
    svg.setAttribute("role", "img");
    svg.setAttribute("aria-label", `架构流程图 ${idx + 1}`);
    const viewBox = parseMermaidViewBox(svg);
    if (!svg.hasAttribute("viewBox")) svg.setAttribute("viewBox", `${viewBox.x} ${viewBox.y} ${viewBox.width} ${viewBox.height}`);

    const baseWidth = Math.round(Math.max(760, Math.min(3600, viewBox.width)));
    const card = document.createElement("section");
    card.className = "bsb-mermaid-card";
    card.dataset.scale = "1";
    card.dataset.fit = "actual";

    const toolbar = document.createElement("div");
    toolbar.className = "bsb-mermaid-toolbar";
    const title = document.createElement("span");
    title.className = "bsb-mermaid-title";
    const repaired = !!host?._bsbMermaidJob?.repaired;
    title.textContent = `架构流程图 ${idx + 1}${repaired ? " · AI 已修复" : ""} · 可滚动查看`;
    const tools = document.createElement("span");
    tools.className = "bsb-mermaid-tools";
    const makeButton = (toolAction, text, titleText) => {
      const toolButton = document.createElement("button");
      toolButton.type = "button";
      toolButton.className = "bsb-mermaid-tool";
      toolButton.dataset.mmdAct = toolAction;
      toolButton.textContent = text;
      toolButton.title = titleText;
      toolButton.setAttribute("aria-label", titleText);
      return toolButton;
    };
    tools.append(
      makeButton("fit", "适宽", "适应可视区域宽度"),
      makeButton("actual", "100%", "恢复清晰原始尺寸"),
      makeButton("zoom-out", "−", "缩小图表"),
    );
    const scaleLabel = document.createElement("span");
    scaleLabel.className = "bsb-mermaid-scale";
    scaleLabel.textContent = "100%";
    tools.append(
      scaleLabel,
      makeButton("zoom-in", "+", "放大图表"),
      makeButton("retry", "重绘", "先重试渲染；若语法失败则只修复该 Mermaid 代码块"),
      makeButton("fullscreen", "全屏", "全屏查看"),
    );
    toolbar.append(title, tools);

    const viewport = document.createElement("div");
    viewport.className = "bsb-mermaid-viewport";
    viewport.tabIndex = 0;
    viewport.setAttribute("aria-label", "可滚动和缩放的 Mermaid 图表");
    const stage = document.createElement("div");
    stage.className = "bsb-mermaid-stage";
    stage.dataset.baseWidth = String(baseWidth);
    stage.style.setProperty("--bsb-mermaid-width", `${baseWidth}px`);
    stage.appendChild(svg);
    const hint = document.createElement("span");
    hint.className = "bsb-mermaid-hint";
    hint.textContent = "Ctrl + 滚轮缩放";
    viewport.append(stage, hint);
    card.append(toolbar, viewport);
    card._bsbMermaidHost = host || null;
    card._bsbMermaidJob = host?._bsbMermaidJob || null;
    return card;
  }

  function enqueueMermaidRender(task) {
    const run = state.mermaidQueue.catch(() => undefined).then(task);
    state.mermaidQueue = run.catch(() => undefined);
    return run;
  }

  function renderMermaidError(node, code, err, idx) {
    const message = String(err?.message || err || "未知错误");
    node.replaceChildren();

    const box = document.createElement("section");
    box.className = "bsb-mermaid-error";
    const head = document.createElement("div");
    head.className = "bsb-mermaid-error-head";
    const title = document.createElement("strong");
    title.textContent = `架构流程图 ${idx + 1} 渲染失败`;
    const retry = document.createElement("button");
    retry.type = "button";
    retry.className = "bsb-mermaid-tool";
    retry.dataset.mmdAct = "retry";
    retry.textContent = "重绘";
    retry.title = "先用原代码重试；仍失败则调用现有 AI 配置修复此 Mermaid 代码块";
    head.append(title, retry);

    const details = document.createElement("details");
    const summary = document.createElement("summary");
    summary.textContent = "查看 Mermaid 源码与错误";
    const pre = document.createElement("pre");
    const codeNode = document.createElement("code");
    codeNode.textContent = `${code}\n\n${message}`;
    pre.appendChild(codeNode);
    details.append(summary, pre);
    box.append(head, details);
    node.appendChild(box);
  }

  async function renderMermaidNode(
    node,
    code,
    idx,
    epoch,
    { force = false, maxAttempts, showError = true, repaired = false, originalCode = "" } = {},
  ) {
    if (!node || epoch !== state.renderEpoch) return { ok: false, aborted: true };
    if (!force && node.dataset.bsbState === "done") return { ok: true, skipped: true };
    if (node.dataset.bsbState === "rendering") return { ok: false, busy: true };

    const previousState = node.dataset.bsbState || "pending";
    const previousJob = node._bsbMermaidJob || null;
    const job = {
      code: String(code || ""),
      idx,
      epoch,
      repaired: !!repaired,
      originalCode: String(originalCode || previousJob?.originalCode || code || ""),
    };
    node._bsbMermaidJob = job;
    node.dataset.bsbState = "rendering";
    const attempts = Math.max(1, Number(maxAttempts) || (force ? 2 : 3));
    let lastError = null;

    for (let attempt = 0; attempt < attempts; attempt++) {
      try {
        const rendered = await enqueueMermaidRender(async () => {
          await ensureMermaid();
          if (attempt > 0 && typeof mermaid !== "undefined") {
            // 只重新应用配置；不能把语法错误误认为 CDN/运行时故障。
            state.renderLibs.mermaid = false;
            await ensureMermaid();
          }
          if (epoch !== state.renderEpoch) return null;
          const id = `bsb-mmd-${Date.now()}-${idx}-${++state.mermaidRenderSeq}`;
          return mermaid.render(id, job.code);
        });
        if (!rendered || epoch !== state.renderEpoch) return { ok: false, aborted: true };
        const { svg } = rendered;
        const safeSvg = typeof DOMPurify !== "undefined"
          ? DOMPurify.sanitize(svg, {
              USE_PROFILES: { svg: true, svgFilters: true },
              ADD_ATTR: ["class", "style", "viewBox", "preserveAspectRatio", "role", "aria-label"],
            })
          : svg;
        const tpl = document.createElement("template");
        tpl.innerHTML = safeSvg;
        const svgNode = tpl.content.querySelector("svg");
        if (!svgNode) throw new Error("Mermaid 未返回有效 SVG");

        node.replaceChildren(buildMermaidCard(svgNode, idx, node));
        node.dataset.bsbState = "done";
        return { ok: true, code: job.code, repaired: job.repaired };
      } catch (err) {
        lastError = err;
        if (epoch !== state.renderEpoch) return { ok: false, aborted: true, error: err };
        if (attempt + 1 < attempts) await sleep(180 * (attempt + 1));
      }
    }

    if (showError) {
      node.dataset.bsbState = "error";
      renderMermaidError(node, job.code, lastError, idx);
    } else {
      node.dataset.bsbState = previousState;
      node._bsbMermaidJob = previousJob || job;
    }
    return { ok: false, error: lastError, code: job.code };
  }

  function scheduleMermaid(host, blocks, epoch, scrollRoot) {
    const nodes = Array.from(host.querySelectorAll(".mermaid[data-bsb-m]"));
    if (!nodes.length) return;
    if (state.mermaidObserver) state.mermaidObserver.disconnect();
    if ("IntersectionObserver" in window) {
      state.mermaidObserver = new IntersectionObserver((entries, observer) => {
        for (const entry of entries) if (entry.isIntersecting) {
          observer.unobserve(entry.target);
          const idx = Number(entry.target.dataset.bsbM);
          renderMermaidNode(entry.target, blocks[idx] || "", idx, epoch);
        }
      }, { root: scrollRoot || null, rootMargin: "320px 0px", threshold: 0.01 });
      nodes.forEach((node) => { node.dataset.bsbState = "pending"; state.mermaidObserver.observe(node); });
    } else {
      nodes.forEach((node, idx) => renderMermaidNode(node, blocks[idx] || "", idx, epoch));
    }
  }

  async function renderAiMarkdown(md, { streaming } = {}) {
    const root = ensurePanel();
    const box = root.querySelector('[data-role="ai-md"]');
    const host = root.querySelector('[data-role="ai-content"]') || box;
    if (!host) return;
    if (streaming) return paintAiStreamText(md);
    closeMermaidFullscreen();

    // 防止最后一个流式定时绘制在增强渲染完成后反向覆盖 DOM。
    if (state.aiPaintTimer) { clearTimeout(state.aiPaintTimer); state.aiPaintTimer = 0; }
    if (state.aiPaintRaf) { cancelAnimationFrame(state.aiPaintRaf); state.aiPaintRaf = 0; }

    const epoch = ++state.renderEpoch;
    const source = String(md || "");
    const needsMath = hasMathSyntax(source);
    const needsCode = hasCodeSyntax(source);
    const needsMermaid = hasMermaidSyntax(source);
    try {
      await ensureMarkdownCore();
      if (needsMath) await ensureKatex();
    } catch (e) {
      host.innerHTML = simpleMarkdownFallback(source) + `<p style="color:var(--ctp-peach)">增强渲染库加载失败，已使用安全简易渲染。</p>`;
      if (box) box.scrollTop = 0;
      return;
    }
    if (epoch !== state.renderEpoch) return;

    const { md: mdMath, maths } = prepareMarkdownMath(source);
    const mermaidBlocks = [];
    const md2 = mdMath.replace(/```mermaid\s*\n([\s\S]*?)```/gi, (_, code) => {
      const i = mermaidBlocks.length; mermaidBlocks.push(code.trim());
      return `\n\n<div class="mermaid" data-bsb-m="${i}">${escapeHtml(code.trim())}</div>\n\n`;
    });
    let html;
    try { html = marked.parse(md2); } catch (_) { html = simpleMarkdownFallback(md2); }
    if (maths.length) html = replaceMathPlaceholders(html, maths, katexToHtml);
    html = sanitizeRenderedHtml(html);
    if (!(await replaceHostInBatches(host, html, epoch))) return;

    host.querySelectorAll("a[href]").forEach((a) => { a.target = "_blank"; a.rel = "noopener noreferrer"; });
    linkifyTimestamps(host);
    buildToc(host);
    if (needsMermaid) scheduleMermaid(host, mermaidBlocks, epoch, box);
    if (needsCode) await enhanceCodeBlocks(host, epoch);

    if (box && !box.querySelector('[data-role="ai-anchor"]')) {
      const a = document.createElement("div"); a.className = "bsb-ai-anchor"; a.dataset.role = "ai-anchor"; box.appendChild(a);
    }
    if (box) {
      state.aiProgScroll = true; box.scrollTop = 0;
      requestAnimationFrame(() => { state.aiProgScroll = false; });
    }
    state.aiStickBottom = false; state.aiUserReading = true; updateJumpLatestBtn();
  }

  /**
   * OpenAI-compatible chat.completions.
   *
   * 路径优先级（peer + opencli 实测）：
   * 1) 页面原生 fetch + ReadableStream
   * 2) GM_xmlhttpRequest 回退（无 timeout；优先 responseType stream + reader）
   */
  function requestChatCompletion(opts) {
    const {
      baseUrl,
      apiKey,
      model,
      temperature,
      maxTokens,
      messages,
      stream,
      onDelta,
      onDone,
      onError,
      onStatus,
    } = opts;
    const url = baseUrl.replace(/\/+$/, "") + "/chat/completions";
    const useStream = stream !== false;
    const body = {
      model,
      messages,
      temperature,
      max_tokens: maxTokens,
      stream: useStream,
    };

    // cancel previous
    try {
      if (state.aiAbortController) state.aiAbortController.abort();
    } catch (_) {
      /* */
    }
    try {
      if (state.aiXhr && typeof state.aiXhr.abort === "function") {
        state.aiXhr.abort();
      }
    } catch (_) {
      /* */
    }
    state.aiAbortController = null;
    state.aiXhr = null;

    // Prefer page fetch (validated via opencli browser eval)
    requestChatViaPageFetch({
      url,
      apiKey,
      body,
      useStream,
      onDelta,
      onDone,
      onError: (err) => {
        // CORS / network → fall back to GM
        const msg = String(err && err.message ? err.message : err);
        if (
          /cors|failed to fetch|networkerror|load failed|blocked/i.test(msg) ||
          err?.name === "TypeError"
        ) {
          onStatus && onStatus(`页内 fetch 失败(${msg.slice(0, 60)})，改用 GM…`);
          requestChatViaGm({
            url,
            apiKey,
            body,
            useStream,
            onDelta,
            onDone,
            onError,
            onStatus,
          });
        } else {
          onError && onError(err);
        }
      },
      onStatus,
    });
  }

  function requestChatViaPageFetch({
    url,
    apiKey,
    body,
    useStream,
    onDelta,
    onDone,
    onError,
    onStatus,
  }) {
    let assembledContent = "";
    let assembledReasoning = "";
    let settled = false;
    let lineBuf = "";
    let lastStatusAt = 0;
    const ac = new AbortController();
    state.aiAbortController = ac;

    const finish = (err) => {
      if (settled) return;
      settled = true;
      if (state.aiAbortController === ac) state.aiAbortController = null;
      if (err) onError && onError(err);
      else {
        onDone &&
          onDone(formatAiDisplay(assembledContent, assembledReasoning), {
            content: assembledContent,
            reasoning: assembledReasoning,
          });
      }
    };

    const emit = () => {
      onDelta &&
        onDelta("", formatAiDisplay(assembledContent, assembledReasoning), {
          content: assembledContent,
          reasoning: assembledReasoning,
        });
    };

    const applyPiece = (c, r) => {
      let ch = false;
      if (c) {
        assembledContent += c;
        ch = true;
      }
      if (r) {
        assembledReasoning += r;
        ch = true;
      }
      if (ch) emit();
    };

    const handleSseLine = (line) => {
      const parsed = parseSseDataLine(line);
      if (parsed.kind === "delta") applyPiece(parsed.content, parsed.reasoning);
      if (parsed.kind === "error") throw new Error(parsed.message);
    };

    onStatus && onStatus("页内 fetch 流式请求（peer/opencli 路径）…");

    (async () => {
      try {
        if (state.aiAbort) throw new Error("用户停止");
        const res = await fetch(url, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Authorization: "Bearer " + apiKey,
            Accept: useStream
              ? "text/event-stream, application/json"
              : "application/json",
          },
          body: JSON.stringify(body),
          signal: ac.signal,
        });
        if (!res.ok) {
          const t = await res.text();
          throw new Error(`HTTP ${res.status}: ${t.slice(0, 300)}`);
        }

        if (!useStream || !res.body || !res.body.getReader) {
          const text = await res.text();
          const j = JSON.parse(text);
          if (j.error) throw new Error(j.error.message || JSON.stringify(j.error));
          const { content, reasoning } = extractFromChoice(j.choices?.[0]);
          assembledContent = content || "";
          assembledReasoning = reasoning || "";
          emit();
          if (!assembledContent && !assembledReasoning) {
            throw new Error("响应无正文: " + text.slice(0, 200));
          }
          finish(null);
          return;
        }

        onStatus && onStatus("已连接，流式读取中…");
        const reader = res.body.getReader();
        const dec = new TextDecoder();
        let totalBytes = 0;
        while (true) {
          if (state.aiAbort) {
            try {
              await reader.cancel();
            } catch (_) {
              /* */
            }
            throw new Error("用户停止");
          }
          const { done, value } = await reader.read();
          if (done) break;
          totalBytes += value ? value.length : 0;
          lineBuf += dec.decode(value, { stream: true });
          const lines = lineBuf.split(/\r?\n/);
          lineBuf = lines.pop() || "";
          for (const line of lines) handleSseLine(line);
          const now = Date.now();
          if (now - lastStatusAt > 400) {
            lastStatusAt = now;
            onStatus &&
              onStatus(
                `页内流式… ${totalBytes}B · 正文 ${assembledContent.length} · 思考 ${assembledReasoning.length}`,
              );
          }
        }
        if (lineBuf.trim()) handleSseLine(lineBuf);
        if (!assembledContent && !assembledReasoning) {
          throw new Error("流式结束但 content/reasoning 皆空");
        }
        finish(null);
      } catch (e) {
        if (state.aiAbort || (e && e.name === "AbortError")) {
          if (assembledContent || assembledReasoning) {
            onStatus && onStatus("已停止，保留已接收内容");
            finish(null);
          } else {
            finish(new Error("用户停止"));
          }
          return;
        }
        // 有部分内容：不走 GM 回退以免重复计费，直接成功
        if (assembledContent || assembledReasoning) {
          onStatus && onStatus("连接中断，保留已接收内容");
          finish(null);
          return;
        }
        finish(e instanceof Error ? e : new Error(String(e)));
      }
    })();
  }

  function requestChatViaGm({
    url,
    apiKey,
    body,
    useStream,
    onDelta,
    onDone,
    onError,
    onStatus,
  }) {
    let assembledContent = "";
    let assembledReasoning = "";
    let settled = false;
    let lineBuf = "";
    let lastSeenLen = 0;
    let lastStatusAt = 0;
    let xhrHandle = null;
    let usedStreamReader = false;
    /** Only one text POST may start (prevents parallel billing) */
    let textPathStarted = false;
    /** When true, stream onabort is expected and must not finish/error */
    let switchingToText = false;

    const finish = (err) => {
      if (settled) return;
      settled = true;
      switchingToText = false;
      if (state.aiXhr === xhrHandle) state.aiXhr = null;
      if (err) onError && onError(err);
      else {
        onDone &&
          onDone(formatAiDisplay(assembledContent, assembledReasoning), {
            content: assembledContent,
            reasoning: assembledReasoning,
          });
      }
    };

    const softFinishOrError = (label) => {
      if (assembledContent || assembledReasoning) {
        onStatus && onStatus(`${label}，已收到内容，按成功结束`);
        finish(null);
        return;
      }
      finish(new Error(label));
    };

    const emit = () => {
      onDelta &&
        onDelta("", formatAiDisplay(assembledContent, assembledReasoning), {
          content: assembledContent,
          reasoning: assembledReasoning,
        });
    };

    const applyPiece = (c, r) => {
      let ch = false;
      if (c) {
        assembledContent += c;
        ch = true;
      }
      if (r) {
        assembledReasoning += r;
        ch = true;
      }
      if (ch) emit();
    };

    const handleSseLine = (line) => {
      const parsed = parseSseDataLine(line);
      if (parsed.kind === "delta") applyPiece(parsed.content, parsed.reasoning);
      if (parsed.kind === "error") softFinishOrError(parsed.message);
    };

    const ingestSseText = (fullText) => {
      if (fullText.length < lastSeenLen) lastSeenLen = 0;
      const neu = fullText.slice(lastSeenLen);
      lastSeenLen = fullText.length;
      if (!neu) return;
      lineBuf += neu;
      const lines = lineBuf.split(/\r?\n/);
      lineBuf = lines.pop() || "";
      for (const line of lines) handleSseLine(line);
      const now = Date.now();
      if (now - lastStatusAt > 400) {
        lastStatusAt = now;
        onStatus &&
          onStatus(
            `GM流式… ${fullText.length}B · 正文 ${assembledContent.length} · 思考 ${assembledReasoning.length}`,
          );
      }
    };

    const abortXhr = (handle) => {
      if (!handle) return;
      try {
        if (typeof handle.abort === "function") handle.abort();
      } catch (_) {
        /* */
      }
    };

    const commonHeaders = {
      "Content-Type": "application/json",
      Authorization: "Bearer " + apiKey,
      Accept: useStream
        ? "text/event-stream, application/json"
        : "application/json",
    };

    function startGmTextPath(reason) {
      if (settled || textPathStarted) return;
      textPathStarted = true;
      // Abort any in-flight stream XHR before opening a second POST
      if (xhrHandle) {
        switchingToText = true;
        const prev = xhrHandle;
        xhrHandle = null;
        abortXhr(prev);
        switchingToText = false;
      }
      onStatus &&
        onStatus(
          `GM text/onprogress（无 timeout）${reason ? " · " + reason : ""}…`,
        );
      xhrHandle = GM_xmlhttpRequest({
        method: "POST",
        url,
        headers: commonHeaders,
        data: JSON.stringify(body),
        responseType: "text",
        onloadstart() {
          onStatus && onStatus("GM text 已连接…");
        },
        onprogress(res) {
          if (state.aiAbort) {
            abortXhr(xhrHandle);
            softFinishOrError("用户停止");
            return;
          }
          if (useStream) ingestSseText(res.responseText || "");
        },
        onreadystatechange(res) {
          if (state.aiAbort) return;
          if (res.readyState === 3 && useStream && res.responseText) {
            ingestSseText(res.responseText);
          }
        },
        onload(res) {
          if (state.aiAbort) {
            softFinishOrError("用户停止");
            return;
          }
          const text = res.responseText || "";
          if (res.status < 200 || res.status >= 300) {
            softFinishOrError(`HTTP ${res.status}: ${text.slice(0, 200)}`);
            return;
          }
          try {
            if (useStream) {
              ingestSseText(text);
              if (lineBuf.trim()) handleSseLine(lineBuf);
            } else {
              const j = JSON.parse(text);
              if (j.error) {
                throw new Error(j.error.message || JSON.stringify(j.error));
              }
              const { content, reasoning } = extractFromChoice(j.choices?.[0]);
              assembledContent = content || "";
              assembledReasoning = reasoning || "";
              emit();
            }
          } catch (e) {
            softFinishOrError(e.message || String(e));
            return;
          }
          if (!assembledContent && !assembledReasoning) {
            softFinishOrError("GM 响应无正文");
            return;
          }
          finish(null);
        },
        onerror() {
          softFinishOrError("GM 网络错误");
        },
        ontimeout() {
          softFinishOrError("GM ontimeout 误触");
        },
        onabort() {
          if (state.aiAbort) softFinishOrError("用户停止");
          // ignore aborts while replacing handles
        },
      });
      state.aiXhr = xhrHandle;
    }

    // Peer practice (GreasyFork 459997): responseType stream + getReader
    if (useStream) {
      onStatus && onStatus("GM stream reader 回退…");
      xhrHandle = GM_xmlhttpRequest({
        method: "POST",
        url,
        headers: commonHeaders,
        data: JSON.stringify(body),
        responseType: "stream",
        onloadstart(streamRes) {
          if (settled || textPathStarted) return;
          try {
            const reader =
              streamRes.response &&
              streamRes.response.getReader &&
              streamRes.response.getReader();
            if (!reader) throw new Error("no stream reader");
            usedStreamReader = true;
            onStatus && onStatus("GM stream reader 已连接…");
            const dec = new TextDecoder();
            let buf = "";
            const pump = () => {
              if (settled || textPathStarted) return;
              if (state.aiAbort) {
                try {
                  reader.cancel();
                } catch (_) {
                  /* */
                }
                softFinishOrError("用户停止");
                return;
              }
              reader
                .read()
                .then(({ done, value }) => {
                  if (settled || textPathStarted) return;
                  if (done) {
                    if (buf.trim()) {
                      buf.split(/\r?\n/).forEach(handleSseLine);
                    }
                    if (!assembledContent && !assembledReasoning) {
                      softFinishOrError("GM stream 结束无正文");
                    } else finish(null);
                    return;
                  }
                  buf += dec.decode(value, { stream: true });
                  const lines = buf.split(/\r?\n/);
                  buf = lines.pop() || "";
                  for (const line of lines) handleSseLine(line);
                  onStatus &&
                    onStatus(
                      `GM stream… 正文 ${assembledContent.length} · 思考 ${assembledReasoning.length}`,
                    );
                  pump();
                })
                .catch((e) => {
                  if (settled || textPathStarted) return;
                  softFinishOrError(e.message || String(e));
                });
            };
            pump();
          } catch (_) {
            // stream unsupported → single text POST after aborting this handle
            if (!settled && !usedStreamReader && !textPathStarted) {
              onStatus && onStatus("GM stream 不可用，改 text…");
              startGmTextPath("stream unsupported");
            }
          }
        },
        onerror() {
          if (settled || textPathStarted) return;
          if (!usedStreamReader) {
            startGmTextPath("stream onerror");
          } else {
            softFinishOrError("GM stream 网络错误");
          }
        },
        onabort() {
          // Intentional abort when switching to text path — do not finish
          if (switchingToText || textPathStarted) return;
          if (settled) return;
          softFinishOrError(state.aiAbort ? "用户停止" : "GM stream 中止");
        },
      });
      state.aiXhr = xhrHandle;
      return xhrHandle;
    }

    startGmTextPath("non-stream");
    return xhrHandle;
  }

  async function ensureSubtitlesForAi(targets) {
    const delay = state.delayMs;
    for (let i = 0; i < targets.length; i++) {
      if (state.cancel || state.aiAbort) throw new Error("用户停止");
      const it = targets[i];
      if (it.subStatus === "ok" && it.data?.length) continue;
      setStatus(`AI 准备字幕 ${i + 1}/${targets.length} · ${it.bvid}…`);
      const r = await fetchSubtitle(it.bvid, it.page || 1);
      it.subStatus = r.status;
      it.cue_count = r.cue_count || 0;
      it.data = r.data || null;
      it.error = r.error || "";
      if (!it.title && r.title) it.title = r.title;
      if (!it.author && r.author) it.author = r.author;
      renderList();
      if (i < targets.length - 1) await sleep(delay);
    }
    return targets.filter((it) => it.subStatus === "ok" && it.data?.length);
  }

  async function doAiAnalyze() {
    if (state.aiBusy) return;
    const root = ensurePanel();
    toggleAiPanel(true);

    // 设置页表单优先；否则用已存配置
    let cfg;
    try {
      cfg = saveAiConfigFromForm();
    } catch (_) {
      cfg = loadAiConfig();
      state.ai = cfg;
    }
    if (!cfg.apiKey) {
      setStatus("请先在「设置」中填写 API Key", "err");
      setWorkspace("settings");
      return;
    }
    if (!cfg.baseUrl) {
      setStatus("请填写 Base URL", "err");
      setWorkspace("settings");
      return;
    }

    let targets = selectedItems();
    if (!targets.length) {
      // 无勾选：若列表空则扫当前页单视频
      if (!state.items.length) {
        try {
          await doScan();
        } catch (_) {
          /* */
        }
      }
      targets = selectedItems();
    }
    if (!targets.length) {
      setStatus("请先扫描并勾选要分析的视频", "err");
      return;
    }

    state.aiAbort = false;
    state.cancel = false;
    state.aiRaw = "";
    state.aiRenderedText = "";
    state.aiStreamTextNode = null;
    state.aiUserReading = false;
    state.aiStickBottom = true;
    state.aiProgScroll = false;
    updateJumpLatestBtn();
    setAiBusy(true);
    setBusy(true);
    setStatus("准备字幕并连接 AI…");

    const contentHost = root.querySelector('[data-role="ai-content"]');
    if (contentHost) {
      contentHost.innerHTML =
        `<div class="bsb-empty"><div class="bsb-empty-ico">◌</div><strong>正在生成…</strong>` +
        `<span>流式输出中 · 上滑可自由阅读（不会被拽回）· 点「↓ 最新」继续跟随</span></div>`;
    }

    try {
      const ready = await ensureSubtitlesForAi(targets);
      if (!ready.length) {
        setStatus("勾选项均无字幕，无法发送 AI", "err");
        return;
      }

      const built = buildSubtitlePayload(ready);
      const cut = truncateForAi(built, MAX_SUBTITLE_CHARS);
      const first = ready[0];
      state.aiSourceBvids = ready.map((x) => x.bvid).filter(Boolean);
      const selectedNoteMode = root.querySelector('[data-role="note-mode"]')?.value || state.ui?.noteMode || "deep";
      const noteMode = NOTE_MODE_OPTIONS.includes(selectedNoteMode) ? selectedNoteMode : "deep";
      const vars = {
        title:
          ready.map((x) => x.title).filter(Boolean).join(" / ") ||
          first.title ||
          "",
        bvid: ready.map((x) => x.bvid).join(", "),
        author: first.author || "",
        subtitle: cut.text,
        modeInstruction: noteModeInstruction(noteMode),
      };
      let userContent = applyPromptTemplate(cfg.userPromptTemplate, vars);
      // 兼容旧版本已保存的自定义模板：没有新占位符时仍让模式选择生效。
      if (!String(cfg.userPromptTemplate || "").includes("{{modeInstruction}}")) {
        userContent = `${noteModeInstruction(noteMode)}\n\n${userContent}`;
      }
      const messages = [];
      const effectiveSystemPrompt = [
        String(cfg.systemPrompt || "").trim(),
        noteMode === "mermaid" ? MERMAID_LEARNING_SYSTEM_PROMPT : "",
      ]
        .filter(Boolean)
        .join("\n\n");
      if (effectiveSystemPrompt) {
        messages.push({ role: "system", content: effectiveSystemPrompt });
      }
      messages.push({ role: "user", content: userContent });

      const useStream = cfg.stream !== false;
      setStatus(
        `AI 请求中 · ${noteModeLabel(noteMode)} · ${cfg.model} · ${useStream ? "SSE流式" : "非流式"} · ${ready.length} 条` +
          (cut.truncated ? " · 字幕已截断" : ""),
      );

      await new Promise((resolve, reject) => {
        requestChatCompletion({
          baseUrl: cfg.baseUrl,
          apiKey: cfg.apiKey,
          model: cfg.model,
          temperature: cfg.temperature,
          maxTokens: cfg.maxTokens,
          messages,
          stream: useStream,
          onStatus(msg) {
            setStatus(msg);
          },
          onDelta(_d, full) {
            state.aiRaw = full;
            paintAiStreamText(full);
          },
          onDone(full) {
            state.aiRaw = full || state.aiRaw;
            resolve(full);
          },
          onError(err) {
            reject(err);
          },
        });
      });

      if (state.aiAbort) {
        setStatus("AI 已停止", "err");
        await renderAiMarkdown(
          (state.aiRaw || "") + "\n\n*(已停止)*",
          { streaming: false },
        );
        return;
      }

      setStatus("AI 完成 · 正在安全渲染并按需增强…");
      await renderAiMarkdown(state.aiRaw, { streaming: false });
      setStatus(`AI 完成 · ${state.aiRaw.length} 字符 · ${cfg.model}`, "ok");
    } catch (e) {
      console.error("[bili-subbatch] ai", e);
      setStatus(`AI 失败: ${e.message || e}`, "err");
      if (state.aiRaw) {
        await renderAiMarkdown(
          state.aiRaw + `\n\n> 错误：${e.message || e}`,
          { streaming: false },
        );
      }
    } finally {
      setAiBusy(false);
      setBusy(false);
      state.aiAbort = false;
      if (state.aiPaintRaf) {
        cancelAnimationFrame(state.aiPaintRaf);
        state.aiPaintRaf = 0;
      }
      if (state.aiPaintTimer) {
        clearTimeout(state.aiPaintTimer);
        state.aiPaintTimer = 0;
      }
    }
  }

  function validateCtxForScan(ctx) {
    if (ctx.type === "video" || ctx.type === "selection") {
      if (!ctx.bvid) {
        throw new Error("缺少 BV 号。请打开视频页，或切到「单个视频/视频选集」后重试");
      }
    } else if (ctx.type === "user") {
      if (!ctx.mid) {
        throw new Error("缺少 mid。请打开个人主页，或模式选「个人主页」");
      }
    } else if (ctx.type === "collection") {
      if (!ctx.mid || !ctx.season_id) {
        throw new Error(
          "合集需要 mid + season_id。请打开合集页（space/lists 或 /list/{mid}?sid=），或从下拉切到「合集」",
        );
      }
    } else if (ctx.type === "favorite") {
      if (!ctx.media_id) {
        throw new Error("收藏夹需要 fid/media_id。请打开带 fid 的收藏夹页");
      }
    } else if (ctx.type === "search") {
      if (!ctx.keyword) {
        throw new Error("搜索需要 keyword。请打开搜索结果页");
      }
    } else if (ctx.type === "unknown") {
      throw new Error(
        "未能识别页面。可手动选择模式：单个视频 / 视频选集 / 个人主页 / 收藏夹 / 合集 / 搜索页",
      );
    }
  }

  async function doScan() {
    refreshContextUI();
    const ctx = resolveContext();
    state.ctx = ctx;
    state.cancel = false;
    setBusy(true);
    setStatus("扫描中…");
    try {
      const root = ensurePanel();
      state.maxPages = Math.max(
        1,
        Math.min(100, Number(root.querySelector('[data-role="max-pages"]').value) || DEFAULT_MAX_PAGES),
      );
      state.delayMs = Math.max(
        0,
        Math.min(5000, Number(root.querySelector('[data-role="delay"]').value) || DEFAULT_DELAY_MS),
      );

      validateCtxForScan(ctx);

      let items = [];
      let meta = {};

      // 单个视频 = 当前分P；视频选集 = 展开全部分P
      if (ctx.type === "video" || ctx.type === "selection") {
        const bvid = ctx.bvid || extractBvid(location.href);
        if (!bvid) throw new Error("未识别 BV 号");
        const expandParts = ctx.type === "selection";
        setStatus(
          expandParts
            ? `读取选集 ${bvid}（全部分P）…`
            : `读取单个视频 ${bvid}${ctx.page > 1 ? " P" + ctx.page : ""}…`,
        );
        const loaded = await loadVideoAsItems(bvid, expandParts);
        // 单个视频：只保留当前 p（loadVideoAsItems 非 expand 时已是 1 条；
        // 若 expand=false 但我们要当前 p，需按 page 取）
        if (!expandParts) {
          const page = ctx.page || 1;
          if (loaded.pages && loaded.pages.length && page > 1) {
            // 重新按指定分P构造一条
            const part = loaded.pages[page - 1];
            items = [
              {
                bvid,
                aid: loaded.items[0]?.aid,
                title: part
                  ? `${loaded.meta?.title || bvid} - P${page}【${part.part || ""}】`
                  : loaded.meta?.title || bvid,
                author: loaded.meta?.author || "",
                page,
                part: part?.part || "",
              },
            ];
          } else {
            items = loaded.items.map((it) => ({ ...it, page: page || 1 }));
          }
        } else {
          items = loaded.items;
        }
        meta = loaded.meta || {};
        if (meta.multip && ctx.type === "video") {
          meta.hint = "多分P视频：可切换模式「视频选集」拉全部分P";
        }
      } else if (
        ctx.type === "user" ||
        ctx.type === "favorite" ||
        ctx.type === "collection" ||
        ctx.type === "search"
      ) {
        const pageSize =
          ctx.type === "favorite" ? 20 : ctx.type === "search" ? 42 : 30;
        const res = await loadAllListItems(ctx, {
          maxPages: state.maxPages,
          pageSize,
          delayMs: Math.min(state.delayMs, 400),
          onProgress: (t) => setStatus(t),
        });
        items = res.items;
        meta = res.meta || {};
        if (res.truncated) meta.truncated = true;
      } else {
        const fromDom = harvestBvidsFromDom();
        if (fromDom.length) {
          items = fromDom.map((b) => ({
            bvid: b,
            title: b,
            author: "",
            page: 1,
          }));
          meta = { fromDom: true };
        } else {
          throw new Error(
            "当前页未识别。请手动选择模式，或确认 URL 含 BV/mid/fid/keyword",
          );
        }
      }

      state.items = items.map((it) => ({
        ...it,
        selected: true,
        subStatus: "wait",
        cue_count: 0,
        data: null,
        error: "",
      }));
      state.meta = meta;
      renderList();
      refreshContextUI();
      const trunc = meta.truncated ? "（已达页数上限，可调大「最多页」）" : "";
      const modeTag =
        ctx.source === "manual"
          ? `手动·${TYPE_LABEL[ctx.type]}`
          : `自动·${TYPE_LABEL[ctx.type]}`;
      let msg = `[${modeTag}] 已加载 ${state.items.length} 条${trunc}`;
      if (meta.hint) msg += ` · ${meta.hint}`;
      setStatus(msg, "ok");
      if (meta.name || meta.title || meta.keyword) {
        const label = meta.name || meta.title || meta.keyword;
        const ctxEl = ensurePanel().querySelector('[data-role="ctx"]');
        if (ctxEl && !ctxEl.textContent.includes(String(label))) {
          ctxEl.textContent += ` · ${label}`;
        }
      }
    } catch (e) {
      console.error("[bili-subbatch] scan", e);
      setStatus(`扫描失败: ${e.message || e}`, "err");
    } finally {
      setBusy(false);
    }
  }

  function harvestBvidsFromDom() {
    const seen = new Set();
    const out = [];
    document.querySelectorAll('a[href*="/video/BV"]').forEach((a) => {
      const b = extractBvid(a.getAttribute("href") || a.href || "");
      if (b && !seen.has(b)) {
        seen.add(b);
        out.push(b);
      }
    });
    return out;
  }

  async function doBatch(act) {
    let targets =
      act === "dl-ok-only"
        ? state.items.filter((it) => it.selected && it.subStatus === "ok" && it.data)
        : selectedItems();
    if (!targets.length) {
      setStatus(act === "dl-ok-only" ? "没有已成功的勾选项" : "请先勾选视频", "err");
      return;
    }

    state.cancel = false;
    setBusy(true);
    const needFetch = act !== "dl-ok-only" && act !== "copy";
    // copy / download both may need fetch if no data yet
    const needData = act !== "copy-bvid";

    let ok = 0,
      empty = 0,
      err = 0;
    const delay = state.delayMs;

    try {
      for (let i = 0; i < targets.length; i++) {
        if (state.cancel) {
          setStatus(`已停止 · 完成 ${i}/${targets.length}`, "err");
          break;
        }
        const it = targets[i];
        setStatus(`字幕 ${i + 1}/${targets.length} · ${it.bvid}${it.page > 1 ? " P" + it.page : ""}…`);

        if (needData && (!it.data || it.subStatus !== "ok")) {
          try {
            const r = await fetchSubtitle(it.bvid, it.page || 1);
            it.subStatus = r.status;
            it.cue_count = r.cue_count || 0;
            it.data = r.data || null;
            it.error = r.error || "";
            it.lan = r.lan || "";
            if (!it.title && r.title) it.title = r.title;
            if (!it.author && r.author) it.author = r.author;
            if (r.status === "ok") ok++;
            else if (r.status === "empty") empty++;
            else err++;
          } catch (e) {
            it.subStatus = "error";
            it.error = e.message || String(e);
            err++;
          }
          renderList();
          if (i < targets.length - 1 && !state.cancel) await sleep(delay);
        } else if (it.subStatus === "ok") {
          ok++;
        }
      }

      // export after fetch (or reuse)
      const ready = targets.filter((it) => it.subStatus === "ok" && it.data?.length);
      if (state.cancel && act !== "dl-ok-only") {
        // still allow export of what we have if user stopped mid-way? skip for clarity
      }

      if (act === "copy") {
        if (!ready.length) {
          setStatus(`无可用字幕 · ok=${ok} empty=${empty} err=${err}`, "err");
          return;
        }
        const text = ready
          .map((it) => {
            const head =
              ready.length > 1
                ? `=== ${it.bvid}${it.page > 1 ? " P" + it.page : ""} ${it.title || ""} ===\n`
                : "";
            return head + cuesToTxt(it.data);
          })
          .join("\n\n");
        clipboardWrite(text);
        setStatus(`已复制 ${ready.length} 条字幕全文`, "ok");
        return;
      }

      if (act === "dl-srt" || act === "dl-txt" || act === "dl-ok-only") {
        const ext = act === "dl-txt" ? "txt" : "srt";
        const convert = ext === "txt" ? cuesToTxt : cuesToSrt;
        const pool =
          act === "dl-ok-only"
            ? state.items.filter((it) => it.selected && it.subStatus === "ok" && it.data)
            : ready;
        if (!pool.length) {
          setStatus(`无可用字幕 · ok=${ok} empty=${empty} err=${err}`, "err");
          return;
        }
        for (let i = 0; i < pool.length; i++) {
          const it = pool[i];
          const base = safeFilename(
            `${it.bvid}${it.page > 1 ? "_P" + String(it.page).padStart(2, "0") : ""}_${it.title || "sub"}`,
          );
          downloadText(`${base}.${ext}`, convert(it.data));
          if (pool.length > 1) await sleep(200);
        }
        setStatus(
          `已下载 ${pool.length} 个 ${ext.toUpperCase()} · 抓取 ok=${ok} empty=${empty} err=${err}`,
          "ok",
        );
      }
    } catch (e) {
      console.error("[bili-subbatch] batch", e);
      setStatus(`失败: ${e.message || e}`, "err");
    } finally {
      setBusy(false);
      state.cancel = false;
      renderList();
    }
  }

  /**
   * 字幕抓取成功后的自动分析调度：
   * - 默认开启，行为等同点击“开始分析”；
   * - 同一路由只自动触发一次；
   * - stale revalidate / 静默刷新不重复分析；
   * - AI 配置尚未完成时等待用户保存配置，保存后自动续跑。
   */
  function scheduleAutoAnalyze(item, captureKey, reason = "capture", delay = AUTO_ANALYZE_DELAY_MS) {
    if (!state.autoAnalyzeEnabled || !item || item.subStatus !== "ok" || !item.data?.length) return;
    const key = captureKey || routeVideoKey(item.bvid, item.page || 1);
    if (!key || state.autoAnalyzeKey === key || state.autoAnalyzePendingKey === key) return;

    const cfg = loadAiConfig();
    if (!cfg.apiKey || !cfg.baseUrl) {
      setStatus(`已抓取 ${item.cue_count || item.data.length} 条字幕 · 保存 AI 配置后将自动分析`, "ok");
      return;
    }

    clearTimeout(state.autoAnalyzeTimer);
    state.autoAnalyzePendingKey = key;
    state.autoAnalyzeTimer = window.setTimeout(async () => {
      state.autoAnalyzePendingKey = "";
      if (!state.autoAnalyzeEnabled || state.autoAnalyzeKey === key) return;

      const current = detectContext(location.href);
      if (current.type !== "video" || routeVideoKey(current.bvid, current.page || 1) !== key) return;
      const target = state.items.find((x) => routeVideoKey(x.bvid, x.page || 1) === key);
      if (!target || target.subStatus !== "ok" || !target.data?.length) return;

      // 旧视频的 AI 请求正在收尾时稍后重试；不会并发启动第二个分析。
      if (state.aiBusy) {
        scheduleAutoAnalyze(target, key, reason, 500);
        return;
      }

      state.autoAnalyzeKey = key;
      setStatus(`字幕已就绪 · 正在自动开始分析（${reason}）…`);
      try {
        await doAiAnalyze();
      } catch (error) {
        // doAiAnalyze 内部通常已处理错误；这里只防止未捕获异常污染页面。
        console.warn("[bili-subbatch] auto analyze", error);
      }
    }, Math.max(0, Number(delay) || 0));
  }

  function abortAiForAutoNavigation() {
    if (!state.autoAnalyzeEnabled || !state.aiBusy) return;
    state.aiAbort = true;
    try {
      state.aiAbortController?.abort();
    } catch (_) {
      /* noop */
    }
    try {
      if (state.aiXhr && typeof state.aiXhr.abort === "function") state.aiXhr.abort();
    } catch (_) {
      /* noop */
    }
  }

  function scheduleAutoCapture(reason, delay = AUTO_CAPTURE_DELAY_MS) {
    clearTimeout(state.autoCaptureTimer);
    if (!state.autoCaptureEnabled || document.hidden) return;
    state.autoCaptureTimer = window.setTimeout(() => {
      autoCaptureCurrentVideo(reason).catch((error) => {
        if (error?.name !== "AbortError") {
          console.warn("[bili-subbatch] auto capture", error);
        }
      });
    }, Math.max(0, Number(delay) || 0));
  }

  async function autoCaptureCurrentVideo(reason = "route", options = {}) {
    if (!state.autoCaptureEnabled && !options.forceNetwork) return;
    const ctx = detectContext(location.href);
    const bvid = options.requestedBvid || ctx.bvid;
    if (!bvid || (!options.requestedBvid && ctx.type !== "video")) return;
    const page = Math.max(1, Number(options.requestedPage || ctx.page || currentPageNumber()) || 1);
    const captureKey = routeVideoKey(bvid, page);
    const existing = state.items.find(
      (item) => routeVideoKey(item.bvid, item.page || 1) === captureKey,
    );
    if (!options.forceNetwork && state.autoCaptureKey === captureKey && ["ok", "empty"].includes(existing?.subStatus)) {
      if (existing?.subStatus === "ok") selectTranscriptItem(existing);
      return;
    }

    const epoch = ++state.autoCaptureEpoch;
    state.autoCaptureAbortController?.abort();
    const controller = new AbortController();
    state.autoCaptureAbortController = controller;
    state.autoCaptureKey = captureKey;

    const placeholder = options.silent && existing
      ? { ...existing }
      : {
          bvid,
          title: document.title.replace(/_哔哩哔哩_bilibili$/i, "") || bvid,
          author: "",
          page,
          selected: true,
          subStatus: "wait",
          cue_count: 0,
          data: null,
          error: "",
          autoCaptured: true,
        };
    if (!options.silent) {
      state.items = [placeholder];
      state.meta = { autoCaptured: true, reason };
      renderList();
      refreshContextUI();
      setStatus(`自动读取 ${bvid}${page > 1 ? " P" + page : ""} 字幕…`);
    }

    let result = null;
    let fastError = null;
    try {
      result = await fetchCurrentSubtitleFast(bvid, page, controller.signal, { forceNetwork: !!options.forceNetwork });
    } catch (error) {
      if (error?.name === "AbortError") throw error;
      fastError = error;
    }

    // 快速接口无轨道、受限或发生变化时，继续使用原脚本的完整 WBI/DM/AI 回退链。
    if (!result || result.status !== "ok") {
      if (controller.signal.aborted) throw new DOMException("操作已取消", "AbortError");
      try {
        result = await fetchSubtitle(bvid, page);
        if (result && result.status === "ok") result.source = "robust_fallback";
      } catch (error) {
        if (!result) result = { bvid, page, status: "error", error: error.message || String(error) };
      }
    }

    if (epoch !== state.autoCaptureEpoch || controller.signal.aborted) return;
    const nowCtx = detectContext(location.href);
    if (nowCtx.type !== "video" || routeVideoKey(nowCtx.bvid, nowCtx.page || 1) !== captureKey) return;

    const item = {
      ...placeholder,
      bvid: result?.bvid || bvid,
      aid: result?.aid || null,
      cid: result?.cid || null,
      title: result?.title || placeholder.title,
      author: result?.author || "",
      page: result?.page || page,
      pages: result?.pages || [],
      selected: true,
      subStatus: result?.status || "error",
      cue_count: result?.cue_count || 0,
      data: result?.data || null,
      error: result?.error || fastError?.message || "",
      lan: result?.lan || "",
      lan_doc: result?.lan_doc || "",
      tracks: result?.tracks || placeholder.tracks || [],
      activeTrackIndex: Number.isInteger(result?.activeTrackIndex) ? result.activeTrackIndex : (placeholder.activeTrackIndex ?? -1),
      cachePath: result?.cachePath || "",
      cacheLevel: result?.cacheLevel || "",
      cacheStale: !!result?.cacheStale,
      source: result?.source || (fastError ? "fast_failed" : "fast"),
      autoCaptured: true,
    };
    state.items = [item];
    state.meta = {
      autoCaptured: true,
      title: item.title,
      author: item.author,
      source: item.source,
    };
    if (item.subStatus === "ok") {
      state.transcriptItemKey = routeVideoKey(item.bvid, item.page || 1);
    }
    renderList();
    bindTranscriptVideoEvents();

    if (item.subStatus === "ok") {
      if (state.autoEnablePlayerSubtitle) {
        window.setTimeout(() => enablePlayerSubtitle(item).catch(() => {}), 280);
      }
      setStatus(`已自动抓取 ${item.cue_count} 条字幕 · ${item.cachePath || item.cacheLevel || item.source}`, "ok");
      // 静默的 stale-while-revalidate 只更新字幕缓存，不重复消耗一次 AI 请求。
      if (!options.silent && reason !== "stale-revalidate") {
        scheduleAutoAnalyze(item, captureKey, reason);
      }
      if (item.cacheStale && !options.forceNetwork) {
        window.setTimeout(() => {
          const current = detectContext(location.href);
          if (routeVideoKey(current.bvid, current.page || 1) === captureKey) {
            autoCaptureCurrentVideo("stale-revalidate", { forceNetwork: true, silent: true, requestedBvid: item.bvid, requestedPage: item.page }).catch(() => {});
          }
        }, 80);
      }
    } else if (item.subStatus === "empty") {
      setStatus("当前视频没有可读取字幕", "err");
    } else {
      setStatus(`自动抓取失败: ${item.error || "未知错误"}`, "err");
    }
  }

  // ─── SPA watch ──────────────────────────────────────────────────────────
  let lastHref = location.href;
  function onMaybeNavigate() {
    if (location.href === lastHref) return;
    lastHref = location.href;
    state.autoCaptureAbortController?.abort();
    clearTimeout(state.autoAnalyzeTimer);
    state.autoAnalyzePendingKey = "";
    state.autoAnalyzeKey = "";
    abortAiForAutoNavigation();
    state.transcriptVideoAbort?.abort();
    state.transcriptSwitchAbort?.abort();
    state.transcriptActiveCueIndex = -1;
    const nextCtx = detectContext(location.href);
    state.transcriptItemKey = nextCtx.type === "video" && nextCtx.bvid
      ? routeVideoKey(nextCtx.bvid, nextCtx.page || 1)
      : "";
    refreshContextUI();
    renderTranscriptPanel();
    scheduleAutoCapture("route-change");
  }

  function boot() {
    initCacheChannel();
    ensurePanel();
    refreshContextUI();
    bindTranscriptVideoEvents();
    const _push = pageWindow.history.pushState;
    const _replace = pageWindow.history.replaceState;
    pageWindow.history.pushState = function () {
      const result = _push.apply(this, arguments);
      setTimeout(onMaybeNavigate, 0);
      return result;
    };
    pageWindow.history.replaceState = function () {
      const result = _replace.apply(this, arguments);
      setTimeout(onMaybeNavigate, 0);
      return result;
    };
    pageWindow.addEventListener("popstate", onMaybeNavigate);
    pageWindow.addEventListener("hashchange", onMaybeNavigate);
    window.addEventListener("pageshow", () => {
      onMaybeNavigate();
      scheduleAutoCapture("pageshow", 120);
    });
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible") {
        onMaybeNavigate();
        scheduleAutoCapture("visible", 120);
      }
    });
    // History hook 是主路径；低频 URL 比较只为处理少数 B 站站内切换。
    setInterval(() => {
      if (document.visibilityState === "visible") onMaybeNavigate();
    }, 2000);

    // 初次打开页面也默认抓取，不要求先打开面板或点击“扫描”。
    scheduleAutoCapture("initial", 180);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
