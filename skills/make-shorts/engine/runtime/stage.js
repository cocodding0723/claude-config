// 쇼츠 무대. 모든 화면은 시각 t 의 순수 함수여야 한다 — CSS 애니메이션·setTimeout·Math.random 을 쓰면
// 프레임 캡처가 병렬로 돌 때 프레임마다 다른 그림이 나온다.
import { MODES } from './modes.js';
import { stackScene } from './stack.js';

const P = '/project/';
const j = async (u) => (await fetch(u)).json();

// ───────── 도우미 (장면 코드에서 S 로 받는다) ─────────
const clamp = (x, a = 0, b = 1) => Math.min(b, Math.max(a, x));
const lerp = (a, b, k) => a + (b - a) * k;
const prog = (t, a, b) => clamp((t - a) / (b - a));
const ease = {
  linear: (k) => k,
  out: (k) => 1 - Math.pow(1 - k, 3),
  inOut: (k) => (k < 0.5 ? 4 * k * k * k : 1 - Math.pow(-2 * k + 2, 3) / 2),
  back: (k) => { const c = 1.70158; return 1 + (c + 1) * Math.pow(k - 1, 3) + c * Math.pow(k - 1, 2); },
};
// 시드 고정 난수. 같은 시드는 언제나 같은 수열이다.
const rng = (seed) => { let s = (seed >>> 0) || 1; return () => { s ^= s << 13; s >>>= 0; s ^= s >> 17; s ^= s << 5; s >>>= 0; return s / 4294967296; }; };
const NS = 'http://www.w3.org/2000/svg';
const svg = (tag, attrs = {}, parent) => {
  const e = document.createElementNS(NS, tag);
  for (const k in attrs) e.setAttribute(k, attrs[k]);
  if (parent) parent.appendChild(e);
  return e;
};
const el = (tag, attrs = {}, parent) => {
  const e = document.createElement(tag);
  for (const k in attrs) {
    if (k === 'style') e.style.cssText = attrs[k];
    else if (k === 'text') e.textContent = attrs[k];
    else if (k === 'html') e.innerHTML = attrs[k];
    else e.setAttribute(k, attrs[k]);
  }
  if (parent) parent.appendChild(e);
  return e;
};
// 손으로 그린 듯한 닫힌 고리의 path. t 를 주면 초당 boil 번 모양이 떨린다.
const wobble = (cx, cy, rx, ry, { t = 0, boil = 10, amp = 5, seed = 7, turns = 1.08, n = 40 } = {}) => {
  const r = rng(seed * 977 + Math.floor(t * boil) * 131);
  let d = '';
  for (let i = 0; i <= n * turns; i++) {
    const a = (i / n) * Math.PI * 2 - Math.PI / 2;
    const k = 1 + (i / n) * 0.03;
    const x = cx + Math.cos(a) * rx * k + (r() - 0.5) * amp * 2;
    const y = cy + Math.sin(a) * ry * k + (r() - 0.5) * amp * 2;
    d += (i ? 'L' : 'M') + x.toFixed(1) + ' ' + y.toFixed(1);
  }
  return d;
};
// 화살표 path: 몸통 + 촉.
const arrow = (x1, y1, x2, y2, head = 34) => {
  const a = Math.atan2(y2 - y1, x2 - x1);
  const h = (da) => `L${(x2 - Math.cos(a + da) * head).toFixed(1)} ${(y2 - Math.sin(a + da) * head).toFixed(1)}`;
  return `M${x1} ${y1}L${x2} ${y2}${h(0.5)}M${x2} ${y2}${h(-0.5)}`;
};
// 이미 그려진 path 를 k(0~1) 만큼만 드러낸다.
const draw = (path, k) => {
  const L = path.getTotalLength();
  path.style.strokeDasharray = L;
  path.style.strokeDashoffset = L * (1 - clamp(k));
};
const show = (node, on) => { node.style.visibility = on ? 'visible' : 'hidden'; };
// 글자 라벨. kind: box(검정 상자+흰 글자) · plain(글자만) · white(흰 상자+어두운 글자) · red(빨간 상자+흰 글자) · pill(검정 알약+색 글자)
const LABEL = {
  box: 'background:#000;color:#fff;padding:10px 22px;', plain: 'color:#fff;', white: 'background:#fff;color:#10162a;padding:10px 22px;',
  red: 'background:#E8364A;color:#fff;padding:10px 22px;', pill: 'background:rgba(0,0,0,.78);padding:12px 28px;border-radius:999px;',
};
const label = (parent, text, { x = 0, y = 0, kind = 'box', size = 64, color, align = 'left', weight = 800, mono = false } = {}) =>
  el('div', { text, style: `position:absolute;left:${x}px;top:${y}px;${align === 'center' ? 'transform:translateX(-50%);' : ''}${LABEL[kind]}${color ? `color:${color};` : ''}font-size:${size}px;font-weight:${weight};line-height:1.25;white-space:pre;visibility:hidden;${mono ? 'font-family:Menlo,ui-monospace,Pretendard,monospace;' : ''}` }, parent);
// at 부터 until 전까지 곧바로 보인다(움직임 없음).
const appear = (node, t, at, until = Infinity) => { const on = t >= at && t < until; show(node, on); return on; };
// at 부터 튀어나오듯 커지며 보인다. 가운데 정렬(align:center) 라벨은 center 를 준다.
const pop = (node, t, at, { until = Infinity, center = false, rot = 0 } = {}) => {
  if (!appear(node, t, at, until)) return;
  node.style.transform = `${center ? 'translateX(-50%) ' : ''}scale(${ease.back(prog(t, at, at + 0.22))}) rotate(${rot}deg)`;
};
const S = { clamp, lerp, prog, ease, rng, svg, el, wobble, arrow, draw, show, label, appear, pop, three: () => import('./vendor/three.module.min.js') };

// ───────── 무대 조립 ─────────
const stage = document.getElementById('stage');
let TL, SCRIPT, MODE, SCENES, roots = {}, infos = {}, capEl, capInner;

function buildChrome() {
  const m = MODE;
  document.documentElement.style.setProperty('--bg', m.bg);
  stage.style.background = m.bg;
  el('div', { id: 'scenes' }, stage);
  if (m.title) {
    const plate = el('div', { id: 'titleplate', style: `position:absolute;left:${m.title.plate.x}px;top:${m.title.plate.y}px;width:${m.title.plate.w}px;height:${m.title.plate.h}px;border-radius:${m.title.plate.r}px;background:${m.title.plate.bg};visibility:hidden` }, stage);
    const t = el('div', { id: 'title' }, stage);
    (SCRIPT.title || []).forEach((line, i) => {
      const L = m.title.lines[Math.min(i, m.title.lines.length - 1)];
      const d = el('div', { text: line, style: `position:absolute;left:0;width:1080px;top:${L.y}px;text-align:center;white-space:nowrap;font-weight:${L.weight};font-size:${L.size}px;line-height:1;color:${L.color};letter-spacing:${L.spacing || 0}px` }, t);
      // 한 줄이 가로 폭을 넘으면 글자를 줄인다. 제목은 영상 내내 고정이라 한 번만 맞추면 된다.
      const max = 1080 * (L.maxW || 0.9);
      if (d.scrollWidth > max) d.style.fontSize = Math.floor(L.size * max / d.scrollWidth) + 'px';
    });
    S._plate = plate;
  }
  const c = m.caption;
  if (!c) return;
  capEl = el('div', { id: 'caption', style: `position:absolute;left:0;width:1080px;top:${c.y}px;display:flex;justify-content:center;transform:translateY(-50%);visibility:hidden` }, stage);
  capInner = el('div', { style: `max-width:${c.maxW}px;padding:${c.pad};border-radius:${c.r}px;background:${c.box};color:${c.color};font-weight:${c.weight};font-size:${c.size}px;line-height:${c.lh};text-align:center;white-space:pre-line;word-break:keep-all;${c.stroke ? `-webkit-text-stroke:${c.stroke};paint-order:stroke fill;` : ''}${c.shadow ? `text-shadow:${c.shadow};` : ''}` }, capEl);
}

function sceneInfo(sc) {
  const beats = TL.beats.filter((b) => b.scene === sc.key).map((b) => ({ text: b.text, start: b.start - sc.start, end: b.end - sc.start }));
  const words = TL.words.filter((w) => w.start >= sc.start - 0.01 && w.start < sc.end).map((w) => ({ text: w.text, start: w.start - sc.start, end: w.end - sc.start }));
  return {
    id: sc.id, dur: sc.end - sc.start, beats, words,
    // 이 장면 안에서 주어진 낱말이 처음 발음되는 시각(장면 기준 초). 없으면 fallback.
    when: (s, fallback = 0) => { const w = words.find((x) => x.text.includes(s)); return w ? w.start : fallback; },
    beat: (i) => (beats[i] ? beats[i].start : (beats.length ? beats[beats.length - 1].end : 0)),
  };
}

async function init() {
  [TL, SCRIPT] = await Promise.all([j(P + 'build/timeline.json'), j(P + 'script.json')]);
  MODE = MODES[SCRIPT.mode];
  if (!MODE) throw new Error('모르는 mode: ' + SCRIPT.mode);
  // 장면은 두 가지로 정의한다: script.json 의 scenes(자료 나열) 와 scenes.js(직접 그리는 코드). 같은 이름이면 코드가 이긴다.
  const clips = await fetch(P + 'build/clips/meta.json').then((r) => (r.ok ? r.json() : {}), () => ({}));
  const coded = (await fetch(P + 'scenes.js', { method: 'HEAD' })).ok ? (await import(P + 'scenes.js')).default : {};
  S.stackReserve = MODE.stackReserve || 0;
  SCENES = { ...Object.fromEntries(Object.entries(SCRIPT.scenes || {}).map(([k, d]) => [k, stackScene(d, clips)])), ...coded };
  buildChrome();
  await document.fonts.load(`900 40px Pretendard`); await document.fonts.load(`700 40px Pretendard`); await document.fonts.ready;
  const holder = document.getElementById('scenes');
  const missing = [];
  for (const sc of TL.scenes) {
    const def = SCENES[sc.id];
    if (!def) { missing.push(sc.id); continue; }
    const r = def.full ? MODE.full : MODE.card;
    const root = el('div', { class: 'scene', 'data-scene': sc.key, style: `position:absolute;left:${r.x}px;top:${r.y}px;width:${r.w}px;height:${r.h}px;overflow:hidden;${def.full ? '' : `border-radius:${MODE.card.r || 0}px;`}background:${def.bg || MODE.sceneBg}` }, holder);
    roots[sc.key] = root; infos[sc.key] = sceneInfo(sc);
    S.W = r.w; S.H = r.h;
    if (def.setup) await def.setup(root, S, infos[sc.key]);
    root.style.display = 'none';
  }
  if (missing.length) throw new Error('scenes.js 에 없는 장면: ' + [...new Set(missing)].join(', '));
  window.__meta = { duration: TL.duration, captions: MODE.caption ? TL.captions : [], scenes: TL.scenes, mode: SCRIPT.mode };
}

window.__seek = async (t) => {
  const sc = TL.scenes.find((s) => t >= s.start && t < s.end) || TL.scenes[TL.scenes.length - 1];
  for (const k in roots) roots[k].style.display = k === sc.key ? 'block' : 'none';
  const def = SCENES[sc.id];
  if (S._plate) S._plate.style.visibility = def.full && !def.noPlate ? 'visible' : 'hidden';
  const r = def.full ? MODE.full : MODE.card; S.W = r.w; S.H = r.h;
  if (def.frame) await def.frame(roots[sc.key], Math.max(0, t - sc.start), infos[sc.key], S, t);
  if (capEl) {
    const cap = def.noCaption ? null : TL.captions.find((c) => t >= c.start && t < c.end);
    capEl.style.visibility = cap ? 'visible' : 'hidden';
    if (cap && capInner.textContent !== cap.text) capInner.textContent = cap.text;
  }
  if (MODE.onFrame) MODE.onFrame(stage, t, TL, S);
};
// 자막 상자가 화면 안에 들어오는지 재는 검사용.
window.__capBox = () => { const b = capInner.getBoundingClientRect(); const lh = parseFloat(getComputedStyle(capInner).lineHeight); const pad = parseFloat(getComputedStyle(capInner).paddingTop) * 2; return { x: b.x, y: b.y, w: b.width, h: b.height, lines: Math.round((b.height - pad) / lh) }; };

// 선언형 장면이 끝날 때 쌓인 것이 영역을 넘는지 잰다.
window.__overflow = async () => {
  const out = [];
  for (const sc of TL.scenes) {
    const def = SCENES[sc.id];
    if (!def.overflow) continue;
    await window.__seek(sc.end - 0.05);
    const px = def.overflow(roots[sc.key]);
    if (px) out.push({ id: sc.id, at: sc.end, px });
  }
  return { overflow: out, warn: window.__warn || [] };
};

window.__ready = init().then(() => true, (e) => { window.__error = String(e && e.stack || e); return false; });
