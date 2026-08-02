/**
 * DOM simulation: prove free-scroll survives stream growth (v0.8.4).
 * Uses jsdom if available; otherwise a minimal scroll container mock.
 */
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";
import assert from "assert";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const src = fs.readFileSync(path.join(__dirname, "bili-subbatch.user.js"), "utf8");

// Extract resolveAiScrollState from pure-logic region
const start = src.indexOf("// #region pure-logic");
const end = src.indexOf("// #endregion pure-logic");
const chunk = src.slice(src.indexOf("const MIXIN_KEY_ENC_TAB"), end);
const factory = `${chunk}
return { resolveAiScrollState, shouldStickBottom };
`;
const api = new Function(factory)();

/** Minimal scroll box mock */
function makeBox(clientHeight = 400) {
  let scrollTop = 0;
  let contentH = 100;
  const box = {
    clientHeight,
    get scrollHeight() {
      return contentH;
    },
    get scrollTop() {
      return scrollTop;
    },
    set scrollTop(v) {
      const max = Math.max(0, contentH - clientHeight);
      scrollTop = Math.max(0, Math.min(max, v));
    },
    grow(by) {
      contentH += by;
    },
    gap() {
      return contentH - scrollTop - clientHeight;
    },
  };
  return box;
}

function paint(box, st) {
  const r = api.resolveAiScrollState(st, { type: "paint" });
  if (r.allowPaintScroll) {
    box.scrollTop = box.scrollHeight; // follow
    return { ...st, stick: r.stick, userReading: r.userReading, painted: true };
  }
  // free: must NOT change scrollTop
  return { ...st, stick: r.stick, userReading: r.userReading, painted: false };
}

let st = { stick: true, userReading: false, progScroll: false };
const box = makeBox(400);
box.grow(500); // content 600

// Start streaming follow
st = paint(box, st);
assert.ok(box.gap() < 1, "follow should stick bottom");
const bottomAt = box.scrollTop;

// User wheels up → detach
st = api.resolveAiScrollState(st, { type: "wheel-up" });
assert.strictEqual(st.allowPaintScroll, false);
// simulate user scroll up 120px
box.scrollTop = bottomAt - 120;
const frozen = box.scrollTop;

// Stream grows a lot while user reading
for (let i = 0; i < 30; i++) {
  box.grow(40);
  st = paint(box, st);
  assert.strictEqual(
    box.scrollTop,
    frozen,
    `paint #${i} must freeze scrollTop (got ${box.scrollTop}, want ${frozen})`,
  );
  // Old bug: gap 40 would re-stick
  st = api.resolveAiScrollState(
    { stick: st.stick, userReading: st.userReading, progScroll: false },
    { type: "scroll", gap: box.gap() },
  );
  assert.strictEqual(
    st.allowPaintScroll,
    false,
    `growth scroll must not re-enable follow (gap=${box.gap()})`,
  );
}

// User scrolls to real bottom → resume
box.scrollTop = box.scrollHeight;
st = api.resolveAiScrollState(
  { stick: st.stick, userReading: st.userReading, progScroll: false },
  { type: "scroll", gap: box.gap() },
);
assert.strictEqual(st.allowPaintScroll, true, "user at bottom resumes");
st = paint(box, st);
assert.ok(box.gap() < 1, "resume follow sticks");

// Contrast: OLD buggy model would re-stick at gap<80 after tiny scroll
function oldBuggyModel(scrollHeight, scrollTop, clientHeight) {
  return scrollHeight - scrollTop - clientHeight < 80;
}
const midTop = box.scrollHeight - box.clientHeight - 40; // gap=40
assert.strictEqual(
  oldBuggyModel(box.scrollHeight, midTop, box.clientHeight),
  true,
  "sanity: old model would re-stick at gap 40",
);
const newAtGap40 = api.resolveAiScrollState(
  { stick: false, userReading: true, progScroll: false },
  { type: "scroll", gap: 40 },
);
assert.strictEqual(
  newAtGap40.allowPaintScroll,
  false,
  "new model must NOT re-stick at gap 40",
);

console.log("OK scroll sim: free-scroll freezes across 30 growth frames");
console.log("OK scroll sim: old gap<80 re-stick bug covered");
console.log("OK scroll sim: user-to-bottom resumes follow");
console.log("\n3 scroll simulation scenarios passed");
