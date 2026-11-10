// 샘플(codingapple 모드): 0.1 + 0.2 가 0.3 이 아닌 이유
// 화면의 계산 결과는 전부 이 코드가 실제로 계산한 값이다.
const MONO = 'font-family:Menlo,ui-monospace,Pretendard,monospace;';
// 0.1 을 담은 64비트를 그대로 꺼낸다.
const bits = (x) => { const v = new DataView(new ArrayBuffer(8)); v.setFloat64(0, x); let s = ''; for (let i = 0; i < 8; i++) s += v.getUint8(i).toString(2).padStart(8, '0'); return s; };
// 0.1 의 이진 소수 자릿수를 n 개 계산한다(정수 연산이라 정확하다).
const binFrac = (num, den, n) => { let r = num, s = ''; for (let i = 0; i < n; i++) { r *= 2; s += r >= den ? '1' : '0'; if (r >= den) r -= den; } return s; };
const win = (S, root, title, y, h) => {
  const w = S.el('div', { style: `position:absolute;left:50px;top:${y}px;width:980px;height:${h}px;border-radius:22px;background:#15181c;border:3px solid #2a2f36;overflow:hidden;visibility:hidden` }, root);
  S.el('div', { text: title, style: 'height:64px;background:#22262c;color:#8b95a1;font-size:32px;font-weight:700;line-height:64px;padding-left:28px' }, w);
  return w;
};
const line = (S, parent, html, y, size = 62) => S.el('div', { html, style: `position:absolute;left:36px;top:${y}px;${MONO}font-size:${size}px;font-weight:700;color:#e6edf3;white-space:pre;visibility:hidden` }, parent);
// 체념한 얼굴. 동그라미와 선으로만 그린다.
const face = (S, root, x, y, r) => {
  const g = S.svg('svg', { viewBox: '-110 -110 220 220', style: `position:absolute;left:${x - r}px;top:${y - r}px;width:${r * 2}px;height:${r * 2}px;visibility:hidden` }, root);
  S.svg('circle', { r: 100, fill: '#FFD23F', stroke: '#fff', 'stroke-width': 8 }, g);
  S.svg('path', { d: 'M-58 -22 L-22 -22 M22 -22 L58 -22', stroke: '#10162a', 'stroke-width': 12, 'stroke-linecap': 'round' }, g);
  S.svg('path', { d: 'M-38 46 Q0 34 38 46', fill: 'none', stroke: '#10162a', 'stroke-width': 12, 'stroke-linecap': 'round' }, g);
  return g;
};

export default {
  console: {
    setup(root, S) {
      const w = win(S, root, '콘솔', 60, 430), sum = 0.1 + 0.2, str = String(sum);
      root._ = {
        w, str,
        q: line(S, w, '<span style="color:#7ee787">&gt;</span> 0.1 + 0.2', 100),
        expect: S.label(root, '(0.3 나와야 됨)', { x: 60, y: 530, kind: 'plain', size: 56, color: '#8b95a1' }),
        a: line(S, w, '', 220, 76),
        box: S.el('div', { style: 'position:absolute;border:8px solid #E8364A;border-radius:10px;visibility:hidden' }, w),
        tag: S.label(root, '▲ 이 4 어디서 나옴?', { x: 500, y: 390, kind: 'red', size: 54 }),
        real: S.label(root, '(조작 아님. 진짜 계산한 값임)', { x: 60, y: 640, kind: 'box', size: 50 }),
      };
    },
    frame(root, t, info, S) {
      const { w, str, q, expect, a, box, tag, real } = root._, b1 = info.beat(1);
      S.show(w, true);
      // 질문은 타자 치듯 한 글자씩 나온다
      const typed = Math.round(S.prog(t, 0.15, info.when('시키면', 1.6)) * 9);
      q.innerHTML = '<span style="color:#7ee787">&gt;</span> ' + '0.1 + 0.2'.slice(0, typed); S.show(q, true);
      S.appear(expect, t, info.when('0.3이', 2.4), b1);
      const shown = S.appear(a, t, info.when('안', 2.9));
      if (shown) a.innerHTML = `<span style="color:#79c0ff">${str.slice(0, -1)}</span><span id="four" style="color:#ff7b72">${str.slice(-1)}</span>`;
      const hit = info.when('4가', b1 + 1.4);
      if (S.appear(box, t, hit)) { const r = a.querySelector('#four').getBoundingClientRect(), p = w.getBoundingClientRect(); box.style.cssText += `;left:${r.left - p.left - 12}px;top:${r.top - p.top - 6}px;width:${r.width + 24}px;height:${r.height + 12}px`; }
      S.appear(tag, t, hit + 0.1); S.appear(real, t, info.when('콘솔에', b1 + 0.4));
    },
  },

  binary: {
    setup(root, S) {
      const digits = binFrac(1, 10, 44);
      root._ = {
        digits,
        head: S.label(root, '0.1 을 이진수로 바꾸면', { x: 60, y: 60, kind: 'plain', size: 60 }),
        bin: S.el('div', { style: `position:absolute;left:60px;top:160px;width:960px;${MONO}font-size:70px;font-weight:700;line-height:1.3;word-break:break-all;color:#e6edf3` }, root),
        inf: S.label(root, '(0011 이 끝없이 반복됨)', { x: 60, y: 480, kind: 'red', size: 54 }),
        third: S.el('div', { html: '1 ÷ 3 = <span style="color:#79c0ff">0.3333333333…</span>', style: `position:absolute;left:60px;top:640px;${MONO}font-size:66px;font-weight:700;color:#e6edf3;visibility:hidden` }, root),
        same: S.label(root, '(이거랑 똑같은 거임)', { x: 60, y: 760, kind: 'box', size: 54 }),
      };
    },
    frame(root, t, info, S) {
      const { digits, head, bin, inf, third, same } = root._, b1 = info.beat(1);
      S.appear(head, t, 0.1);
      const a = 0.3, n = Math.floor(S.prog(t, a, b1 - 0.3) * digits.length);
      // 0011 묶음마다 색을 번갈아 칠해 반복이 보이게 한다
      let html = t >= a ? '0.' : '';
      for (let i = 0; i < n; i++) html += `<span style="color:${i < 1 ? '#e6edf3' : Math.floor((i - 1) / 4) % 2 ? '#f2cc60' : '#79c0ff'}">${digits[i]}</span>`;
      bin.innerHTML = html + (n >= digits.length ? '<span style="color:#ff7b72">…</span>' : '');
      S.appear(inf, t, info.when('끝없이', 4.6));
      S.appear(third, t, info.when('3분의', b1 + 0.4)); S.appear(same, t, info.when('똑같은', b1 + 2.4));
    },
  },

  cut: {
    setup(root, S) {
      const b = bits(0.1);
      const grid = S.el('div', { style: 'position:absolute;left:60px;top:150px;width:960px;display:grid;grid-template-columns:repeat(16,1fr);gap:6px' }, root);
      const cells = [...b].map((c, i) => S.el('div', { text: c, style: `height:74px;border-radius:8px;${MONO}font-size:44px;font-weight:700;display:flex;align-items:center;justify-content:center;visibility:hidden;background:${i === 0 ? '#3d2b5a' : i < 12 ? '#1f3a5a' : '#1c2a22'};color:${i === 0 ? '#d2a8ff' : i < 12 ? '#79c0ff' : '#7ee787'}` }, grid));
      root._ = {
        cells,
        head: S.label(root, '메모리: 64칸', { x: 60, y: 50, kind: 'plain', size: 60 }),
        snip: S.label(root, '✂ 여기서 자름', { x: 600, y: 480, kind: 'red', size: 54 }),
        stored: S.el('div', { html: `저장된 값<br><span style="color:#79c0ff">0.1000000000000000</span><span style="color:#ff7b72">${(0.1).toFixed(40).slice(18)}</span>`, style: `position:absolute;left:60px;top:590px;width:960px;${MONO}font-size:46px;font-weight:700;line-height:1.4;color:#8b95a1;word-break:break-all;visibility:hidden` }, root),
        err: S.label(root, '(0.1 이 아님. 살짝 큼)', { x: 60, y: 820, kind: 'box', size: 54 }),
      };
    },
    frame(root, t, info, S) {
      const { cells, head, snip, stored, err } = root._;
      S.appear(head, t, 0.1);
      const n = Math.floor(S.prog(t, info.when('64칸밖에', 1.0) - 0.3, info.when('잘라', 3.0)) * 64);
      cells.forEach((c, i) => S.show(c, i < n));
      S.appear(snip, t, info.when('잘라', 3.0));
      S.appear(stored, t, info.when('오차가', 4.6)); S.appear(err, t, info.when('튀어나오는', 6.0));
    },
  },

  fix: {
    setup(root, S) {
      const w = win(S, root, '콘솔', 40, 860), rows = [
        ['0.1 + 0.2 === 0.3', String(0.1 + 0.2 === 0.3), 90],
        ['10 + 20 === 30', String(10 + 20 === 30), 290],
        ['Math.abs(0.1 + 0.2 - 0.3) < 1e-9', String(Math.abs(0.1 + 0.2 - 0.3) < 1e-9), 560],
      ];
      const mk = ([q, a, y]) => ({ q: line(S, w, `<span style="color:#7ee787">&gt;</span> ${q.replace('<', '&lt;')}`, y, q.length > 20 ? 42 : 58), a: line(S, w, `<span style="color:${a === 'true' ? '#7ee787' : '#ff7b72'}">${a}</span>`, y + 78, 64) });
      root._ = {
        w, r: rows.map(mk),
        no: S.label(root, '✗ 돈 계산 금지', { x: 600, y: 210, kind: 'red', size: 52 }),
        yes: S.label(root, '(원 단위 정수로 하면 됨)', { x: 420, y: 410, kind: 'white', size: 46 }),
        eq: S.label(root, '(이 정도 차이면 같다고 침)', { x: 100, y: 790, kind: 'white', size: 46 }),
      };
    },
    frame(root, t, info, S) {
      const { w, r, no, yes, eq } = root._, b1 = info.beat(1);
      S.show(w, true);
      const at = [0.1, info.when('정수로', 3.6) - 0.4, b1 + 0.2];
      r.forEach((x, i) => { S.appear(x.q, t, at[i]); S.appear(x.a, t, at[i] + 0.55); });
      S.appear(no, t, info.when('큰일', 2.0)); S.appear(yes, t, info.when('정수로', 3.6));
      S.appear(eq, t, info.when('같다고', b1 + 3.0));
    },
  },

  end: {
    setup(root, S) {
      const row = (text, y, color) => S.el('div', { html: text, style: `position:absolute;left:0;top:${y}px;width:1080px;text-align:center;font-size:120px;font-weight:900;color:${color};visibility:hidden` }, root);
      root._ = { bug: row('<span id="bugword">버그</span>', 150, '#fff'), trad: row('전통', 330, '#fff'), f: face(S, root, 540, 680, 130), year: S.label(root, '(1985년 표준 그대로임)', { x: 540, y: 60, kind: 'plain', size: 48, color: '#8b95a1', align: 'center' }) };
    },
    frame(root, t, info, S) {
      const { bug, trad, f, year } = root._;
      S.appear(year, t, info.when('수십', 0.3)); S.appear(bug, t, 0.15); const no = t >= info.when('버그가', 2.6); bug.firstChild.style.cssText = no ? 'text-decoration:line-through;text-decoration-thickness:10px;text-decoration-color:#E8364A;color:#8b95a1' : ''; S.appear(trad, t, info.when('전통인', 3.5)); S.show(f, t >= info.when('전통인', 3.5) + 0.25);
    },
  },
};
