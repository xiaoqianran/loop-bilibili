/** Offline check: WBI/MD5 parity with packages/bili_subbatch/wbi.py */

const MIXIN_KEY_ENC_TAB = [
  46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49, 33, 9,
  42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0,
  1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52,
];

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
  // length in bits as 64-bit little-endian
  words.push(bitLen >>> 0);
  words.push(Math.floor(bitLen / 0x100000000));

  let a0 = 0x67452301;
  let b0 = 0xefcdab89;
  let c0 = 0x98badcfe;
  let d0 = 0x10325476;

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
  const wRid = md5(query + mixinKey(imgKey, subKey));
  return `${query}&w_rid=${wRid}`;
}

// RFC vectors
const md5Empty = md5("");
const md5Abc = md5("abc");
console.log("md5('')", md5Empty, md5Empty === "d41d8cd98f00b204e9800998ecf8427e" ? "OK" : "FAIL");
console.log("md5('abc')", md5Abc, md5Abc === "900150983cd24fb0d6963f7d28e17f72" ? "OK" : "FAIL");

const img = "7cd084941338484aae1ad9425b84077c";
const sub = "4932caff0ff746eab6f01bf08b70ac45";
const mixin = mixinKey(img, sub);
console.log("mixin", mixin, mixin === "ea1db124af3c7062474693fa704f4ff8" ? "OK" : "FAIL");

const q = encWbi({ bvid: "BV1xx411c7mD", need_elec: 0 }, img, sub, 1700000000);
const expect =
  "bvid=BV1xx411c7mD&need_elec=0&wts=1700000000&w_rid=4b8506556389b2b4a7d71f2a4d2a2d58";
console.log("encWbi", q === expect ? "OK" : "FAIL");
if (q !== expect) {
  console.log(" got:", q);
  console.log("exp:", expect);
}

const q2 = encWbi({ aid: 123, cid: 456 }, "imgkey", "subkey", 100);
const expect2 = "aid=123&cid=456&wts=100&w_rid=a0f0763559b24900d726984fb4bdff9b";
console.log("encWbi short keys", q2 === expect2 ? "OK" : "FAIL");
if (q2 !== expect2) {
  console.log(" got:", q2);
  console.log("exp:", expect2);
}
