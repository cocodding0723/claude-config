// 선언형 장면: script.json 의 scenes 에 자료(이미지·영상·라벨)를 나열하면 말에 맞춰 차례로 쌓는다.
// 장면 코드를 쓰지 않고 영상을 만들기 위한 것이다.

const num = (v, d) => (typeof v === 'number' ? v : d);

// "낱말", "낱말+0.3", "beat:1", 1.5 를 장면 기준 초로 바꾼다.
function timeOf(v, info, fallback) {
  if (v === undefined || v === null) return fallback;
  if (typeof v === 'number') return v;
  const m = /^(.*?)([+-]\d+(?:\.\d+)?)?$/.exec(String(v));
  const off = m[2] ? parseFloat(m[2]) : 0, key = m[1];
  if (key.startsWith('beat:')) return info.beat(parseInt(key.slice(5), 10)) + off;
  if (key === 'end') return info.dur + off;
  const w = info.words.find((x) => x.text.includes(key));
  if (!w) { (window.__warn = window.__warn || []).push(`장면 ${info.id}: 낱말 "${key}" 을 대본에서 못 찾음`); return fallback; }
  return w.start + off;
}

async function loadImage(src) {
  const img = new Image();
  img.src = src;
  try { await img.decode(); } catch (e) { throw new Error('이미지를 못 읽음: ' + src); }
  return img;
}

// 이미지를 crop 영역만 보이게 상자에 넣는다. 반환: 상자, 이미지, 보이는 크기.
function frameMedia(S, img, it, maxW, maxH) {
  const [cx, cy, cw, ch] = it.crop || [0, 0, 1, 1];
  const sw = img.naturalWidth * cw, sh = img.naturalHeight * ch;
  const k = Math.min(maxW / sw, maxH / sh), w = Math.round(sw * k), h = Math.round(sh * k);
  const box = S.el('div', { style: `position:relative;width:${w}px;height:${h}px;overflow:hidden;border-radius:${num(it.round, 18)}px;flex:none;${it.border === false ? '' : 'outline:3px solid rgba(255,255,255,.14);outline-offset:-3px;'}` });
  img.style.cssText = `position:absolute;left:${-cx * img.naturalWidth * k}px;top:${-cy * img.naturalHeight * k}px;width:${img.naturalWidth * k}px;height:${img.naturalHeight * k}px;transform-origin:${(cx + cw / 2) * 100}% ${(cy + ch / 2) * 100}%`;
  box.appendChild(img);
  const dim = S.el('div', { style: 'position:absolute;inset:0;background:rgba(0,0,0,.55);visibility:hidden' }, box);
  return { box, dim, w, h };
}

export function stackScene(def, clipMeta) {
  return {
    full: def.full, bg: def.bg, noCaption: def.noCaption,
    async setup(root, S, info) {
      const padTop = num(def.padTop, 30), usable = S.H - num(def.reserve, S.stackReserve || 0);
      const col = S.el('div', { style: `position:absolute;left:0;top:0;width:${S.W}px;height:${usable}px;padding:${padTop}px 50px 0;display:flex;flex-wrap:wrap;align-items:center;align-content:${def.align === 'center' ? 'center' : 'flex-start'};justify-content:center;gap:${num(def.gap, 18)}px` }, root);
      const items = [], byId = {};
      for (const it of def.items || []) {
        const at = timeOf(it.at, info, 0), until = timeOf(it.until, info, Infinity);
        const rec = { it, at, until };
        if (it.clear) { items.push({ ...rec, clear: true }); continue; }
        const host = it.on ? byId[it.on] : null;
        if (it.on && !host) throw new Error(`장면 ${info.id}: on "${it.on}" 에 해당하는 id 가 앞에 없다`);
        if (it.image || it.clip) {
          const maxW = (S.W - 100) * num(it.w, 1) - (it.w ? num(def.gap, 18) / 2 : 0), maxH = num(it.h, 560);
          let img, meta = null;
          if (it.clip) {
            meta = clipMeta[it.clip + '|' + num(it.from, 0) + '|' + num(it.dur, 8)];
            if (!meta) throw new Error('영상 프레임이 준비되지 않음: ' + it.clip);
            img = await loadImage(`${meta.dir}/f_00001.jpg`);
          } else img = await loadImage('/project/' + it.image);
          const m = frameMedia(S, img, it, maxW, maxH);
          const cell = S.el('div', { style: `flex:0 0 ${it.w ? Math.round(num(it.w, 1) * 100) - 1 + '%' : '100%'};display:none;justify-content:center` }, col);
          cell.appendChild(m.box);
          Object.assign(rec, { node: cell, flow: true, img, meta, ...m, dimAt: timeOf(it.dimAt, info, Infinity), dimUntil: timeOf(it.dimUntil, info, Infinity) });
          if (it.id) byId[it.id] = rec;
        } else if (it.label !== undefined) {
          const node = S.label(host ? host.box : col, it.label, { kind: it.kind || 'box', size: num(it.size, 56), color: it.color, mono: it.mono, weight: it.weight });
          if (host) node.style.cssText += `;left:${num(it.x, 0.5) * 100}%;top:${num(it.y, 0.5) * 100}%;transform:translate(${it.anchor === 'left' ? '0' : '-50%'},-50%)`;
          else { node.style.position = 'static'; node.style.display = 'none'; node.style.visibility = 'visible'; node.style.whiteSpace = 'pre-wrap'; node.style.wordBreak = 'keep-all'; node.style.textAlign = 'center'; node.style.maxWidth = (S.W - 100) + 'px'; node.dataset.flow = '1'; }
          Object.assign(rec, { node, flow: !host, type: it.type && it.label.length });
          if (it.id) byId[it.id] = rec;
        } else if (it.code !== undefined) {
          // 코드·명령·JSON 을 창 모양 카드에 보여 준다. hl 의 문자열은 노랗게 칠한다.
          const esc = (x) => x.replace(/&/g, '&amp;').replace(/</g, '&lt;');
          let html = esc(it.code);
          for (const h of it.hl || []) html = html.split(esc(h)).join(`<span style="color:#f2cc60">${esc(h)}</span>`);
          const node = S.el('div', { style: `display:none;flex:0 0 100%;border-radius:20px;background:#15181c;border:3px solid #2a2f36;overflow:hidden` }, col);
          if (it.title) S.el('div', { text: it.title, style: 'height:56px;background:#22262c;color:#8b95a1;font-size:28px;font-weight:700;line-height:56px;padding-left:26px' }, node);
          S.el('div', { html, style: `padding:22px 26px;font-family:Menlo,ui-monospace,Pretendard,monospace;font-size:${num(it.size, 34)}px;font-weight:700;line-height:1.45;color:#e6edf3;white-space:pre-wrap;word-break:break-all` }, node);
          node.dataset.flow = '1';
          Object.assign(rec, { node, flow: true });
        } else if (it.box) {
          if (!host) throw new Error(`장면 ${info.id}: box 는 on 으로 이미지 id 를 가리켜야 한다`);
          const [x, y, w, h] = it.box;
          rec.node = S.el('div', { style: `position:absolute;left:${x * 100}%;top:${y * 100}%;width:${w * 100}%;height:${h * 100}%;border:${num(it.thick, 8)}px solid ${it.color || '#E8364A'};border-radius:10px;visibility:hidden` }, host.box);
        } else throw new Error(`장면 ${info.id}: 알 수 없는 항목 ${JSON.stringify(it)}`);
        items.push(rec);
      }
      root._stack = { items, col, usable };
    },
    async frame(root, t, info, S) {
      const { items } = root._stack;
      let cleared = -Infinity;
      for (const r of items) if (r.clear && t >= r.at) cleared = Math.max(cleared, r.at);
      for (const r of items) {
        if (r.clear) continue;
        const on = t >= r.at && t < r.until && r.at >= cleared;
        if (r.flow) r.node.style.display = on ? (r.node.dataset.flow ? 'block' : 'flex') : 'none';
        else r.node.style.visibility = on ? 'visible' : 'hidden';
        if (!on) continue;
        if (r.type) r.node.textContent = r.it.label.slice(0, Math.ceil(S.prog(t, r.at, r.at + r.it.label.length * 0.045) * r.it.label.length));
        if (r.img) {
          if (r.meta) { // 영상: 시각에 맞는 프레임으로 갈아 끼운다
            const f = Math.floor((t - r.at) * num(r.it.rate, 1) * r.meta.fps);
            const i = (r.it.loop === false ? Math.min(f, r.meta.n - 1) : f % r.meta.n) + 1;
            const src = `${r.meta.dir}/f_${String(i).padStart(5, '0')}.jpg`;
            if (!r.img.src.endsWith(src)) { r.img.src = src; try { await r.img.decode(); } catch (e) { /* 다음 프레임에서 다시 */ } }
          }
          if (r.it.kenburns) r.img.style.transform = `scale(${S.lerp(1, r.it.kenburns, S.prog(t, r.at, Math.min(r.until, info.dur)))})`;
          r.dim.style.visibility = t >= r.dimAt && t < r.dimUntil ? 'visible' : 'hidden';
        }
      }
    },
    // 쌓인 것이 쓸 수 있는 높이를 넘는지 잰다(검사용).
    overflow(root) {
      const { col, usable } = root._stack, top = col.getBoundingClientRect().top;
      let bottom = 0;
      for (const c of col.children) if (c.style.display !== 'none') bottom = Math.max(bottom, c.getBoundingClientRect().bottom - top);
      return bottom > usable + 1 ? Math.round(bottom - usable) : 0;
    },
  };
}
