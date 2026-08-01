// ==UserScript==
// @name         Bili SubBatch (loop-bilibili)
// @namespace    https://github.com/loop-bilibili/bili-subbatch
// @version      0.2.0
// @description  B站字幕批量下载：单视频/选集/个人主页/收藏夹/合集/搜索页。协议对齐 packages/bili_subbatch
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
// @grant        GM_xmlhttpRequest
// @grant        GM_setClipboard
// @grant        GM_addStyle
// @grant        GM_info
// @run-at       document-idle
// @license      MIT
// ==/UserScript==

/**
 * v0.2 — multi-source list + batch, aligned with Chrome SubBatch + bili_subbatch.
 *
 * Sources:
 *   video / selection  — view/detail pages[]
 *   user               — /x/space/wbi/arc/search
 *   favorite           — /x/v3/fav/resource/list
 *   collection         — /x/polymer/web-space/seasons_archives_list
 *   search             — /x/web-interface/wbi/search/type
 *
 * Subtitle: WBI → view/detail → player/wbi/v2 → (dm/view | ai_stat) → body → SRT/TXT
 */

(function () {
  "use strict";

  const SCRIPT_VERSION =
    (typeof GM_info !== "undefined" && GM_info?.script?.version) || "0.2.0";
  const PANEL_ID = "bili-subbatch-panel";
  const WBI_TTL_MS = 600_000;
  const DEFAULT_DELAY_MS = 400;
  const DEFAULT_MAX_PAGES = 20;
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

  // ─── WBI ────────────────────────────────────────────────────────────────
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
  function detectContext(href) {
    let u;
    try {
      u = new URL(href || location.href);
    } catch (_) {
      return { type: "unknown" };
    }
    const host = u.hostname.toLowerCase();
    const path = u.pathname;

    // search
    if (/^search\.bilibili\.com$/i.test(host)) {
      if (/^\/(all|video)\/?$/i.test(path)) {
        const keyword = (u.searchParams.get("keyword") || "").trim();
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
          const page = Math.max(1, parseInt(u.searchParams.get("page") || "1", 10) || 1);
          return {
            type: "search",
            keyword,
            order: allowed.has(order) ? order : "totalrank",
            page,
          };
        }
      }
      return { type: "unknown" };
    }

    // space
    if (/^space\.bilibili\.com$/i.test(host)) {
      // collection: /{mid}/lists/{sid}
      let m = path.match(/^\/(\d+)\/lists\/(\d+)\/?$/i);
      if (m) {
        return { type: "collection", mid: m[1], season_id: m[2] };
      }
      // collectiondetail?sid=
      m = path.match(/^\/(\d+)\/channel\/collectiondetail\/?$/i);
      if (m) {
        const sid = u.searchParams.get("sid") || u.searchParams.get("season_id");
        if (sid && /^\d+$/.test(sid)) {
          return { type: "collection", mid: m[1], season_id: sid };
        }
      }
      // favorite
      const fid = (u.searchParams.get("fid") || "").trim();
      if (fid && /^\d+$/.test(fid) && /\/favlist\/?$/i.test(path)) {
        return { type: "favorite", media_id: fid };
      }
      // user homepage / video tab
      m = path.match(/^\/(\d+)(?:\/(?:video|upload\/video)?)?\/?$/i);
      if (m) {
        const segs = path.split("/").filter(Boolean);
        if (
          segs.length === 1 ||
          (segs.length === 2 && /^(video|upload)$/i.test(segs[1])) ||
          (segs.length === 3 && segs[1] === "upload" && segs[2] === "video")
        ) {
          return { type: "user", mid: m[1] };
        }
      }
      // space root with only mid
      m = path.match(/^\/(\d+)\/?$/);
      if (m) return { type: "user", mid: m[1] };
      return { type: "unknown" };
    }

    // www medialist / favlist
    if (/^(www\.)?bilibili\.com$/i.test(host)) {
      let m = path.match(/^\/medialist\/(?:detail|play)\/ml(\d+)\/?$/i);
      if (m) return { type: "favorite", media_id: m[1] };
      m = path.match(/^\/(?:fav|list)\/(?:ml)?(\d+)\/?$/i);
      if (m) return { type: "favorite", media_id: m[1] };
      if (/^\/favlist\/?$/i.test(path)) {
        const fid = (u.searchParams.get("fid") || "").trim();
        if (fid && /^\d+$/.test(fid)) return { type: "favorite", media_id: fid };
      }

      // video
      const bvid = extractBvid(path) || extractBvid(href);
      if (bvid && /\/video\//i.test(path)) {
        const p = Math.max(1, parseInt(u.searchParams.get("p") || "1", 10) || 1);
        return { type: "video", bvid, page: p };
      }

      // list multi collection play sometimes embeds BV
      if (/\/list\//i.test(path)) {
        const bvid2 = extractBvid(href) || extractBvid(u.searchParams.get("bvid") || "");
        if (bvid2) {
          return {
            type: "video",
            bvid: bvid2,
            page: Math.max(1, parseInt(u.searchParams.get("p") || "1", 10) || 1),
          };
        }
      }
    }

    return { type: "unknown" };
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
    const r = await fetchSubtitle(bvid, 1);
    // We only need meta; even empty subtitle is fine
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
      };
    }
    const p = 1;
    return {
      items: [
        {
          bvid: r.bvid || bvid,
          aid: r.aid,
          title: r.title || bvid,
          author: r.author || "",
          page: p,
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
    ctx: null,
    items: [], // { bvid, title, author, page, selected, status?, cues?, error? }
    meta: {},
    delayMs: DEFAULT_DELAY_MS,
    maxPages: DEFAULT_MAX_PAGES,
  };

  // ─── UI ─────────────────────────────────────────────────────────────────
  function injectStyles() {
    GM_addStyle(`
      #${PANEL_ID} {
        position: fixed; right: 16px; bottom: 80px; z-index: 2147483646;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC",
          "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
        font-size: 12px; color: #1f2329; line-height: 1.4;
      }
      #${PANEL_ID} * { box-sizing: border-box; }
      #${PANEL_ID} .bsb-fab {
        width: 48px; height: 48px; border-radius: 50%; border: none; cursor: pointer;
        background: linear-gradient(135deg, #00a1d6, #0b7eb8); color: #fff;
        font-weight: 700; font-size: 12px;
        box-shadow: 0 6px 20px rgba(0,161,214,.45);
        display: flex; align-items: center; justify-content: center;
      }
      #${PANEL_ID} .bsb-fab:hover { filter: brightness(1.06); }
      #${PANEL_ID} .bsb-card {
        display: none; width: 380px; max-height: min(78vh, 640px);
        margin-bottom: 10px; background: #fff; border-radius: 12px;
        box-shadow: 0 12px 40px rgba(15,23,42,.2);
        border: 1px solid rgba(0,0,0,.06); overflow: hidden;
        flex-direction: column;
      }
      #${PANEL_ID}.open .bsb-card { display: flex; }
      #${PANEL_ID} .bsb-head {
        display: flex; align-items: center; justify-content: space-between;
        padding: 10px 12px; color: #fff;
        background: linear-gradient(135deg, #00a1d6, #0b7eb8); flex-shrink: 0;
      }
      #${PANEL_ID} .bsb-head strong { font-size: 13px; }
      #${PANEL_ID} .bsb-head .bsb-ver { opacity: .85; font-size: 11px; margin-left: 6px; }
      #${PANEL_ID} .bsb-close {
        background: transparent; border: none; color: #fff; cursor: pointer;
        font-size: 18px; line-height: 1;
      }
      #${PANEL_ID} .bsb-body {
        padding: 10px 12px 12px; overflow: hidden;
        display: flex; flex-direction: column; gap: 8px; min-height: 0;
      }
      #${PANEL_ID} .bsb-badge {
        display: inline-block; background: #e8f7fc; color: #0b7eb8;
        border-radius: 999px; padding: 2px 8px; font-weight: 600; font-size: 11px;
      }
      #${PANEL_ID} .bsb-meta { color: #646a73; word-break: break-all; font-size: 11px; }
      #${PANEL_ID} .bsb-toolbar { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
      #${PANEL_ID} .bsb-toolbar button, #${PANEL_ID} .bsb-actions button {
        height: 28px; border-radius: 7px; border: 1px solid #d0d3d6;
        background: #fff; cursor: pointer; font-size: 12px; padding: 0 10px; color: #1f2329;
      }
      #${PANEL_ID} .bsb-toolbar button.primary, #${PANEL_ID} .bsb-actions button.primary {
        background: #00a1d6; border-color: #00a1d6; color: #fff; font-weight: 600;
      }
      #${PANEL_ID} .bsb-toolbar button:disabled, #${PANEL_ID} .bsb-actions button:disabled {
        opacity: .55; cursor: not-allowed;
      }
      #${PANEL_ID} .bsb-toolbar button.danger { color: #c92a2a; border-color: #f1aeb5; }
      #${PANEL_ID} .bsb-opts {
        display: flex; flex-wrap: wrap; gap: 8px; align-items: center; color: #4e5969;
      }
      #${PANEL_ID} .bsb-opts label { display: inline-flex; align-items: center; gap: 4px; }
      #${PANEL_ID} .bsb-opts input[type="number"] {
        width: 56px; height: 24px; border: 1px solid #dee0e3; border-radius: 5px;
        padding: 0 4px; font-size: 12px;
      }
      #${PANEL_ID} .bsb-list {
        border: 1px solid #e5e6eb; border-radius: 8px; overflow: auto;
        max-height: 260px; min-height: 80px; background: #fafbfc;
      }
      #${PANEL_ID} .bsb-list table { width: 100%; border-collapse: collapse; }
      #${PANEL_ID} .bsb-list th, #${PANEL_ID} .bsb-list td {
        padding: 5px 6px; border-bottom: 1px solid #eef0f3; text-align: left;
        vertical-align: top;
      }
      #${PANEL_ID} .bsb-list th {
        position: sticky; top: 0; background: #f2f3f5; font-weight: 600; z-index: 1;
      }
      #${PANEL_ID} .bsb-list .bsb-t {
        max-width: 210px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
      }
      #${PANEL_ID} .bsb-list .st-ok { color: #2b8a3e; }
      #${PANEL_ID} .bsb-list .st-empty { color: #e67700; }
      #${PANEL_ID} .bsb-list .st-err { color: #c92a2a; }
      #${PANEL_ID} .bsb-list .st-wait { color: #8a919f; }
      #${PANEL_ID} .bsb-actions { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 6px; }
      #${PANEL_ID} .bsb-status {
        font-size: 12px; color: #646a73; min-height: 16px; word-break: break-word;
      }
      #${PANEL_ID} .bsb-status.ok { color: #2b8a3e; }
      #${PANEL_ID} .bsb-status.err { color: #c92a2a; }
      #${PANEL_ID} .bsb-foot { font-size: 10px; color: #8a919f; }
      #${PANEL_ID} .bsb-empty {
        padding: 20px 10px; text-align: center; color: #8a919f;
      }
    `);
  }

  function ensurePanel() {
    let root = document.getElementById(PANEL_ID);
    if (root) return root;
    injectStyles();
    root = document.createElement("div");
    root.id = PANEL_ID;
    root.innerHTML = `
      <div class="bsb-card" role="dialog" aria-label="Bili SubBatch">
        <div class="bsb-head">
          <div><strong>字幕下载</strong><span class="bsb-ver">v${SCRIPT_VERSION}</span></div>
          <button type="button" class="bsb-close" title="关闭">×</button>
        </div>
        <div class="bsb-body">
          <div>
            <span class="bsb-badge" data-role="type">—</span>
            <div class="bsb-meta" data-role="ctx">—</div>
          </div>
          <div class="bsb-toolbar">
            <button type="button" class="primary" data-act="scan">扫描当前页</button>
            <button type="button" data-act="sel-all">全选</button>
            <button type="button" data-act="sel-none">全不选</button>
            <button type="button" class="danger" data-act="cancel" style="display:none">停止</button>
          </div>
          <div class="bsb-opts">
            <label>最多页 <input type="number" data-role="max-pages" min="1" max="100" value="${DEFAULT_MAX_PAGES}"></label>
            <label>间隔ms <input type="number" data-role="delay" min="0" max="5000" step="50" value="${DEFAULT_DELAY_MS}"></label>
            <label class="bsb-expand-parts" style="display:none">
              <input type="checkbox" data-role="expand-parts" checked> 展开全部分P
            </label>
          </div>
          <div class="bsb-list" data-role="list">
            <div class="bsb-empty">点「扫描当前页」加载视频列表</div>
          </div>
          <div class="bsb-actions">
            <button type="button" class="primary" data-act="dl-srt">下载 SRT</button>
            <button type="button" data-act="dl-txt">下载 TXT</button>
            <button type="button" data-act="copy">复制全文</button>
            <button type="button" data-act="copy-bvid">复制 BV 列表</button>
            <button type="button" data-act="dl-ok-only" title="仅下载已成功项">再下成功项</button>
            <button type="button" data-act="clear">清空列表</button>
          </div>
          <div class="bsb-status" data-role="status">就绪</div>
          <div class="bsb-foot">对齐 SubBatch / bili_subbatch · 使用当前登录 Cookie · 请适度限速</div>
        </div>
      </div>
      <button type="button" class="bsb-fab" title="Bili SubBatch">CC</button>
    `;
    document.documentElement.appendChild(root);

    root.querySelector(".bsb-fab").addEventListener("click", () => {
      state.open = !state.open;
      root.classList.toggle("open", state.open);
      if (state.open) refreshContextUI();
    });
    root.querySelector(".bsb-close").addEventListener("click", () => {
      state.open = false;
      root.classList.remove("open");
    });

    root.querySelector('[data-role="max-pages"]').addEventListener("change", (e) => {
      state.maxPages = Math.max(1, Math.min(100, Number(e.target.value) || DEFAULT_MAX_PAGES));
    });
    root.querySelector('[data-role="delay"]').addEventListener("change", (e) => {
      state.delayMs = Math.max(0, Math.min(5000, Number(e.target.value) || DEFAULT_DELAY_MS));
    });

    root.querySelectorAll("[data-act]").forEach((btn) => {
      btn.addEventListener("click", () => onAction(btn.getAttribute("data-act")));
    });

    return root;
  }

  function setStatus(text, cls) {
    const el = document.querySelector(`#${PANEL_ID} [data-role="status"]`);
    if (!el) return;
    el.textContent = text;
    el.classList.remove("ok", "err");
    if (cls) el.classList.add(cls);
  }

  function setBusy(busy) {
    state.busy = busy;
    const root = ensurePanel();
    root.querySelectorAll("button[data-act]").forEach((b) => {
      const act = b.getAttribute("data-act");
      if (act === "cancel") {
        b.style.display = busy ? "" : "none";
        b.disabled = false;
      } else {
        b.disabled = busy && act !== "cancel";
      }
    });
  }

  function refreshContextUI() {
    const root = ensurePanel();
    state.ctx = detectContext(location.href);
    const ctx = state.ctx;
    root.querySelector('[data-role="type"]').textContent =
      TYPE_LABEL[ctx.type] || ctx.type;
    const bits = [];
    if (ctx.bvid) bits.push(ctx.bvid);
    if (ctx.mid) bits.push(`mid=${ctx.mid}`);
    if (ctx.season_id) bits.push(`season=${ctx.season_id}`);
    if (ctx.media_id) bits.push(`fid=${ctx.media_id}`);
    if (ctx.keyword) bits.push(`「${ctx.keyword}」`);
    if (ctx.order) bits.push(`order=${ctx.order}`);
    if (ctx.page && ctx.type === "video") bits.push(`p=${ctx.page}`);
    root.querySelector('[data-role="ctx"]').textContent =
      bits.join(" · ") || location.href.slice(0, 80);

    const expandWrap = root.querySelector(".bsb-expand-parts");
    expandWrap.style.display =
      ctx.type === "video" || ctx.type === "selection" ? "" : "none";
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
      });
    });
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

  async function doScan() {
    refreshContextUI();
    const ctx = state.ctx || detectContext(location.href);
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
      const expandParts = !!root.querySelector('[data-role="expand-parts"]')?.checked;

      let items = [];
      let meta = {};

      if (ctx.type === "video" || ctx.type === "selection") {
        const bvid = ctx.bvid || extractBvid(location.href);
        if (!bvid) throw new Error("未识别 BV 号");
        setStatus(`读取视频 ${bvid}…`);
        // Always probe pages; if multip and expand → selection list
        const loaded = await loadVideoAsItems(bvid, expandParts);
        items = loaded.items;
        meta = loaded.meta || {};
        // If multip and not expand, still mark as video; if expand → selection feel
        if (expandParts && items.length > 1) {
          state.ctx = { ...ctx, type: "selection", bvid };
          root.querySelector('[data-role="type"]').textContent = TYPE_LABEL.selection;
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
        if (res.truncated) {
          meta.truncated = true;
        }
      } else {
        // fallback: try BV on page / DOM harvest for search-like pages
        const bvid = extractBvid(location.href);
        if (bvid) {
          const loaded = await loadVideoAsItems(bvid, expandParts);
          items = loaded.items;
          meta = loaded.meta || {};
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
              "当前页未识别。请在 视频/个人主页/收藏夹/合集/搜索 页使用，或确认 URL 含 BV/mid/fid/keyword",
            );
          }
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
      const trunc = meta.truncated ? "（已达页数上限，可调大「最多页」）" : "";
      setStatus(`已加载 ${state.items.length} 条${trunc}`, "ok");
      if (meta.name || meta.title || meta.keyword) {
        const label = meta.name || meta.title || meta.keyword;
        ensurePanel().querySelector('[data-role="ctx"]').textContent += ` · ${label}`;
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
