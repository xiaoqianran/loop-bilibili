#!/usr/bin/env python3
"""
Build a static subtitle browser site from v2 SQLite DBs.

Includes Mermaid learning graphs produced by the AI analyze worker.

Usage:
  python scripts/build_subtitle_site.py --out site
  python scripts/build_subtitle_site.py --db data/v2/haianyu.db --slug haianyu --title 海安雨
  python scripts/build_subtitle_site.py --from-dir data/v2   # auto-discover *.db

Site layout:
  site/index.html
  site/assets/{app.css,app.js}
  site/ups/<slug>/index.html
  site/data/<slug>/meta.json
  site/data/<slug>/videos.json
  site/data/<slug>/v/<bvid>.json
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@dataclass
class UpSource:
    slug: str
    title: str
    db_path: Path


def _slugify(name: str) -> str:
    s = re.sub(r"[^\w\u4e00-\u9fff\-]+", "-", name.strip(), flags=re.UNICODE)
    s = re.sub(r"-+", "-", s).strip("-").lower()
    return s or "up"


def discover_dbs(data_dir: Path) -> list[UpSource]:
    """Discover snapshot DBs for the static site.

    Includes ``loop.db`` (homepage preference feed) as slug ``loop``.
    Skips unit-test / scratch DBs only.
    """
    skip_names = {
        "single_test.db",
        "homepage_guest_test.db",
        "prefer_guest_test.db",
    }
    out: list[UpSource] = []
    for db in sorted(data_dir.glob("*.db")):
        if db.name in skip_names:
            continue
        if db.name.startswith("_") or "_test" in db.stem or db.stem.endswith("_test"):
            continue
        # Homepage runtime DB: mixed owners → fixed label
        if db.stem == "loop":
            out.append(UpSource(slug="loop", title="首页精选", db_path=db))
            continue
        slug = db.stem
        title = slug
        # try owner_name from DB
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            con.row_factory = sqlite3.Row
            row = con.execute(
                "SELECT owner_name, COUNT(*) AS c FROM videos "
                "WHERE owner_name != '' GROUP BY owner_name ORDER BY c DESC LIMIT 1"
            ).fetchone()
            if row and row["owner_name"]:
                title = str(row["owner_name"])
            con.close()
        except Exception:
            pass
        out.append(UpSource(slug=slug, title=title, db_path=db))
    return out


def _table_exists(con: sqlite3.Connection, name: str) -> bool:
    row = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()
    return bool(row)


def _parse_diagrams(raw: str | None) -> list[dict]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    out = []
    for item in data:
        if not isinstance(item, dict):
            continue
        code = str(item.get("code") or "").strip()
        if not code:
            continue
        out.append(
            {
                "title": str(item.get("title") or f"图 {len(out) + 1}"),
                "code": code,
            }
        )
    return out



# Preferred model order for UI (first = default display)
PREFERRED_MODELS = [
    "google/diffusiongemma-26b-a4b-it",
    "openai/gpt-oss-120b",
]


def model_label(model_id: str) -> str:
    mid = (model_id or "").strip()
    if not mid:
        return "unknown"
    last = mid.rsplit("/", 1)[-1]
    if "diffusiongemma" in last.lower():
        return "diffusiongemma"
    if "gpt-oss-120b" in last.lower():
        return "gpt-oss-120b"
    if "gpt-oss" in last.lower():
        return last
    return last


def order_models(found: list[str]) -> list[str]:
    """Stable order: preferred first, then remaining alphabetically."""
    seen: set[str] = set()
    out: list[str] = []
    for m in PREFERRED_MODELS:
        if m in found and m not in seen:
            out.append(m)
            seen.add(m)
    for m in sorted(found):
        if m not in seen:
            out.append(m)
            seen.add(m)
    return out


def load_videos(db_path: Path) -> list[dict]:
    """Load videos + per-model analyses. analyses keyed by model id."""
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    has_analyses = _table_exists(con, "analyses")
    rows = con.execute(
        """
        SELECT
          v.bvid,
          v.title,
          v.owner_mid,
          v.owner_name,
          v.published_at,
          COALESCE(s.status, 'missing') AS status,
          COALESCE(s.language, '') AS language,
          COALESCE(s.text, '') AS text,
          COALESCE(s.error, '') AS error,
          COALESCE(s.fetched_at, '') AS fetched_at
        FROM videos v
        LEFT JOIN subtitles s
          ON s.bvid = v.bvid
         AND s.rowid = (
           SELECT s2.rowid FROM subtitles s2
           WHERE s2.bvid = v.bvid
           ORDER BY
             CASE s2.status
               WHEN 'ok' THEN 0
               WHEN 'empty' THEN 1
               WHEN 'failed' THEN 2
               ELSE 3
             END,
             s2.rowid DESC
           LIMIT 1
         )
        ORDER BY v.published_at DESC, v.bvid DESC
        """
    ).fetchall()

    analyses_by_bvid: dict[str, dict[str, dict]] = {}
    all_models: set[str] = set()
    if has_analyses:
        # support both old (model column only) and new PK schemas
        arows = con.execute("SELECT * FROM analyses").fetchall()
        for ar in arows:
            bvid = str(ar["bvid"])
            model = str(ar["model"] or "").strip() or "legacy"
            diagrams = _parse_diagrams(ar["diagrams_json"] if "diagrams_json" in ar.keys() else "[]")
            status = str(ar["status"] or "")
            entry = {
                "model": model,
                "label": model_label(model),
                "status": status,
                "mode": str(ar["mode"] if "mode" in ar.keys() else "mermaid") or "mermaid",
                "markdown": str(ar["markdown"] or "") if status == "ok" else "",
                "diagrams": diagrams if status == "ok" else [],
                "diagram_count": len(diagrams) if status == "ok" else 0,
                "error": str(ar["error"] or ""),
                "updated_at": str(ar["updated_at"] or "") if "updated_at" in ar.keys() else "",
            }
            analyses_by_bvid.setdefault(bvid, {})[model] = entry
            all_models.add(model)
    con.close()

    models_ordered = order_models(list(all_models))
    default_model = models_ordered[0] if models_ordered else (
        PREFERRED_MODELS[0] if PREFERRED_MODELS else ""
    )

    videos = []
    for r in rows:
        text_body = r["text"] or ""
        bvid = r["bvid"]
        analyses = analyses_by_bvid.get(bvid, {})
        # pick default display analysis: preferred ok, else any ok, else any
        def pick() -> dict:
            for m in models_ordered:
                a = analyses.get(m)
                if a and a.get("status") == "ok" and a.get("diagram_count"):
                    return a
            for m in models_ordered:
                a = analyses.get(m)
                if a and a.get("status") == "ok":
                    return a
            for a in analyses.values():
                return a
            return {}

        primary = pick()
        # by_model counts for list badges
        model_counts = {
            mid: int((analyses.get(mid) or {}).get("diagram_count") or 0)
            for mid in models_ordered
            if mid in analyses
        }
        for mid, a in analyses.items():
            if mid not in model_counts:
                model_counts[mid] = int(a.get("diagram_count") or 0)

        videos.append(
            {
                "bvid": bvid,
                "title": r["title"] or bvid,
                "owner_mid": r["owner_mid"] or "",
                "owner_name": r["owner_name"] or "",
                "published_at": r["published_at"] or "",
                "status": r["status"] or "missing",
                "language": r["language"] or "",
                "chars": len(text_body),
                "preview": text_body[:160].replace("\n", " "),
                "text": text_body,
                "error": r["error"] or "",
                "fetched_at": r["fetched_at"] or "",
                "url": f"https://www.bilibili.com/video/{bvid}",
                # multi-model
                "analyses": analyses,
                "models": list(analyses.keys()),
                "model_counts": model_counts,
                # default/primary (backward compat + initial paint)
                "analysis_status": primary.get("status") or "",
                "analysis_mode": primary.get("mode") or "",
                "analysis_model": primary.get("model") or "",
                "analysis_markdown": primary.get("markdown") or "",
                "diagrams": primary.get("diagrams") or [],
                "diagram_count": int(primary.get("diagram_count") or 0),
                "analysis_error": primary.get("error") or "",
                "analysis_updated_at": primary.get("updated_at") or "",
                "default_model": default_model,
                "site_models": models_ordered,
            }
        )
    # stash site-level models on first video via sentinel? return tuple instead
    # attach as attribute on list for callers
    result = videos
    result_models = models_ordered  # noqa used by build_up via scan
    # put metadata on empty dict if no videos — build_up will rescan
    return videos




def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


CSS = """
:root {
  --bg: #0f1419;
  --panel: #1a2332;
  --text: #e7ecf3;
  --muted: #8b9bb4;
  --accent: #3d8bfd;
  --ok: #3dd68c;
  --empty: #f0b429;
  --bad: #f07178;
  --mermaid: #cba6f7;
  --border: #2a3548;
  --mono: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  --sans: "Segoe UI", system-ui, -apple-system, sans-serif;
}
* { box-sizing: border-box; }
html, body {
  margin: 0; padding: 0;
  background: var(--bg); color: var(--text);
  font-family: var(--sans); line-height: 1.5;
}
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
.wrap { max-width: 1100px; margin: 0 auto; padding: 1.25rem 1rem 3rem; }
header {
  display: flex; flex-wrap: wrap; gap: .75rem 1.25rem;
  align-items: baseline; justify-content: space-between;
  border-bottom: 1px solid var(--border); padding-bottom: 1rem; margin-bottom: 1.25rem;
}
header h1 { font-size: 1.35rem; margin: 0; font-weight: 650; }
header .meta { color: var(--muted); font-size: .9rem; }
.stats { display: flex; flex-wrap: wrap; gap: .5rem; margin: .75rem 0 1.25rem; }
.pill {
  background: var(--panel); border: 1px solid var(--border);
  border-radius: 999px; padding: .25rem .7rem; font-size: .85rem; color: var(--muted);
}
.pill b { color: var(--text); font-weight: 600; }
.pill.ok b { color: var(--ok); }
.pill.empty b { color: var(--empty); }
.pill.bad b { color: var(--bad); }
.pill.mermaid b { color: var(--mermaid); }
.search {
  width: 100%; padding: .7rem .9rem; border-radius: 10px;
  border: 1px solid var(--border); background: var(--panel); color: var(--text);
  font-size: 1rem; margin-bottom: 1rem;
}
.search:focus { outline: 2px solid color-mix(in srgb, var(--accent) 50%, transparent); border-color: var(--accent); }
.grid { display: grid; gap: .75rem; }
.card {
  background: var(--panel); border: 1px solid var(--border);
  border-radius: 12px; padding: .9rem 1rem;
}
.card h2, .card h3 { margin: 0 0 .35rem; font-size: 1.05rem; }
.card .sub { color: var(--muted); font-size: .85rem; font-family: var(--mono); }
.badge {
  display: inline-block; font-size: .75rem; padding: .1rem .45rem;
  border-radius: 6px; border: 1px solid var(--border); color: var(--muted);
  margin-right: .35rem; text-transform: uppercase;
}
.badge.ok { color: var(--ok); border-color: color-mix(in srgb, var(--ok) 40%, var(--border)); }
.badge.empty { color: var(--empty); border-color: color-mix(in srgb, var(--empty) 40%, var(--border)); }
.badge.missing, .badge.failed, .badge.retry { color: var(--bad); }
.badge.mermaid {
  color: var(--mermaid);
  border-color: color-mix(in srgb, var(--mermaid) 45%, var(--border));
  text-transform: none;
}
.preview { color: var(--muted); margin-top: .45rem; font-size: .92rem; }
.actions { margin-top: .55rem; display: flex; flex-wrap: wrap; gap: .6rem; font-size: .9rem; }
.video-body {
  white-space: pre-wrap; background: #0c1016; border: 1px solid var(--border);
  border-radius: 10px; padding: 1rem; font-size: .95rem; line-height: 1.65;
  max-height: 70vh; overflow: auto;
}
.section-title {
  margin: 1.5rem 0 .75rem; font-size: 1.05rem; font-weight: 650;
  color: var(--text); display: flex; align-items: center; gap: .5rem;
}
.section-title .badge { margin: 0; }
.mermaid-stack { display: grid; gap: 1rem; margin-bottom: 1.25rem; }
.mermaid-card {
  background: #121826;
  border: 1px solid color-mix(in srgb, var(--mermaid) 28%, var(--border));
  border-radius: 14px;
  overflow: hidden;
}
.mermaid-card header {
  margin: 0; padding: .7rem 1rem; border-bottom: 1px solid var(--border);
  display: flex; justify-content: space-between; gap: .75rem; align-items: center;
}
.mermaid-card header h3 {
  margin: 0; font-size: .98rem; font-weight: 600; color: var(--mermaid);
}
.mermaid-card .tools { display: flex; gap: .35rem; flex-wrap: wrap; }
.mermaid-card .tools button {
  background: #1b2434; color: var(--muted); border: 1px solid var(--border);
  border-radius: 8px; padding: .2rem .55rem; font-size: .8rem; cursor: pointer;
}
.mermaid-card .tools button:hover { color: var(--text); border-color: var(--accent); }
.mermaid-card .tools button:disabled { opacity: .5; cursor: wait; }
.mermaid-scale {
  min-width: 3.2rem; text-align: center; color: var(--muted);
  font-size: .78rem; font-family: var(--mono); align-self: center;
}
.mermaid-viewport {
  overflow: auto; max-height: 70vh; padding: 1rem;
  background:
    radial-gradient(circle at 20% 0%, color-mix(in srgb, var(--mermaid) 8%, transparent), transparent 45%),
    #0c1016;
}
.mermaid-stage {
  width: var(--mmd-width, 100%);
  min-width: 240px;
  margin: 0 auto;
  transform-origin: top left;
}
.mermaid-viewport .mermaid,
.mermaid-viewport svg {
  display: block; margin: 0 auto; max-width: none; height: auto;
}
.mermaid-error {
  color: var(--bad); font-size: .9rem; padding: .5rem 0;
  white-space: pre-wrap; font-family: var(--mono);
}
.mermaid-error-actions { margin-top: .5rem; display: flex; gap: .4rem; }

.model-bar {
  display: flex; flex-wrap: wrap; gap: .45rem; align-items: center;
  margin: 0 0 1rem; padding: .55rem .75rem;
  background: color-mix(in srgb, var(--mermaid) 8%, var(--panel));
  border: 1px solid color-mix(in srgb, var(--mermaid) 30%, var(--border));
  border-radius: 12px;
}
.model-bar .label { color: var(--muted); font-size: .85rem; margin-right: .25rem; }
.model-bar button {
  background: #1b2434; color: var(--muted); border: 1px solid var(--border);
  border-radius: 999px; padding: .3rem .75rem; font-size: .85rem; cursor: pointer;
}
.model-bar button.active {
  color: var(--text); border-color: var(--mermaid);
  background: color-mix(in srgb, var(--mermaid) 22%, #1b2434);
}
.model-bar button:hover { color: var(--text); }
.model-bar .hint { color: var(--muted); font-size: .78rem; margin-left: auto; }
.tabs { display: flex; gap: .4rem; margin: 0 0 1rem; flex-wrap: wrap; }
.tabs button {
  background: var(--panel); border: 1px solid var(--border); color: var(--muted);
  border-radius: 999px; padding: .35rem .85rem; cursor: pointer; font-size: .9rem;
}
.tabs button.active {
  color: var(--text); border-color: var(--accent);
  background: color-mix(in srgb, var(--accent) 18%, var(--panel));
}
.nav { margin-bottom: 1rem; color: var(--muted); font-size: .9rem; }
.hidden { display: none !important; }
footer { margin-top: 2rem; color: var(--muted); font-size: .8rem; border-top: 1px solid var(--border); padding-top: .8rem; }
@media (max-width: 640px) {
  .wrap { padding: 1rem .75rem 2rem; }
  .mermaid-viewport { max-height: 55vh; padding: .75rem; }
}
"""

# Paths are absolute from SITE_BASE (e.g. /loop-bilibili) so nested pages never break.
JS = r"""
const SITE_BASE = (window.SITE_BASE || '').replace(/\/$/, '');
const ASSET_V = window.SITE_ASSET_V || '1';
let mermaidReady = null;

const MODEL_STORAGE_KEY = 'bsb_preferred_model';

function shortModelLabel(id) {
  if (!id) return 'unknown';
  const last = String(id).split('/').pop();
  if (/diffusiongemma/i.test(last)) return 'diffusiongemma';
  if (/gpt-oss-120b/i.test(last)) return 'gpt-oss-120b';
  return last;
}

function getGlobalModel(fallback) {
  try {
    const v = localStorage.getItem(MODEL_STORAGE_KEY);
    if (v) return v;
  } catch (_) {}
  return fallback || '';
}

function setGlobalModel(id) {
  try { localStorage.setItem(MODEL_STORAGE_KEY, id); } catch (_) {}
}

function pickAnalysis(data, preferred) {
  const analyses = data.analyses || {};
  const keys = Object.keys(analyses);
  if (!keys.length) {
    // legacy single
    if ((data.diagrams || []).length) {
      return {
        model: data.analysis_model || '',
        status: data.analysis_status || 'ok',
        diagrams: data.diagrams || [],
        diagram_count: (data.diagrams || []).length,
        error: data.analysis_error || '',
        markdown: data.analysis_markdown || '',
      };
    }
    return null;
  }
  const order = (data.site_models || data.models || keys).slice();
  const tryIds = [];
  if (preferred) tryIds.push(preferred);
  if (data.default_model) tryIds.push(data.default_model);
  for (const m of order) tryIds.push(m);
  for (const m of keys) tryIds.push(m);
  const seen = new Set();
  for (const m of tryIds) {
    if (!m || seen.has(m)) continue;
    seen.add(m);
    const a = analyses[m];
    if (a && a.status === 'ok' && (a.diagram_count || (a.diagrams || []).length)) return a;
  }
  for (const m of tryIds) {
    if (!m || !analyses[m]) continue;
    return analyses[m];
  }
  return null;
}

function buildModelBar(models, active, onPick, { hint } = {}) {
  const bar = el('div', { className: 'model-bar' });
  bar.appendChild(el('span', { className: 'label', text: '模型' }));
  if (!models || !models.length) {
    bar.appendChild(el('span', { className: 'hint', text: '暂无分析' }));
    return bar;
  }
  for (const mid of models) {
    const btn = el('button', {
      type: 'button',
      className: mid === active ? 'active' : '',
      text: shortModelLabel(mid),
      title: mid,
    });
    btn.addEventListener('click', () => onPick(mid));
    bar.appendChild(btn);
  }
  if (hint) bar.appendChild(el('span', { className: 'hint', text: hint }));
  return bar;
}


function url(path) {
  if (!path) return SITE_BASE + '/';
  if (path.startsWith('http://') || path.startsWith('https://')) return path;
  return SITE_BASE + (path.startsWith('/') ? path : '/' + path);
}

async function loadJSON(path) {
  const full = url(path) + (path.includes('?') ? '&' : '?') + 'v=' + encodeURIComponent(ASSET_V);
  const r = await fetch(full, { cache: 'no-cache' });
  if (!r.ok) throw new Error('load failed: ' + full + ' (' + r.status + ')');
  return r.json();
}

function el(tag, attrs = {}, children = []) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === 'className') n.className = v;
    else if (k === 'text') n.textContent = v;
    else if (k === 'href') n.setAttribute('href', url(v));
    else if (k.startsWith('on') && typeof v === 'function') n.addEventListener(k.slice(2).toLowerCase(), v);
    else n.setAttribute(k, v);
  }
  for (const c of [].concat(children)) {
    if (c == null) continue;
    n.appendChild(typeof c === 'string' ? document.createTextNode(c) : c);
  }
  return n;
}

function badge(status) {
  return el('span', { className: 'badge ' + (status || 'missing'), text: status || 'missing' });
}

function mermaidBadge(count) {
  if (!count) return null;
  return el('span', { className: 'badge mermaid', text: 'mermaid ×' + count });
}

function norm(s) { return (s || '').toLowerCase(); }

function filterList(items, q) {
  q = norm(q).trim();
  if (!q) return items;
  const parts = q.split(/\s+/).filter(Boolean);
  return items.filter(it => {
    const hay = norm([it.bvid, it.title, it.preview, it.owner_name, it.status, it.analysis_status, it.analysis_model].join(' '));
    return parts.every(p => hay.includes(p));
  });
}

function ensureMermaid() {
  if (mermaidReady) return mermaidReady;
  mermaidReady = new Promise((resolve, reject) => {
    if (window.mermaid) {
      window.mermaid.initialize({
        startOnLoad: false,
        theme: 'dark',
        securityLevel: 'strict',
        fontFamily: 'Segoe UI, system-ui, sans-serif',
        flowchart: { htmlLabels: false, useMaxWidth: false, curve: 'basis' },
      });
      resolve(window.mermaid);
      return;
    }
    const s = document.createElement('script');
    s.src = 'https://cdn.jsdelivr.net/npm/mermaid@10.9.1/dist/mermaid.min.js';
    s.async = true;
    s.onload = () => {
      try {
        window.mermaid.initialize({
          startOnLoad: false,
          theme: 'dark',
          securityLevel: 'strict',
          fontFamily: 'Segoe UI, system-ui, sans-serif',
          flowchart: { htmlLabels: false, useMaxWidth: false, curve: 'basis' },
        });
        resolve(window.mermaid);
      } catch (e) { reject(e); }
    };
    s.onerror = () => reject(new Error('failed to load mermaid'));
    document.head.appendChild(s);
  });
  return mermaidReady;
}

function parseViewBox(svg) {
  const raw = String(svg?.getAttribute('viewBox') || '').trim().split(/[\s,]+/).map(Number);
  if (raw.length === 4 && raw.every(Number.isFinite) && raw[2] > 0 && raw[3] > 0) {
    return { width: raw[2], height: raw[3] };
  }
  return {
    width: Number.parseFloat(svg?.getAttribute('width')) || 760,
    height: Number.parseFloat(svg?.getAttribute('height')) || 540,
  };
}

function setCardScale(card, scale) {
  const stage = card.querySelector('.mermaid-stage');
  const label = card.querySelector('.mermaid-scale');
  if (!stage) return;
  const base = Number(stage.dataset.baseWidth) || 760;
  const next = Math.max(0.35, Math.min(3, Number(scale) || 1));
  card.dataset.scale = String(next);
  stage.style.setProperty('--mmd-width', Math.max(240, Math.round(base * next)) + 'px');
  if (label) label.textContent = Math.round(next * 100) + '%';
}

function fitCard(card) {
  const viewport = card.querySelector('.mermaid-viewport');
  const stage = card.querySelector('.mermaid-stage');
  if (!viewport || !stage) return;
  const base = Number(stage.dataset.baseWidth) || 760;
  const available = Math.max(240, viewport.clientWidth - 36);
  setCardScale(card, Math.min(1.5, available / base));
  viewport.scrollLeft = 0;
  viewport.scrollTop = 0;
}

async function paintMermaidInto(viewport, code, idx, { showRetry = true } = {}) {
  viewport.innerHTML = '';
  try {
    const m = await ensureMermaid();
    const id = 'mmd-' + Date.now() + '-' + idx + '-' + Math.random().toString(36).slice(2, 7);
    const { svg } = await m.render(id, String(code || ''));
    const wrap = document.createElement('div');
    wrap.innerHTML = svg;
    const svgNode = wrap.querySelector('svg');
    if (!svgNode) throw new Error('Mermaid 未返回 SVG');
    svgNode.removeAttribute('width');
    svgNode.removeAttribute('height');
    svgNode.setAttribute('preserveAspectRatio', 'xMinYMin meet');
    const vb = parseViewBox(svgNode);
    const baseWidth = Math.round(Math.max(480, Math.min(3600, vb.width)));
    const stage = document.createElement('div');
    stage.className = 'mermaid-stage';
    stage.dataset.baseWidth = String(baseWidth);
    stage.style.setProperty('--mmd-width', baseWidth + 'px');
    stage.appendChild(svgNode);
    viewport.appendChild(stage);
    return { ok: true, baseWidth };
  } catch (e) {
    const box = el('div', { className: 'mermaid-error' });
    box.appendChild(document.createTextNode(
      '渲染失败: ' + (e && e.message ? e.message : String(e)) + '\n\n' + (code || '')
    ));
    if (showRetry) {
      const actions = el('div', { className: 'mermaid-error-actions' });
      const retry = el('button', { type: 'button', text: '重绘' });
      retry.addEventListener('click', () => {
        const card = viewport.closest('.mermaid-card');
        if (card && typeof card._bsbRetry === 'function') card._bsbRetry();
      });
      actions.appendChild(retry);
      box.appendChild(actions);
    }
    viewport.appendChild(box);
    return { ok: false, error: e };
  }
}

async function renderDiagramCard(diagram, idx) {
  const card = el('section', { className: 'mermaid-card' });
  card.dataset.scale = '1';
  const head = el('header', {}, [
    el('h3', { text: diagram.title || ('图 ' + (idx + 1)) }),
  ]);
  const tools = el('div', { className: 'tools' });
  const scaleLabel = el('span', { className: 'mermaid-scale', text: '100%' });

  const makeBtn = (text, title, onClick) => {
    const b = el('button', { type: 'button', text, title: title || text });
    b.addEventListener('click', onClick);
    return b;
  };

  const retryBtn = makeBtn('重绘', '本地重新渲染（不调用 AI）', async () => {
    retryBtn.disabled = true;
    retryBtn.textContent = '重绘中…';
    try {
      await card._bsbRetry();
    } finally {
      retryBtn.disabled = false;
      retryBtn.textContent = '重绘';
    }
  });

  tools.append(
    makeBtn('适宽', '适应可视区域宽度', () => fitCard(card)),
    makeBtn('100%', '原始尺寸', () => setCardScale(card, 1)),
    makeBtn('−', '缩小', () => setCardScale(card, (Number(card.dataset.scale) || 1) - 0.15)),
    scaleLabel,
    makeBtn('+', '放大', () => setCardScale(card, (Number(card.dataset.scale) || 1) + 0.15)),
    retryBtn,
    makeBtn('复制源码', '复制 Mermaid 源码', async () => {
      const btn = tools.querySelector('[data-act=copy]') || null;
      try {
        await navigator.clipboard.writeText(diagram.code || '');
        const b = Array.from(tools.querySelectorAll('button')).find(x => x.textContent.startsWith('复制'));
        if (b) { b.textContent = '已复制'; setTimeout(() => { b.textContent = '复制源码'; }, 1200); }
      } catch (_) { /* ignore */ }
    }),
  );
  head.appendChild(tools);
  card.appendChild(head);

  const viewport = el('div', { className: 'mermaid-viewport' });
  // Ctrl + wheel zoom
  viewport.addEventListener('wheel', (e) => {
    if (!e.ctrlKey) return;
    e.preventDefault();
    const cur = Number(card.dataset.scale) || 1;
    setCardScale(card, cur + (e.deltaY > 0 ? -0.1 : 0.1));
  }, { passive: false });
  card.appendChild(viewport);

  card._bsbRetry = async () => {
    const result = await paintMermaidInto(viewport, diagram.code, idx);
    if (result.ok) {
      // keep current zoom intent: fit if was near fit, else keep scale
      const scale = Number(card.dataset.scale) || 1;
      setCardScale(card, scale);
    }
    return result;
  };

  await card._bsbRetry();
  // default: fit width for readability
  requestAnimationFrame(() => fitCard(card));
  return card;
}

async function renderHome() {
  const catalog = await loadJSON('/data/catalog.json');
  const root = document.getElementById('app');
  root.innerHTML = '';
  root.appendChild(el('header', {}, [
    el('h1', { text: 'loop-bilibili 字幕浏览' }),
    el('div', { className: 'meta', text: '构建于 ' + (catalog.built_at || '') }),
  ]));
  root.appendChild(el('p', { className: 'meta', text: '从 v2 SQLite 快照生成的静态站 · 字幕 + Mermaid 学习图谱' }));
  const grid = el('div', { className: 'grid' });
  for (const up of catalog.ups || []) {
    const card = el('div', { className: 'card' });
    card.appendChild(el('h2', {}, [el('a', { href: '/ups/' + up.slug + '/', text: up.title })]));
    card.appendChild(el('div', { className: 'sub', text: up.slug + (up.owner_mid ? ' · mid ' + up.owner_mid : '') }));
    card.appendChild(el('div', { className: 'stats' }, [
      el('span', { className: 'pill' }, [document.createTextNode('视频 '), el('b', { text: String(up.videos) })]),
      el('span', { className: 'pill ok' }, [document.createTextNode('ok '), el('b', { text: String(up.ok) })]),
      el('span', { className: 'pill mermaid' }, [document.createTextNode('mermaid '), el('b', { text: String(up.mermaid || 0) })]),
      el('span', { className: 'pill empty' }, [document.createTextNode('empty '), el('b', { text: String(up.empty) })]),
      el('span', { className: 'pill bad' }, [document.createTextNode('other '), el('b', { text: String(up.other) })]),
    ]));
    card.appendChild(el('div', { className: 'actions' }, [
      el('a', { href: '/ups/' + up.slug + '/', text: '进入列表 →' }),
    ]));
    grid.appendChild(card);
  }
  root.appendChild(grid);
  root.appendChild(el('footer', { text: 'loop-bilibili v2 · GitHub Pages · base=' + (SITE_BASE || '/') }));
}

async function renderUpList(slug) {
  const [meta, videos] = await Promise.all([
    loadJSON('/data/' + slug + '/meta.json'),
    loadJSON('/data/' + slug + '/videos.json'),
  ]);
  const root = document.getElementById('app');
  root.innerHTML = '';
  root.appendChild(el('div', { className: 'nav' }, [
    el('a', { href: '/', text: '← 全部 UP' }),
  ]));
  root.appendChild(el('header', {}, [
    el('h1', { text: meta.title || slug }),
    el('div', { className: 'meta', text: (meta.videos || 0) + ' 条 · ' + (meta.source_db || '') }),
  ]));
  root.appendChild(el('div', { className: 'stats' }, [
    el('span', { className: 'pill' }, [document.createTextNode('ok '), el('b', { text: String(meta.ok || 0) })]),
    el('span', { className: 'pill mermaid' }, [document.createTextNode('mermaid '), el('b', { text: String(meta.mermaid || 0) })]),
    el('span', { className: 'pill' }, [document.createTextNode('empty '), el('b', { text: String(meta.empty || 0) })]),
  ]));

  const models = (meta.models && meta.models.length) ? meta.models.slice() : [];
  for (const v of videos) {
    const mc = v.model_counts || {};
    for (const k of Object.keys(mc)) {
      if (!models.includes(k)) models.push(k);
    }
  }
  let activeModel = getGlobalModel(meta.default_model || models[0] || '');
  if (models.length && !models.includes(activeModel)) activeModel = models[0] || '';

  const search = el('input', {
    type: 'search',
    placeholder: '搜索标题 / BV / 状态…',
    style: 'width:100%;margin:0 0 .75rem;padding:.55rem .75rem;border-radius:10px;border:1px solid var(--border);background:var(--panel);color:var(--text)',
  });
  const barHost = el('div');
  const listHost = el('div');

  function diagramCountFor(v) {
    const mc = v.model_counts || {};
    if (activeModel && Object.prototype.hasOwnProperty.call(mc, activeModel)) {
      return Number(mc[activeModel]) || 0;
    }
    return Number(v.diagram_count) || 0;
  }

  function paintList() {
    listHost.innerHTML = '';
    const items = filterList(videos, search.value || '');
    if (!items.length) {
      listHost.appendChild(el('div', { className: 'card', text: '没有匹配的视频' }));
      return;
    }
    for (const v of items) {
      const count = diagramCountFor(v);
      const badges = [badge(v.status)];
      if (count) badges.push(mermaidBadge(count));
      if (activeModel) {
        badges.push(el('span', {
          className: 'badge',
          text: shortModelLabel(activeModel),
          style: 'margin-left:.25rem',
        }));
      }
      listHost.appendChild(el('a', {
        className: 'card',
        href: '/ups/' + slug + '/v/' + v.bvid + '.html',
        style: 'display:block;margin:0 0 .65rem',
      }, [
        el('div', { style: 'display:flex;justify-content:space-between;gap:.75rem;flex-wrap:wrap' }, [
          el('strong', { text: v.title || v.bvid }),
          el('span', {}, badges),
        ]),
        el('div', {
          className: 'meta',
          text: v.bvid + (v.chars ? ' · ' + v.chars + ' 字' : '') + (v.preview ? ' · ' + v.preview : ''),
        }),
      ]));
    }
  }

  function paintBar() {
    barHost.innerHTML = '';
    if (!models.length) return;
    barHost.appendChild(buildModelBar(models, activeModel, (mid) => {
      activeModel = mid;
      setGlobalModel(mid);
      paintBar();
      paintList();
    }, { hint: '批量切换 · 列表与详情默认模型' }));
  }

  root.appendChild(barHost);
  root.appendChild(search);
  root.appendChild(listHost);
  search.addEventListener('input', paintList);
  paintBar();
  paintList();
}


async function renderVideo(slug, bvid) {
  const data = await loadJSON('/data/' + slug + '/v/' + bvid + '.json');
  const root = document.getElementById('app');
  root.innerHTML = '';
  root.appendChild(el('div', { className: 'nav' }, [
    el('a', { href: '/ups/' + slug + '/', text: '← 返回列表' }),
    document.createTextNode(' · '),
    el('a', { href: '/', text: '全部 UP' }),
  ]));

  const analyses = data.analyses || {};
  const modelIds = (data.site_models && data.site_models.length)
    ? data.site_models.slice()
    : Object.keys(analyses);
  // ensure all present keys included
  for (const k of Object.keys(analyses)) {
    if (!modelIds.includes(k)) modelIds.push(k);
  }
  const preferred = getGlobalModel(data.default_model || modelIds[0] || '');
  let activeModel = preferred;
  if (activeModel && !analyses[activeModel] && modelIds.length) {
    // keep preferred for bar; analysis may be missing
  }

  function currentAnalysis() {
    return pickAnalysis(data, activeModel);
  }

  const headerMeta = el('div', { className: 'meta' });
  root.appendChild(el('header', {}, [
    el('h1', { text: data.title || bvid }),
    headerMeta,
  ]));
  root.appendChild(el('div', { className: 'actions', style: 'margin-bottom:1rem' }, [
    el('a', { href: data.url, target: '_blank', rel: 'noopener', text: '打开 B 站视频' }),
  ]));

  const hasText = data.status === 'ok' && data.text;
  const tabs = el('div', { className: 'tabs' });
  const tabMermaid = el('button', { type: 'button', text: 'Mermaid 图谱' });
  const tabSub = el('button', { type: 'button', text: '字幕正文' });
  const panelMermaid = el('div', { id: 'panel-mermaid' });
  const panelSub = el('div', { id: 'panel-sub', className: 'hidden' });

  async function paintMermaidPanel() {
    panelMermaid.innerHTML = '';
    const a = currentAnalysis();
    const diagrams = (a && a.diagrams) ? a.diagrams : [];
    const hasDiagrams = diagrams.length > 0;

    if (modelIds.length) {
      panelMermaid.appendChild(buildModelBar(modelIds, activeModel, async (mid) => {
        activeModel = mid;
        setGlobalModel(mid);
        await paintMermaidPanel();
        refreshChrome();
      }, { hint: '单视频切换 · 也会设为列表默认' }));
    }

    if (hasDiagrams) {
      panelMermaid.appendChild(el('div', { className: 'section-title' }, [
        document.createTextNode('学习图谱'),
        el('span', { className: 'badge mermaid', text: shortModelLabel(a.model || activeModel) }),
        el('span', { className: 'badge', text: (a.model || activeModel || ''), style: 'font-size:.72rem;opacity:.75' }),
      ]));
      const stack = el('div', { className: 'mermaid-stack' });
      panelMermaid.appendChild(stack);
      for (let i = 0; i < diagrams.length; i++) {
        stack.appendChild(await renderDiagramCard(diagrams[i], i));
      }
    } else if (a && a.status === 'failed') {
      panelMermaid.appendChild(el('div', {
        className: 'card',
        text: 'Mermaid 生成失败（' + shortModelLabel(a.model || activeModel) + '）：' + (a.error || '未知错误'),
      }));
    } else if (modelIds.length) {
      panelMermaid.appendChild(el('div', {
        className: 'card',
        text: '当前模型「' + shortModelLabel(activeModel) + '」还没有图谱。可切换其它模型，或等待 analyze 任务完成。',
      }));
    } else {
      panelMermaid.appendChild(el('div', { className: 'card', text: '尚无 Mermaid 分析结果' }));
    }

    tabMermaid.textContent = 'Mermaid 图谱' + (diagrams.length ? ' (' + diagrams.length + ')' : '');
  }

  function refreshChrome() {
    const a = currentAnalysis();
    const diagrams = (a && a.diagrams) ? a.diagrams : [];
    headerMeta.innerHTML = '';
    headerMeta.appendChild(badge(data.status));
    if (diagrams.length) headerMeta.appendChild(mermaidBadge(diagrams.length));
    headerMeta.appendChild(document.createTextNode(
      ' ' + bvid + (data.chars ? ' · ' + data.chars + ' 字' : '')
      + (a && a.model ? ' · ' + shortModelLabel(a.model) : '')
    ));
  }

  if (hasText) {
    panelSub.appendChild(el('div', { className: 'section-title', text: '字幕正文' }));
    panelSub.appendChild(el('div', { className: 'video-body', text: data.text }));
  }

  // initial: prefer mermaid if any model has diagrams
  const anyDiagrams = modelIds.some(m => {
    const a = analyses[m];
    return a && a.status === 'ok' && (a.diagram_count || (a.diagrams||[]).length);
  }) || ((data.diagrams || []).length > 0);

  if (anyDiagrams || modelIds.length) {
    tabs.appendChild(tabMermaid);
    tabMermaid.classList.add('active');
  }
  if (hasText) {
    tabs.appendChild(tabSub);
    if (!anyDiagrams && !modelIds.length) {
      tabSub.classList.add('active');
      panelSub.classList.remove('hidden');
      panelMermaid.classList.add('hidden');
    }
  }
  if (tabs.childNodes.length) root.appendChild(tabs);
  root.appendChild(panelMermaid);
  if (hasText) root.appendChild(panelSub);

  if (hasText && (anyDiagrams || modelIds.length)) {
    tabMermaid.addEventListener('click', () => {
      tabMermaid.classList.add('active');
      tabSub.classList.remove('active');
      panelMermaid.classList.remove('hidden');
      panelSub.classList.add('hidden');
    });
    tabSub.addEventListener('click', () => {
      tabSub.classList.add('active');
      tabMermaid.classList.remove('active');
      panelSub.classList.remove('hidden');
      panelMermaid.classList.add('hidden');
    });
  }

  await paintMermaidPanel();
  refreshChrome();

  if (!anyDiagrams && !hasText) {
    root.appendChild(el('div', { className: 'card', text: data.error || ('状态: ' + data.status + '（无字幕正文）') }));
  }
}



window.SubtitleSite = { renderHome, renderUpList, renderVideo, url, SITE_BASE };
"""


def _page_shell(
    *,
    title: str,
    body_script: str,
    base: str,
    asset_v: str,
) -> str:
    """HTML shell with absolute asset URLs + SITE_BASE injection."""
    base = (base or "").rstrip("/")
    safe = title.replace("&", "&").replace("<", "<").replace(">", ">")
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <meta name="site-base" content="{base}" />
  <title>{safe}</title>
  <link rel="stylesheet" href="{base}/assets/app.css?v={asset_v}" />
</head>
<body>
  <div class="wrap" id="app">加载中…</div>
  <script>
    window.SITE_BASE = {json.dumps(base)};
    window.SITE_ASSET_V = {json.dumps(asset_v)};
  </script>
  <script src="{base}/assets/app.js?v={asset_v}"></script>
  <script>
    {body_script}
  </script>
</body>
</html>
"""


def video_html(slug: str, bvid: str, title: str, *, base: str, asset_v: str) -> str:
    script = f"""
    SubtitleSite.renderVideo({json.dumps(slug)}, {json.dumps(bvid)}).catch(e => {{
      document.getElementById('app').textContent = String(e);
    }});
    """
    return _page_shell(
        title=f"{title} · {bvid}",
        body_script=script,
        base=base,
        asset_v=asset_v,
    )


def up_index_html(slug: str, title: str, *, base: str, asset_v: str) -> str:
    script = f"""
    SubtitleSite.renderUpList({json.dumps(slug)}).catch(e => {{
      document.getElementById('app').textContent = String(e);
    }});
    """
    return _page_shell(
        title=f"{title} · 字幕列表",
        body_script=script,
        base=base,
        asset_v=asset_v,
    )


def home_html(*, base: str, asset_v: str) -> str:
    script = """
    SubtitleSite.renderHome().catch(e => {
      document.getElementById('app').textContent = String(e);
    });
    """
    return _page_shell(
        title="loop-bilibili 字幕浏览",
        body_script=script,
        base=base,
        asset_v=asset_v,
    )


def build_up(out: Path, src: UpSource, *, base: str, asset_v: str) -> dict:
    videos = load_videos(src.db_path)
    ok = sum(1 for v in videos if v["status"] == "ok")
    empty = sum(1 for v in videos if v["status"] == "empty")
    other = len(videos) - ok - empty
    mermaid_n = sum(1 for v in videos if v.get("diagram_count"))
    total_chars = sum(v["chars"] for v in videos if v["status"] == "ok")
    owner_mid = ""
    for v in videos:
        if v["owner_mid"]:
            owner_mid = v["owner_mid"]
            break

    data_dir = out / "data" / src.slug
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "v").mkdir(exist_ok=True)

    list_items = []
    for v in videos:
        # per-video JSON (full text + mermaid diagrams)
        write_json(
            data_dir / "v" / f"{v['bvid']}.json",
            {
                "bvid": v["bvid"],
                "title": v["title"],
                "status": v["status"],
                "language": v["language"],
                "chars": v["chars"],
                "text": v["text"] if v["status"] == "ok" else "",
                "error": v["error"],
                "url": v["url"],
                "published_at": v["published_at"],
                "fetched_at": v["fetched_at"],
                "default_model": v.get("default_model") or "",
                "site_models": v.get("site_models") or [],
                "analyses": v.get("analyses") or {},
                "model_counts": v.get("model_counts") or {},
                # backward-compat primary (default model prefer)
                "analysis_status": v["analysis_status"],
                "analysis_mode": v["analysis_mode"],
                "analysis_model": v["analysis_model"],
                "analysis_markdown": v["analysis_markdown"]
                if v["analysis_status"] == "ok"
                else "",
                "diagrams": v["diagrams"] if v["analysis_status"] == "ok" else [],
                "diagram_count": v["diagram_count"] if v["analysis_status"] == "ok" else 0,
                "analysis_error": v["analysis_error"],
                "analysis_updated_at": v["analysis_updated_at"],
            },
        )
        list_items.append(
            {
                "bvid": v["bvid"],
                "title": v["title"],
                "status": v["status"],
                "chars": v["chars"],
                "preview": v["preview"] if v["status"] == "ok" else "",
                "published_at": v["published_at"],
                "url": v["url"],
                "analysis_status": v["analysis_status"],
                "diagram_count": v["diagram_count"] if v["analysis_status"] == "ok" else 0,
                "model_counts": v.get("model_counts") or {},
                "analysis_model": v.get("analysis_model") or "",
            }
        )

    # collect models across videos
    site_models: list[str] = []
    seen_m: set[str] = set()
    for v in videos:
        for m in v.get("site_models") or []:
            if m not in seen_m:
                site_models.append(m)
                seen_m.add(m)
        for m in (v.get("analyses") or {}):
            if m not in seen_m:
                site_models.append(m)
                seen_m.add(m)
    site_models = order_models(site_models)
    default_model = site_models[0] if site_models else (
        PREFERRED_MODELS[0] if PREFERRED_MODELS else ""
    )
    meta = {
        "slug": src.slug,
        "title": src.title,
        "owner_mid": owner_mid,
        "videos": len(videos),
        "ok": ok,
        "empty": empty,
        "other": other,
        "mermaid": mermaid_n,
        "total_chars": total_chars,
        "source_db": src.db_path.name,
        "models": site_models,
        "default_model": default_model,
        "model_labels": {m: model_label(m) for m in site_models},
    }
    write_json(data_dir / "meta.json", meta)
    write_json(data_dir / "videos.json", list_items)

    up_dir = out / "ups" / src.slug
    up_dir.mkdir(parents=True, exist_ok=True)
    (up_dir / "index.html").write_text(
        up_index_html(src.slug, src.title, base=base, asset_v=asset_v),
        encoding="utf-8",
    )
    v_dir = up_dir / "v"
    v_dir.mkdir(exist_ok=True)
    for v in videos:
        (v_dir / f"{v['bvid']}.html").write_text(
            video_html(
                src.slug, v["bvid"], v["title"], base=base, asset_v=asset_v
            ),
            encoding="utf-8",
        )

    print(
        f"  {src.slug}: videos={len(videos)} ok={ok} mermaid={mermaid_n} "
        f"empty={empty} other={other}",
        flush=True,
    )
    return meta


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Build static subtitle site from v2 DBs")
    p.add_argument("--out", default="site", help="output directory (default: site)")
    p.add_argument("--from-dir", default=None, help="auto-discover *.db under this dir")
    p.add_argument("--db", action="append", default=[], help="db path (repeatable)")
    p.add_argument("--slug", action="append", default=[], help="slug for each --db")
    p.add_argument("--title", action="append", default=[], help="title for each --db")
    p.add_argument(
        "--base-url",
        default="/loop-bilibili",
        help="site base path for project Pages (default: /loop-bilibili; use '' for local root)",
    )
    args = p.parse_args(argv)

    sources: list[UpSource] = []
    if args.from_dir:
        sources.extend(discover_dbs(Path(args.from_dir)))
    if args.db:
        for i, db in enumerate(args.db):
            path = Path(db)
            slug = args.slug[i] if i < len(args.slug) else path.stem
            title = args.title[i] if i < len(args.title) else slug
            sources.append(UpSource(slug=_slugify(slug), title=title, db_path=path))
    if not sources:
        # default: local data/v2
        default = ROOT / "data" / "v2"
        if default.is_dir():
            sources = discover_dbs(default)
    if not sources:
        print("error: no databases found; pass --from-dir or --db", file=sys.stderr)
        return 2

    base = (args.base_url or "").rstrip("/")
    asset_v = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")

    out = Path(args.out)
    if out.exists():
        # clean only our generated tree content carefully
        import shutil

        shutil.rmtree(out)
    out.mkdir(parents=True)

    (out / "assets").mkdir()
    (out / "assets" / "app.css").write_text(CSS, encoding="utf-8")
    (out / "assets" / "app.js").write_text(JS, encoding="utf-8")
    (out / "index.html").write_text(
        home_html(base=base, asset_v=asset_v), encoding="utf-8"
    )

    ups_meta = []
    for src in sources:
        if not src.db_path.is_file():
            print(f"skip missing db: {src.db_path}", file=sys.stderr)
            continue
        print(f"building {src.slug} from {src.db_path}", flush=True)
        meta = build_up(out, src, base=base, asset_v=asset_v)
        ups_meta.append(meta)

    catalog = {
        "built_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "ups": ups_meta,
        "base_url": base,
        "asset_v": asset_v,
    }
    write_json(out / "data" / "catalog.json", catalog)
    # nojekyll for GH pages
    (out / ".nojekyll").write_text("", encoding="utf-8")
    print(
        f"done -> {out.resolve()} ({len(ups_meta)} ups) base={base or '/'} v={asset_v}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
