#!/usr/bin/env python3
"""
Build a static subtitle browser site from v2 SQLite DBs.

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
    out: list[UpSource] = []
    for db in sorted(data_dir.glob("*.db")):
        if db.name in ("single_test.db", "loop.db"):
            continue
        if db.name.startswith("_"):
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


def load_videos(db_path: Path) -> list[dict]:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
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
    con.close()
    videos = []
    for r in rows:
        text = r["text"] or ""
        videos.append(
            {
                "bvid": r["bvid"],
                "title": r["title"] or r["bvid"],
                "owner_mid": r["owner_mid"] or "",
                "owner_name": r["owner_name"] or "",
                "published_at": r["published_at"] or "",
                "status": r["status"] or "missing",
                "language": r["language"] or "",
                "chars": len(text),
                "preview": text[:160].replace("\n", " "),
                "text": text,
                "error": r["error"] or "",
                "fetched_at": r["fetched_at"] or "",
                "url": f"https://www.bilibili.com/video/{r['bvid']}",
            }
        )
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
.preview { color: var(--muted); margin-top: .45rem; font-size: .92rem; }
.actions { margin-top: .55rem; display: flex; flex-wrap: wrap; gap: .6rem; font-size: .9rem; }
.video-body {
  white-space: pre-wrap; background: #0c1016; border: 1px solid var(--border);
  border-radius: 10px; padding: 1rem; font-size: .95rem; line-height: 1.65;
  max-height: 70vh; overflow: auto;
}
.nav { margin-bottom: 1rem; color: var(--muted); font-size: .9rem; }
.hidden { display: none !important; }
footer { margin-top: 2rem; color: var(--muted); font-size: .8rem; border-top: 1px solid var(--border); padding-top: .8rem; }
"""

JS = r"""
async function loadJSON(path) {
  const r = await fetch(path);
  if (!r.ok) throw new Error('load failed: ' + path);
  return r.json();
}

function el(tag, attrs = {}, children = []) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === 'className') n.className = v;
    else if (k === 'text') n.textContent = v;
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

function norm(s) { return (s || '').toLowerCase(); }

function filterList(items, q) {
  q = norm(q).trim();
  if (!q) return items;
  const parts = q.split(/\s+/).filter(Boolean);
  return items.filter(it => {
    const hay = norm([it.bvid, it.title, it.preview, it.owner_name, it.status].join(' '));
    return parts.every(p => hay.includes(p));
  });
}

async function renderHome() {
  const catalog = await loadJSON('data/catalog.json');
  const root = document.getElementById('app');
  root.innerHTML = '';
  root.appendChild(el('header', {}, [
    el('h1', { text: 'loop-bilibili 字幕浏览' }),
    el('div', { className: 'meta', text: '构建于 ' + (catalog.built_at || '') }),
  ]));
  root.appendChild(el('p', { className: 'meta', text: '从 v2 SQLite 快照生成的静态站 · 只读浏览' }));
  const grid = el('div', { className: 'grid' });
  for (const up of catalog.ups || []) {
    const card = el('div', { className: 'card' });
    card.appendChild(el('h2', {}, [el('a', { href: 'ups/' + up.slug + '/', text: up.title })]));
    card.appendChild(el('div', { className: 'sub', text: up.slug + (up.owner_mid ? ' · mid ' + up.owner_mid : '') }));
    card.appendChild(el('div', { className: 'stats' }, [
      el('span', { className: 'pill' }, [document.createTextNode('视频 '), el('b', { text: String(up.videos) })]),
      el('span', { className: 'pill ok' }, [document.createTextNode('ok '), el('b', { text: String(up.ok) })]),
      el('span', { className: 'pill empty' }, [document.createTextNode('empty '), el('b', { text: String(up.empty) })]),
      el('span', { className: 'pill bad' }, [document.createTextNode('other '), el('b', { text: String(up.other) })]),
    ]));
    card.appendChild(el('div', { className: 'actions' }, [
      el('a', { href: 'ups/' + up.slug + '/', text: '进入列表 →' }),
    ]));
    grid.appendChild(card);
  }
  root.appendChild(grid);
  root.appendChild(el('footer', { text: 'loop-bilibili v2 · GitHub Pages static export' }));
}

async function renderUpList(slug) {
  const base = '../../data/' + slug + '/';
  const [meta, videos] = await Promise.all([
    loadJSON(base + 'meta.json'),
    loadJSON(base + 'videos.json'),
  ]);
  const root = document.getElementById('app');
  root.innerHTML = '';
  root.appendChild(el('div', { className: 'nav' }, [
    el('a', { href: '../../', text: '← 全部 UP' }),
  ]));
  root.appendChild(el('header', {}, [
    el('h1', { text: meta.title || slug }),
    el('div', { className: 'meta', text: (meta.owner_mid ? 'mid ' + meta.owner_mid + ' · ' : '') + videos.length + ' 条' }),
  ]));
  root.appendChild(el('div', { className: 'stats' }, [
    el('span', { className: 'pill ok' }, [document.createTextNode('ok '), el('b', { text: String(meta.ok) })]),
    el('span', { className: 'pill empty' }, [document.createTextNode('empty '), el('b', { text: String(meta.empty) })]),
    el('span', { className: 'pill bad' }, [document.createTextNode('other '), el('b', { text: String(meta.other) })]),
    el('span', { className: 'pill' }, [document.createTextNode('字幕字数 '), el('b', { text: String(meta.total_chars) })]),
  ]));
  const input = el('input', { className: 'search', type: 'search', placeholder: '搜索标题 / BV 号 / 预览…', id: 'q' });
  root.appendChild(input);
  const grid = el('div', { className: 'grid', id: 'list' });
  root.appendChild(grid);
  root.appendChild(el('footer', { text: '数据只读 · 来自 v2 SQLite 导出' }));

  function paint(list) {
    grid.innerHTML = '';
    for (const v of list) {
      const card = el('div', { className: 'card', 'data-bvid': v.bvid }, [
        badge(v.status),
        el('h3', {}, [
          el('a', { href: 'v/' + v.bvid + '.html', text: v.title || v.bvid }),
        ]),
        el('div', { className: 'sub', text: v.bvid + (v.chars ? ' · ' + v.chars + ' 字' : '') + (v.published_at ? ' · ' + v.published_at : '') }),
        v.preview ? el('div', { className: 'preview', text: v.preview + (v.chars > 160 ? '…' : '') }) : null,
        el('div', { className: 'actions' }, [
          el('a', { href: 'v/' + v.bvid + '.html', text: '看字幕' }),
          el('a', { href: v.url, target: '_blank', rel: 'noopener', text: 'B 站' }),
        ]),
      ]);
      grid.appendChild(card);
    }
    if (!list.length) {
      grid.appendChild(el('div', { className: 'card', text: '无匹配结果' }));
    }
  }
  paint(videos);
  input.addEventListener('input', () => paint(filterList(videos, input.value)));
}

async function renderVideo(slug, bvid) {
  const data = await loadJSON('../../data/' + slug + '/v/' + bvid + '.json');
  const root = document.getElementById('app');
  root.innerHTML = '';
  root.appendChild(el('div', { className: 'nav' }, [
    el('a', { href: '../', text: '← 返回列表' }),
    document.createTextNode(' · '),
    el('a', { href: '../../', text: '全部 UP' }),
  ]));
  root.appendChild(el('header', {}, [
    el('h1', { text: data.title || bvid }),
    el('div', { className: 'meta' }, [
      badge(data.status),
      document.createTextNode(' ' + bvid + (data.chars ? ' · ' + data.chars + ' 字' : '')),
    ]),
  ]));
  root.appendChild(el('div', { className: 'actions', style: 'margin-bottom:1rem' }, [
    el('a', { href: data.url, target: '_blank', rel: 'noopener', text: '打开 B 站视频' }),
  ]));
  if (data.status === 'ok' && data.text) {
    root.appendChild(el('div', { className: 'video-body', text: data.text }));
  } else {
    root.appendChild(el('div', { className: 'card', text: data.error || ('状态: ' + data.status + '（无字幕正文）') }));
  }
}

window.SubtitleSite = { renderHome, renderUpList, renderVideo };
"""


def video_html(slug: str, bvid: str, title: str) -> str:
    safe_title = (
        title.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{safe_title} · {bvid}</title>
  <link rel="stylesheet" href="../../../assets/app.css" />
</head>
<body>
  <div class="wrap" id="app">加载中…</div>
  <script src="../../../assets/app.js"></script>
  <script>
    SubtitleSite.renderVideo({json.dumps(slug)}, {json.dumps(bvid)}).catch(e => {{
      document.getElementById('app').textContent = String(e);
    }});
  </script>
</body>
</html>
"""


def up_index_html(slug: str, title: str) -> str:
    safe = title.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{safe} · 字幕列表</title>
  <link rel="stylesheet" href="../../assets/app.css" />
</head>
<body>
  <div class="wrap" id="app">加载中…</div>
  <script src="../../assets/app.js"></script>
  <script>
    SubtitleSite.renderUpList({json.dumps(slug)}).catch(e => {{
      document.getElementById('app').textContent = String(e);
    }});
  </script>
</body>
</html>
"""


HOME_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>loop-bilibili 字幕浏览</title>
  <link rel="stylesheet" href="assets/app.css" />
</head>
<body>
  <div class="wrap" id="app">加载中…</div>
  <script src="assets/app.js"></script>
  <script>
    SubtitleSite.renderHome().catch(e => {
      document.getElementById('app').textContent = String(e);
    });
  </script>
</body>
</html>
"""


def build_up(out: Path, src: UpSource) -> dict:
    videos = load_videos(src.db_path)
    ok = sum(1 for v in videos if v["status"] == "ok")
    empty = sum(1 for v in videos if v["status"] == "empty")
    other = len(videos) - ok - empty
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
        # per-video JSON (full text)
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
            }
        )

    meta = {
        "slug": src.slug,
        "title": src.title,
        "owner_mid": owner_mid,
        "videos": len(videos),
        "ok": ok,
        "empty": empty,
        "other": other,
        "total_chars": total_chars,
        "source_db": src.db_path.name,
    }
    write_json(data_dir / "meta.json", meta)
    write_json(data_dir / "videos.json", list_items)

    up_dir = out / "ups" / src.slug
    up_dir.mkdir(parents=True, exist_ok=True)
    (up_dir / "index.html").write_text(
        up_index_html(src.slug, src.title), encoding="utf-8"
    )
    v_dir = up_dir / "v"
    v_dir.mkdir(exist_ok=True)
    for v in videos:
        (v_dir / f"{v['bvid']}.html").write_text(
            video_html(src.slug, v["bvid"], v["title"]), encoding="utf-8"
        )

    print(
        f"  {src.slug}: videos={len(videos)} ok={ok} empty={empty} other={other}",
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
        default="",
        help="optional site base path e.g. /loop-bilibili/ (for project pages)",
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

    out = Path(args.out)
    if out.exists():
        # clean only our generated tree content carefully
        import shutil

        shutil.rmtree(out)
    out.mkdir(parents=True)

    (out / "assets").mkdir()
    (out / "assets" / "app.css").write_text(CSS, encoding="utf-8")
    (out / "assets" / "app.js").write_text(JS, encoding="utf-8")
    (out / "index.html").write_text(HOME_HTML, encoding="utf-8")

    ups_meta = []
    for src in sources:
        if not src.db_path.is_file():
            print(f"skip missing db: {src.db_path}", file=sys.stderr)
            continue
        print(f"building {src.slug} from {src.db_path}", flush=True)
        meta = build_up(out, src)
        ups_meta.append(meta)

    catalog = {
        "built_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "ups": ups_meta,
        "base_url": args.base_url,
    }
    write_json(out / "data" / "catalog.json", catalog)
    # nojekyll for GH pages
    (out / ".nojekyll").write_text("", encoding="utf-8")
    print(f"done -> {out.resolve()} ({len(ups_meta)} ups)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
