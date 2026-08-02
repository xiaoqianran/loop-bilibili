// ==UserScript==
// @name         Bili SubBatch (loop-bilibili)
// @namespace    https://github.com/loop-bilibili/bili-subbatch
// @version      0.8.2
// @description  B站字幕+AI：可自由滚动的流式阅读 + 宽松排版
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
// @grant        GM_xmlhttpRequest
// @grant        GM_setClipboard
// @grant        GM_addStyle
// @grant        GM_info
// @grant        GM_setValue
// @grant        GM_getValue
// @run-at       document-idle
// @license      MIT
// ==/UserScript==

/**
 * v0.8 — Peer AI-userscript practices: page fetch stream first,
 * GM stream fallback (no timeout), stick-bottom scroll, GM storage.
 * See PEER_AI_PRACTICES.md.
 */

(function () {
  "use strict";

  const SCRIPT_VERSION =
    (typeof GM_info !== "undefined" && GM_info?.script?.version) || "0.8.2";
  const PANEL_ID = "bili-subbatch-panel";
  const UI_STORE_KEY = "bili-subbatch-ui-v2";
  /** v2：默认 stream=true，避免非流式长推理被中间层 10s 掐断 (client_gone) */
  const AI_STORE_KEY = "bili-subbatch-ai-v2";
  const MAX_SUBTITLE_CHARS = 18000;
  const WBI_TTL_MS = 600_000;
  const DEFAULT_DELAY_MS = 400;
  const DEFAULT_MAX_PAGES = 20;
  const MIN_W = 420;
  const MIN_H = 520;
  const DOCK_EDGE_PX = 32;
  const DOCK_SNAP_PX = 36;

  /** OpenAI 兼容默认值（密钥仅存 localStorage，可在面板修改） */
  const AI_DEFAULTS = {
    baseUrl: "https://newapi-jp1.202820.xyz/v1",
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
      "你是资深内容分析助手。根据用户提供的 B 站视频字幕，输出结构清晰、可执行的中文笔记。" +
      "需要时使用 Markdown：标题、列表、表格；代码用 fenced code block 并标注语言；" +
      "流程/架构用 mermaid 代码块（```mermaid）。不要编造字幕中不存在的事实。" +
      "最终答案写在正文里，尽量简洁。",
    userPromptTemplate:
      "请分析以下字幕并输出：\n" +
      "1. 一句话摘要\n2. 要点列表\n3. 关键概念/术语\n4. 若有步骤或架构，用 mermaid 图\n" +
      "5. 可行动的后续建议\n\n" +
      "【元信息】\n标题：{{title}}\nBV：{{bvid}}\nUP：{{author}}\n\n" +
      "【字幕全文】\n{{subtitle}}\n",
  };
  const UA =
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 " +
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36";

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
    const parts = [];
    if (reasoning && String(reasoning).trim()) {
      parts.push("### 思考过程\n\n" + String(reasoning).trim());
    }
    if (content && String(content).trim()) {
      parts.push(String(content).trim());
    }
    return parts.join("\n\n---\n\n");
  }

  function truncateForAi(text, maxChars) {
    const s = String(text || "");
    const lim = maxChars == null ? 18000 : maxChars;
    if (s.length <= lim) return { text: s, truncated: false, originalLen: s.length };
    return {
      text:
        s.slice(0, lim) +
        `\n\n…[字幕已截断 ${s.length - lim} 字，避免超长 token 导致超时/断连]`,
      truncated: true,
      originalLen: s.length,
    };
  }

  /** Peer scroll pattern: stick when distance-to-bottom < threshold */
  function shouldStickBottom(scrollHeight, scrollTop, clientHeight, threshold) {
    const th = threshold == null ? 48 : threshold;
    return scrollHeight - scrollTop - clientHeight < th;
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
    aiStickBottom: true, // 流式粘底；用户上滑后暂停
    aiPaintRaf: 0,
    aiPendingText: "",
    renderLibsReady: false,
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
        font-family: "JetBrains Mono", "Fira Code", ui-monospace, SFMono-Regular,
          "Segoe UI", "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
        font-size: 12px;
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
        flex: 1;
        overflow: hidden;
      }
      #${PANEL_ID} .bsb-view.active { display: flex; }

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
        flex: 1; min-height: 0; display: flex; flex-direction: column;
        border-radius: 16px; overflow: hidden;
        border: 1px solid color-mix(in srgb, var(--ctp-surface1) 45%, transparent);
        background:
          radial-gradient(120% 80% at 100% 0%,
            color-mix(in srgb, var(--ctp-mauve) 10%, transparent), transparent 55%),
          color-mix(in srgb, var(--ctp-crust) 55%, transparent);
        box-shadow: inset 0 1px 0 color-mix(in srgb, var(--ctp-overlay2) 10%, transparent);
        position: relative;
      }
      #${PANEL_ID} .bsb-ai-canvas-bar {
        display: flex; align-items: center; justify-content: space-between; gap: 8px;
        padding: 8px 12px; flex-shrink: 0;
        border-bottom: 1px solid color-mix(in srgb, var(--ctp-surface0) 70%, transparent);
        background: color-mix(in srgb, var(--ctp-mantle) 45%, transparent);
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
        display: flex; flex-direction: column; flex: 1; min-height: 0; overflow: hidden;
        position: relative;
      }
      #${PANEL_ID} .bsb-ai-stream .bsb-ai-raw { display: none !important; }

      /* 唯一滚动容器：宽松阅读排版 */
      #${PANEL_ID} .bsb-ai-md {
        flex: 1; min-height: 0; overflow-y: auto; overflow-x: hidden;
        padding: 22px 22px 56px;
        font-size: 15px;
        line-height: 1.85;
        letter-spacing: 0.02em;
        color: var(--ctp-text);
        scroll-behavior: auto;
        overscroll-behavior: contain;
        -webkit-overflow-scrolling: touch;
        font-family: "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei",
          "Noto Sans SC", system-ui, sans-serif;
      }
      #${PANEL_ID} .bsb-ai-content {
        max-width: 42em;
        margin: 0 auto;
      }
      #${PANEL_ID} .bsb-ai-stream-body {
        margin: 0;
        white-space: pre-wrap;
        word-break: break-word;
        overflow-wrap: anywhere;
        font-size: 15px;
        line-height: 1.9;
        letter-spacing: 0.03em;
        color: var(--ctp-text);
        font-family: inherit;
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
        font-size: 1.45em; margin: 1.4em 0 0.65em;
        padding-bottom: 0.35em;
        border-bottom: 1px solid color-mix(in srgb, var(--ctp-surface1) 50%, transparent);
      }
      #${PANEL_ID} .bsb-ai-md h1:first-child { margin-top: 0.2em; }
      #${PANEL_ID} .bsb-ai-md h2 {
        font-size: 1.22em; margin: 1.35em 0 0.55em; color: var(--ctp-mauve);
      }
      #${PANEL_ID} .bsb-ai-md h3 {
        font-size: 1.08em; margin: 1.2em 0 0.5em; color: var(--ctp-sapphire);
      }
      #${PANEL_ID} .bsb-ai-md p {
        margin: 0.85em 0;
        line-height: 1.9;
      }
      #${PANEL_ID} .bsb-ai-md ul,
      #${PANEL_ID} .bsb-ai-md ol {
        margin: 0.85em 0;
        padding-left: 1.5em;
      }
      #${PANEL_ID} .bsb-ai-md li {
        margin: 0.45em 0;
        line-height: 1.85;
        padding-left: 0.15em;
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
        background: color-mix(in srgb, var(--ctp-base) 50%, transparent);
        border-radius: 14px; padding: 18px; margin: 1.1em 0;
        text-align: center; overflow: auto;
        border: 1px solid color-mix(in srgb, var(--ctp-surface0) 45%, transparent);
      }
      #${PANEL_ID} .bsb-ai-md .bsb-code-lang {
        display: block; font-size: 10px; color: var(--ctp-overlay1);
        margin-bottom: 10px; text-transform: lowercase; letter-spacing: 0.06em;
      }
      #${PANEL_ID} .bsb-ai-md hr {
        border: none; height: 1px; margin: 1.6em 0;
        background: color-mix(in srgb, var(--ctp-surface1) 55%, transparent);
      }
      /* 思考过程更淡、更松 */
      #${PANEL_ID} .bsb-ai-md h3:first-child {
        color: var(--ctp-overlay1); font-size: 0.95em; font-weight: 650;
        letter-spacing: 0.06em; text-transform: uppercase;
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
        state.ui.dock = null;
        state.ui.dockExpanded = false;
      }
      applyPanelGeometry();
      saveUiGeom();
    }

    function setDock(side) {
      // side: 'left' | 'right' | null
      if (side) {
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
      <aside class="bsb-sidebar" role="dialog" aria-label="Bili SubBatch Workspace" aria-hidden="true">
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
            <div class="bsb-ai-canvas-wrap">
              <div class="bsb-ai-canvas-bar">
                <span class="bsb-bar-left">
                  <span class="bsb-live-dot" aria-hidden="true"></span>
                  <span>Output</span>
                  <span data-role="ai-canvas-meta">就绪</span>
                </span>
                <span class="bsb-bar-actions">
                  <button type="button" class="bsb-mini on" data-act="ai-stick" title="流式时自动滚到底">粘底</button>
                  <button type="button" class="bsb-mini" data-act="ai-copy" title="复制当前输出">复制</button>
                  <button type="button" class="bsb-mini" data-act="ai-top" title="回到顶部">顶部</button>
                </span>
              </div>
              <div class="bsb-ai-stream" data-role="ai-stream">
                <pre class="bsb-ai-raw" data-role="ai-raw" hidden></pre>
                <div class="bsb-ai-md" data-role="ai-md">
                  <div class="bsb-ai-content" data-role="ai-content">
                    <div class="bsb-empty">
                      <div class="bsb-empty-ico">✦</div>
                      <strong>还没有分析结果</strong>
                      <span>在「字幕库」扫描并勾选，再点「开始分析」。生成时可自由上滑阅读；需要跟随时点右下角「↓ 最新」。</span>
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
              <label>最多页 <input type="number" data-role="max-pages" min="1" max="100" value="${DEFAULT_MAX_PAGES}"></label>
              <label>间隔ms <input type="number" data-role="delay" min="0" max="5000" step="50" value="${DEFAULT_DELAY_MS}"></label>
            </div>
            <div class="bsb-list" data-role="list">
              <div class="bsb-empty">
                <div class="bsb-empty-ico">≡</div>
                <strong>字幕库为空</strong>
                <span>点「扫描当前页」加载列表，勾选后可下载或送 AI</span>
              </div>
            </div>
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
                  <label>User 模板（{{title}} {{bvid}} {{author}} {{subtitle}}）
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
                  AI 笔记为主画布；字幕库负责扫描与导出；密钥只存本机 localStorage。
                </p>
              </div>
            </div>
          </section>
        </div>
        <div class="bsb-statusbar">
          <span class="bsb-status-dot" data-role="status-dot"></span>
          <div class="bsb-status" data-role="status">就绪 · AI 工作台</div>
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
    root.querySelector('[data-role="mode"]').addEventListener("change", (e) => {
      state.mode = e.target.value || "auto";
      refreshContextUI();
      setStatus(
        state.mode === "auto"
          ? "已切回自动识别（默认偏单个视频）"
          : `已手动指定：${TYPE_LABEL[state.mode] || state.mode}`,
      );
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
      box.innerHTML = `<div class="bsb-empty">列表为空 · 点「扫描当前页」</div>`;
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
        return `<tr data-i="${i}">
          <td><input type="checkbox" data-i="${i}" ${it.selected ? "checked" : ""}></td>
          <td class="bsb-t" title="${escapeAttr(it.title)}">${escapeHtml(it.title || it.bvid)}</td>
          <td>${escapeHtml(it.bvid)}${it.page > 1 ? " P" + it.page : ""}</td>
          <td class="${stClass}">${stText}</td>
        </tr>`;
      })
      .join("");
    box.innerHTML = `<table>
      <thead><tr>
        <th style="width:28px"></th><th>标题</th><th style="width:96px">BV</th><th style="width:48px">状态</th>
      </tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
    box.querySelectorAll('input[type="checkbox"][data-i]').forEach((cb) => {
      cb.addEventListener("change", () => {
        const i = Number(cb.getAttribute("data-i"));
        if (state.items[i]) state.items[i].selected = cb.checked;
        refreshAiChips();
      });
    });
    refreshAiChips();
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
      state.aiStickBottom = !state.aiStickBottom;
      if (state.aiStickBottom) scrollAiToBottom(true);
      else updateJumpLatestBtn();
      setStatus(
        state.aiStickBottom
          ? "跟随最新输出"
          : "已暂停跟随 · 可自由滚动 · 点「↓ 最新」回到底部",
      );
      return;
    }
    if (act === "ai-jump") {
      state.aiStickBottom = true;
      scrollAiToBottom(true);
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
    if (act === "ai-top") {
      scrollAiToTop();
      setStatus("已回到顶部");
      return;
    }
    if (state.busy && act !== "cancel") return;

    if (act === "clear") {
      state.items = [];
      state.meta = {};
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
    } catch (_) {
      /* */
    }
    try {
      const v = localStorage.getItem(key);
      if (v != null) return v;
    } catch (_) {
      /* */
    }
    return fallback;
  }

  function storageSet(key, value) {
    try {
      if (typeof GM_setValue === "function") GM_setValue(key, value);
    } catch (_) {
      /* */
    }
    try {
      localStorage.setItem(key, value);
    } catch (_) {
      /* */
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
    if (stickBtn) stickBtn.classList.toggle("on", !!state.aiStickBottom);
    if (!jump) return;
    // 未粘底且有内容时显示「↓ 最新」
    const show = !state.aiStickBottom && !!(state.aiRaw || state.aiBusy);
    jump.classList.toggle("show", show);
  }

  /**
   * 自由滚动优先：
   * - wheel/touch/指针上滑立刻取消粘底（不必等 scroll 阈值）
   * - 仅当用户在底部附近才恢复粘底
   * - 禁止 scrollIntoView（会带动父级/页面，像“锁死滚动”）
   */
  function bindAiScrollBehavior(root) {
    const box = root.querySelector('[data-role="ai-md"]');
    if (!box || box.dataset.bsbScrollBound === "1") return;
    box.dataset.bsbScrollBound = "1";

    const onUserIntentLeaveBottom = () => {
      if (!state.aiStickBottom) return;
      state.aiStickBottom = false;
      updateJumpLatestBtn();
    };

    box.addEventListener(
      "wheel",
      (e) => {
        // 向上滚 = 想自由阅读
        if (e.deltaY < 0) onUserIntentLeaveBottom();
      },
      { passive: true },
    );
    box.addEventListener(
      "touchstart",
      () => {
        // 触摸开始也视为用户接管
        box._bsbTouchY = null;
      },
      { passive: true },
    );
    box.addEventListener(
      "touchmove",
      (e) => {
        const y = e.touches && e.touches[0] ? e.touches[0].clientY : null;
        if (y == null) return;
        if (box._bsbTouchY != null && y > box._bsbTouchY + 4) {
          // finger down = content moves up = reading earlier text
          onUserIntentLeaveBottom();
        }
        box._bsbTouchY = y;
      },
      { passive: true },
    );

    box.addEventListener(
      "scroll",
      () => {
        const atBottom = shouldStickBottom(
          box.scrollHeight,
          box.scrollTop,
          box.clientHeight,
          64,
        );
        // 只有回到底部才自动重新粘底；上滑不会被强制拉回
        if (atBottom) state.aiStickBottom = true;
        else state.aiStickBottom = false;
        updateJumpLatestBtn();
      },
      { passive: true },
    );
  }

  function scrollAiToBottom(force) {
    if (!force && !state.aiStickBottom) {
      updateJumpLatestBtn();
      return;
    }
    const root = document.getElementById(PANEL_ID);
    if (!root) return;
    const box = root.querySelector('[data-role="ai-md"]');
    if (!box) return;
    // 只用 scrollTop，避免 scrollIntoView 牵动外层
    requestAnimationFrame(() => {
      box.scrollTop = box.scrollHeight;
      requestAnimationFrame(() => {
        box.scrollTop = box.scrollHeight;
        updateJumpLatestBtn();
      });
    });
  }

  function scrollAiToTop() {
    const box = document.querySelector(`#${PANEL_ID} [data-role="ai-md"]`);
    if (box) box.scrollTop = 0;
    state.aiStickBottom = false;
    updateJumpLatestBtn();
  }

  /** 流式轻量绘制：保留 scrollTop 当用户自由阅读时不被重置 */
  function paintAiStreamText(full) {
    state.aiPendingText = full || "";
    if (state.aiPaintRaf) return;
    state.aiPaintRaf = requestAnimationFrame(() => {
      state.aiPaintRaf = 0;
      const root = document.getElementById(PANEL_ID);
      if (!root) return;
      const box = root.querySelector('[data-role="ai-md"]');
      const content = root.querySelector('[data-role="ai-content"]');
      if (!content || !box) return;

      const prevTop = box.scrollTop;
      const prevHeight = box.scrollHeight;
      const text = state.aiPendingText || "…";

      content.innerHTML =
        `<pre class="bsb-ai-stream-body">${escapeHtml(text)}</pre>` +
        (state.aiBusy ? `<span class="bsb-ai-caret" aria-hidden="true"></span>` : "");

      if (state.aiStickBottom) {
        box.scrollTop = box.scrollHeight;
      } else {
        // 内容增高时保持视口内容稳定（不把用户拽走）
        const delta = box.scrollHeight - prevHeight;
        if (delta > 0) box.scrollTop = prevTop;
        else box.scrollTop = prevTop;
      }
      updateJumpLatestBtn();
    });
  }

  function buildSubtitlePayload(items) {
    return items
      .map((it) => {
        const head = `=== ${it.bvid}${it.page > 1 ? " P" + it.page : ""} ${it.title || ""} ===`;
        const body = cuesToTxt(it.data || []);
        return head + "\n" + body;
      })
      .join("\n\n");
  }

  function loadScriptOnce(src, globalCheck) {
    return new Promise((resolve, reject) => {
      if (globalCheck && globalCheck()) {
        resolve();
        return;
      }
      const existed = document.querySelector(`script[data-bsb-src="${src}"]`);
      if (existed) {
        existed.addEventListener("load", () => resolve());
        existed.addEventListener("error", () => reject(new Error("load " + src)));
        if (globalCheck && globalCheck()) resolve();
        return;
      }
      const s = document.createElement("script");
      s.src = src;
      s.async = true;
      s.dataset.bsbSrc = src;
      s.onload = () => resolve();
      s.onerror = () => reject(new Error("failed to load " + src));
      (document.head || document.documentElement).appendChild(s);
    });
  }

  function loadCssOnce(href) {
    if (document.querySelector(`link[data-bsb-href="${href}"]`)) return;
    const l = document.createElement("link");
    l.rel = "stylesheet";
    l.href = href;
    l.dataset.bsbHref = href;
    (document.head || document.documentElement).appendChild(l);
  }

  async function ensureRenderLibs() {
    if (state.renderLibsReady) return;
    loadCssOnce(
      "https://cdn.jsdelivr.net/gh/highlightjs/cdn-release@11.9.0/build/styles/atom-one-dark.min.css",
    );
    await loadScriptOnce(
      "https://cdn.jsdelivr.net/npm/marked@12.0.2/marked.min.js",
      () => typeof marked !== "undefined",
    );
    await loadScriptOnce(
      "https://cdn.jsdelivr.net/gh/highlightjs/cdn-release@11.9.0/build/highlight.min.js",
      () => typeof hljs !== "undefined",
    );
    // common languages pack (full build already has many; languages min is extra)
    try {
      await loadScriptOnce(
        "https://cdn.jsdelivr.net/gh/highlightjs/cdn-release@11.9.0/build/languages/python.min.js",
        () => true,
      );
    } catch (_) {
      /* optional */
    }
    await loadScriptOnce(
      "https://cdn.jsdelivr.net/npm/mermaid@10.9.1/dist/mermaid.min.js",
      () => typeof mermaid !== "undefined",
    );
    if (typeof mermaid !== "undefined") {
      mermaid.initialize({
        startOnLoad: false,
        theme: "dark",
        securityLevel: "loose",
        fontFamily: "JetBrains Mono, Fira Code, monospace",
      });
    }
    if (typeof marked !== "undefined") {
      marked.setOptions({
        gfm: true,
        breaks: true,
        mangle: false,
        headerIds: false,
      });
    }
    state.renderLibsReady = true;
  }

  function simpleMarkdownFallback(md) {
    // very small fallback if CDN blocked
    let html = escapeHtml(md);
    html = html.replace(
      /```(\w*)\n([\s\S]*?)```/g,
      (_, lang, code) =>
        `<pre><span class="bsb-code-lang">${escapeHtml(lang || "text")}</span><code>${escapeHtml(code)}</code></pre>`,
    );
    html = html.replace(/^### (.+)$/gm, "<h3>$1</h3>");
    html = html.replace(/^## (.+)$/gm, "<h2>$1</h2>");
    html = html.replace(/^# (.+)$/gm, "<h1>$1</h1>");
    html = html.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
    html = html.replace(/`([^`]+)`/g, "<code>$1</code>");
    html = html.replace(/\n\n/g, "</p><p>");
    return `<p>${html}</p>`;
  }

  async function renderAiMarkdown(md, { streaming } = {}) {
    const root = ensurePanel();
    const box = root.querySelector('[data-role="ai-md"]');
    const contentHost = root.querySelector('[data-role="ai-content"]');
    const host = contentHost || box;
    if (!host) return;

    if (streaming) {
      paintAiStreamText(md);
      return;
    }

    try {
      await ensureRenderLibs();
    } catch (e) {
      host.innerHTML =
        simpleMarkdownFallback(md || "") +
        `<p style="color:var(--ctp-peach)">渲染库加载失败：${escapeHtml(e.message || e)}（已用简易 Markdown）</p>` +
        `<div class="bsb-ai-anchor" data-role="ai-anchor"></div>`;
      // 完成后滚到顶部便于阅读
      if (box) box.scrollTop = 0;
      return;
    }

    // 拆出 mermaid 块，避免 marked 破坏
    const mermaidBlocks = [];
    const md2 = String(md || "").replace(
      /```mermaid\s*\n([\s\S]*?)```/gi,
      (_, code) => {
        const i = mermaidBlocks.length;
        mermaidBlocks.push(code.trim());
        return `\n\n<div class="mermaid" data-bsb-m="${i}">${escapeHtml(code.trim())}</div>\n\n`;
      },
    );

    let html;
    try {
      html = marked.parse(md2);
    } catch (_) {
      html = simpleMarkdownFallback(md2);
    }
    host.innerHTML = html;

    // 代码高亮
    host.querySelectorAll("pre code").forEach((block) => {
      const pre = block.parentElement;
      const cls = block.className || "";
      const m = cls.match(/language-([\w#+-]+)/i);
      if (m && pre && !pre.querySelector(".bsb-code-lang")) {
        const tag = document.createElement("span");
        tag.className = "bsb-code-lang";
        tag.textContent = m[1];
        pre.insertBefore(tag, block);
      }
      if (typeof hljs !== "undefined") {
        try {
          hljs.highlightElement(block);
        } catch (_) {
          /* unknown lang */
        }
      }
    });

    // Mermaid
    if (typeof mermaid !== "undefined" && mermaidBlocks.length) {
      const nodes = host.querySelectorAll(".mermaid[data-bsb-m]");
      for (const node of nodes) {
        const idx = Number(node.getAttribute("data-bsb-m"));
        const code = mermaidBlocks[idx];
        if (!code) continue;
        try {
          const id = "bsb-mmd-" + Date.now() + "-" + idx;
          const { svg } = await mermaid.render(id, code);
          node.innerHTML = svg;
        } catch (err) {
          node.innerHTML =
            `<pre class="bsb-code-lang">mermaid 渲染失败</pre><pre><code>${escapeHtml(code)}\n\n${escapeHtml(err.message || err)}</code></pre>`;
        }
      }
    }
    // 保证锚点仍在 md 底部（host 是 content 时 anchor 在兄弟节点）
    if (box && !box.querySelector('[data-role="ai-anchor"]')) {
      const a = document.createElement("div");
      a.className = "bsb-ai-anchor";
      a.setAttribute("data-role", "ai-anchor");
      box.appendChild(a);
    }
    // 完成后滚到顶部阅读
    if (box) box.scrollTop = 0;
    state.aiStickBottom = false;
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
    state.aiStickBottom = true;
    const stickBtn = root.querySelector('[data-act="ai-stick"]');
    if (stickBtn) stickBtn.classList.add("on");
    setAiBusy(true);
    setBusy(true);
    setStatus("准备字幕并连接 AI…");

    const contentHost = root.querySelector('[data-role="ai-content"]');
    if (contentHost) {
      contentHost.innerHTML =
        `<div class="bsb-empty"><div class="bsb-empty-ico">◌</div><strong>正在生成…</strong>` +
        `<span>页内 fetch 流式 · 粘底自动滚动 · 可点「粘底」开关</span></div>`;
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
      const vars = {
        title:
          ready.map((x) => x.title).filter(Boolean).join(" / ") ||
          first.title ||
          "",
        bvid: ready.map((x) => x.bvid).join(", "),
        author: first.author || "",
        subtitle: cut.text,
      };
      const userContent = applyPromptTemplate(cfg.userPromptTemplate, vars);
      const messages = [];
      if (cfg.systemPrompt.trim()) {
        messages.push({ role: "system", content: cfg.systemPrompt });
      }
      messages.push({ role: "user", content: userContent });

      const useStream = cfg.stream !== false;
      setStatus(
        `AI 请求中 · ${cfg.model} · ${useStream ? "SSE流式" : "非流式"} · ${ready.length} 条` +
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

      setStatus("AI 完成 · 正在渲染 Markdown / 代码 / Mermaid…");
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

  // ─── SPA watch ──────────────────────────────────────────────────────────
  let lastHref = location.href;
  function onMaybeNavigate() {
    if (location.href === lastHref) return;
    lastHref = location.href;
    if (state.open) refreshContextUI();
  }

  function boot() {
    ensurePanel();
    refreshContextUI();
    const _push = history.pushState;
    const _replace = history.replaceState;
    history.pushState = function () {
      _push.apply(this, arguments);
      setTimeout(onMaybeNavigate, 0);
    };
    history.replaceState = function () {
      _replace.apply(this, arguments);
      setTimeout(onMaybeNavigate, 0);
    };
    window.addEventListener("popstate", onMaybeNavigate);
    setInterval(onMaybeNavigate, 1500);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
