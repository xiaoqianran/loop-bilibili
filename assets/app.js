
function resolveSiteBase() {
  // Normalize: drop trailing slash and /index.html so directory URLs
  // like /loop-bilibili/ups/haianyu/ do NOT become the site root.
  let path = location.pathname || '';
  if (path.length > 1 && path.endsWith('/')) path = path.slice(0, -1);
  path = path.replace(/\/index\.html$/i, '');

  // video: .../ups/{slug}/v/{bvid}.html
  let m = path.match(/^(.*)\/ups\/[^/]+\/v\/[^/]+(?:\.html)?$/i);
  if (m) return (m[1] || '').replace(/\/$/, '');

  // up list: .../ups/{slug}
  m = path.match(/^(.*)\/ups\/[^/]+$/);
  if (m) return (m[1] || '').replace(/\/$/, '');

  // home: .../index.html already stripped → path is site root
  // e.g. /loop-bilibili or "" 
  const injected = String(window.SITE_BASE || '').replace(/\/$/, '');
  if (injected && (path === injected || path === '' || path === '/')) {
    return injected;
  }
  // still under /ups/... somehow → strip to parent of ups
  m = path.match(/^(.*)\/ups(?:\/.*)?$/);
  if (m) return (m[1] || '').replace(/\/$/, '');

  return injected;
}
const SITE_BASE = resolveSiteBase();
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
