/**
 * Offline harness: extract // #region pure-logic from shipped userscript
 * and exercise the real function bodies (plus md5 / MIXIN_KEY_ENC_TAB deps).
 */
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";
import assert from "assert";
import crypto from "crypto";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const USERSCRIPT = path.join(__dirname, "bili-subbatch.user.js");

function extractShippedPureLogic() {
  const src = fs.readFileSync(USERSCRIPT, "utf8");
  const start = src.indexOf("// #region pure-logic");
  const end = src.indexOf("// #endregion pure-logic");
  if (start < 0 || end < 0 || end <= start) {
    throw new Error("pure-logic region missing in shipped userscript");
  }
  // Also need MIXIN_KEY_ENC_TAB + md5 from shipped file
  const mixinStart = src.indexOf("const MIXIN_KEY_ENC_TAB");
  const md5Start = src.indexOf("function md5(str)");
  const pureStart = start;
  if (mixinStart < 0 || md5Start < 0) {
    throw new Error("MIXIN_KEY_ENC_TAB or md5 missing");
  }
  // From MIXIN through end of pure-logic
  let chunk = src.slice(mixinStart, end);
  // Close any open function scopes — pure region already closed functions.
  // Wrap in factory that returns exports
  const factory = `
${chunk}
// #endregion pure-logic
return {
  keyFromUrl,
  mixinKey,
  encWbi,
  applyPromptTemplate,
  extractAssistantText,
  extractFromChoice,
  formatAiDisplay,
  truncateForAi,
  shouldStickBottom,
  resolveAiScrollState,
  parseSseDataLine,
  md5,
  MIXIN_KEY_ENC_TAB,
};
`;
  // md5 uses no Node crypto; it's pure JS in the script
  // eslint-disable-next-line no-new-func
  return new Function(factory)();
}

const api = extractShippedPureLogic();
let passed = 0;
function check(name, cond, detail) {
  if (!cond) {
    console.error("FAIL", name, detail || "");
    process.exitCode = 1;
    return;
  }
  console.log("OK", name);
  passed++;
}

// --- WBI ---
const img = "7cd084941338484aae1ad9425b84077c";
const sub = "4932caff0ff746eab6f01bf08b70ac45";
const q = api.encWbi({ bvid: "BV1xx411c7mD", need_elec: 0 }, img, sub, 1700000000);
const expectWbi =
  "bvid=BV1xx411c7mD&need_elec=0&wts=1700000000&w_rid=4b8506556389b2b4a7d71f2a4d2a2d58";
check("enc_wbi vector", q === expectWbi, q);

// --- prompt template ---
const filled = api.applyPromptTemplate(
  "T={{title}} B={{bvid}} S={{subtitle}} X={{missing}}",
  { title: "你好", bvid: "BV1", subtitle: "字幕正文" },
);
check(
  "prompt template",
  filled === "T=你好 B=BV1 S=字幕正文 X=",
  filled,
);

// --- SSE / reasoning ---
const d1 = api.parseSseDataLine(
  'data: {"choices":[{"delta":{"content":"","reasoning":"think "}}]}',
);
check("sse reasoning delta", d1.kind === "delta" && d1.reasoning === "think " && d1.content === "", d1);

const d2 = api.parseSseDataLine(
  'data: {"choices":[{"delta":{"content":"hello","reasoning_content":""}}]}',
);
check("sse content delta", d2.kind === "delta" && d2.content === "hello", d2);

const d3 = api.parseSseDataLine("data: [DONE]");
check("sse done", d3.kind === "done", d3);

const ex = api.extractFromChoice({
  message: { content: null, reasoning_content: "r1", reasoning: "r0" },
});
check(
  "extract prefers reasoning_content",
  ex.content === "" && ex.reasoning === "r1",
  ex,
);

const disp = api.formatAiDisplay("答案", "思考");
check(
  "formatAiDisplay",
  disp.includes("思考过程") && disp.includes("答案") && disp.includes("---"),
  disp,
);

// --- truncate ---
const long = "字".repeat(200);
const tr = api.truncateForAi(long, 50);
check("truncate flags", tr.truncated === true && tr.originalLen === 200, tr);
check("truncate length", tr.text.length > 50 && tr.text.includes("截断"), tr.text.slice(0, 80));
const short = api.truncateForAi("短", 50);
check("truncate short", short.truncated === false && short.text === "短", short);

// --- stick bottom ---
check(
  "stick when near bottom",
  api.shouldStickBottom(1000, 940, 50, 48) === true,
);
check(
  "unstick when scrolled up",
  api.shouldStickBottom(1000, 100, 50, 48) === false,
);

// --- v0.8.4 scroll state machine（修「上滑被 80px 阈值拽回」）---
{
  let s = { stick: true, userReading: false, progScroll: false };
  // 开始：允许 paint 滚底
  let r = api.resolveAiScrollState(s, { type: "start" });
  check("scroll: start allows paint", r.allowPaintScroll === true && r.stick && !r.userReading, r);

  // 用户上滑：禁止 paint 滚底
  r = api.resolveAiScrollState(r, { type: "wheel-up" });
  check("scroll: wheel-up locks reading", r.allowPaintScroll === false && r.userReading, r);

  // 流式内容增高、距底仍 <80 的假 scroll：不得恢复跟随
  r = api.resolveAiScrollState(
    { stick: r.stick, userReading: r.userReading, progScroll: false },
    { type: "scroll", gap: 40 },
  );
  check(
    "scroll: gap40 must NOT re-stick (old bug)",
    r.allowPaintScroll === false && r.userReading === true,
    r,
  );

  // 程序化滚底产生的 scroll：状态不变
  r = api.resolveAiScrollState(
    { stick: true, userReading: false, progScroll: true },
    { type: "scroll", gap: 0 },
  );
  check("scroll: prog scroll ignored", r.allowPaintScroll === true && !r.userReading, r);

  // 用户自己贴底：恢复
  r = api.resolveAiScrollState(
    { stick: false, userReading: true, progScroll: false },
    { type: "scroll", gap: 5 },
  );
  check("scroll: user to bottom resumes", r.allowPaintScroll === true && !r.userReading, r);

  // resume 按钮
  r = api.resolveAiScrollState(
    { stick: false, userReading: true, progScroll: false },
    { type: "resume" },
  );
  check("scroll: resume button", r.allowPaintScroll === true, r);
}

// --- keyFromUrl ---
check(
  "keyFromUrl",
  api.keyFromUrl("https://i0.hdslb.com/bfs/wbi/abc.png") === "abc",
);

console.log(`\n${passed} assertions ok (shipped pure-logic region)`);
if (process.exitCode) process.exit(1);
