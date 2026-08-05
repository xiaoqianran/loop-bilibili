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
  prepareMarkdownMath,
  replaceMathPlaceholders,
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

// v2：有正文时只展示 content，不把 reasoning / CoT 暴露到界面
const disp = api.formatAiDisplay("答案", "思考");
check("formatAiDisplay body only", disp === "答案", disp);
const dispRationalizing = api.formatAiDisplay("", "思考中");
check(
  "formatAiDisplay reasoning placeholder",
  dispRationalizing === "正在分析字幕并组织笔记…",
  dispRationalizing,
);
const dispEmpty = api.formatAiDisplay("", "");
check("formatAiDisplay empty", dispEmpty === "", dispEmpty);

// --- truncate ---
// lim = max(4000, maxChars)：短上限会被抬到 4000；需用更长文本验证截断
const long = "字".repeat(12000);
const tr = api.truncateForAi(long, 5000);
check(
  "truncate flags",
  tr.truncated === true && tr.originalLen === 12000,
  tr,
);
check(
  "truncate samples middle",
  tr.text.includes("中段采样") && tr.text.includes("省略") && tr.text.length < long.length,
  tr.text.slice(0, 120),
);
const short = api.truncateForAi("短", 50);
check("truncate short", short.truncated === false && short.text === "短", short);
// 未截断：长度低于抬升后的下限
const underFloor = api.truncateForAi("字".repeat(200), 50);
check(
  "truncate under floor keeps all",
  underFloor.truncated === false && underFloor.originalLen === 200,
  underFloor,
);

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

// --- KaTeX math placeholders (v0.8.5) ---
{
  const sample = [
    "行内 $E=mc^2$ 与货币 $12.5 美元",
    "",
    "$$",
    "\\int_0^1 x^2\\,dx = \\frac{1}{3}",
    "$$",
    "",
    "括号 \\(a+b\\) 与",
    "\\[",
    "\\sum_{i=1}^n i",
    "\\]",
    "",
    "```math",
    "\\nabla \\cdot \\mathbf{E} = \\frac{\\rho}{\\varepsilon_0}",
    "```",
    "",
    "代码里不抽：`x = $1` 与",
    "```js",
    "const x = '$not_math$';",
    "```",
  ].join("\n");

  const prep = api.prepareMarkdownMath(sample);
  check("math: extracted count", prep.maths.length === 5, prep.maths);
  check(
    "math: has inline E=mc2",
    prep.maths.some((m) => !m.display && m.tex.includes("E=mc")),
    prep.maths,
  );
  check(
    "math: has display integral",
    prep.maths.some((m) => m.display && m.tex.includes("int_0")),
    prep.maths,
  );
  check(
    "math: has math fence",
    prep.maths.some((m) => m.display && m.tex.includes("nabla")),
    prep.maths,
  );
  check(
    "math: placeholders in md",
    /@@BSBMATH0@@/.test(prep.md) && /@@BSBMATH1@@/.test(prep.md),
    prep.md.slice(0, 200),
  );
  check(
    "math: code not extracted as math",
    prep.md.includes("const x = '$not_math$'") ||
      prep.md.includes("```js"),
    prep.md,
  );
  check(
    "math: currency $12.5 kept",
    prep.md.includes("$12.5"),
    prep.md,
  );

  const filled = api.replaceMathPlaceholders(
    "<p>@@BSBMATH0@@</p><p>@@BSBMATH1@@</p>",
    [
      { tex: "a+b", display: false },
      { tex: "c=d", display: true },
    ],
    (tex, display) =>
      display ? `<div class="K">${tex}</div>` : `<span class="K">${tex}</span>`,
  );
  check(
    "math: replace uses render",
    filled.includes('<span class="K">a+b</span>') &&
      filled.includes('<div class="K">c=d</div>'),
    filled,
  );
  const fb = api.replaceMathPlaceholders("@@BSBMATH0@@", [
    { tex: "x<y", display: false },
  ]);
  check(
    "math: fallback escapes",
    fb.includes("x&lt;y") && fb.includes("bsb-math-fallback"),
    fb,
  );
}

console.log(`\n${passed} assertions ok (shipped pure-logic region)`);
if (process.exitCode) process.exit(1);
