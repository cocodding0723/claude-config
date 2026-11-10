// 샘플: 게임 속 먼 나무는 왜 종이 한 장일까?
// 화면의 삼각형 수는 전부 이 파일이 실제로 만든 모델에서 센 값이다.
let T; // three
const C = { bg: '#071216', sky: '#A4D6DF', cyan: '#34DBFF', red: '#FF2A55', yellow: '#F3C04A', lime: '#E4FF0A', ground: '#8FC49A', ground2: '#49A58E' };
const LEAF = [0x3f9f78, 0x49a58e, 0x2f8f6b, 0x5bb58f];
const fmt = (n) => Math.round(n).toLocaleString('en-US');

// ── 나무 모델. level 0 이 가장 정교하다.
const SPEC = [{ clumps: 48, detail: 5, seg: 24 }, { clumps: 12, detail: 1, seg: 8 }, { clumps: 3, detail: 0, seg: 5 }];
function makeTree(S, level, seed = 3) {
  const sp = SPEC[level], r = S.rng(seed), g = new T.Group();
  const trunk = new T.Mesh(new T.CylinderGeometry(0.16, 0.26, 2.2, sp.seg, 1, true), new T.MeshLambertMaterial({ color: 0x7a5a3c, flatShading: true }));
  trunk.position.y = 1.1; g.add(trunk);
  for (let i = 0; i < sp.clumps; i++) {
    const k = sp.clumps === 1 ? 0.5 : i / (sp.clumps - 1);
    const h = 2.0 + k * 3.0, spread = (1 - k * 0.75) * 1.25, a = r() * Math.PI * 2, d = Math.sqrt(r()) * spread;
    const size = S.lerp(0.95, 0.5, k) * (0.8 + r() * 0.4) * (level === 2 ? 1.7 : level === 1 ? 1.25 : 1);
    const m = new T.Mesh(new T.IcosahedronGeometry(size, sp.detail), new T.MeshLambertMaterial({ color: LEAF[i % LEAF.length], flatShading: true }));
    m.position.set(Math.cos(a) * d, h, Math.sin(a) * d); g.add(m);
  }
  let tris = 0;
  g.traverse((o) => { if (o.isMesh) tris += (o.geometry.index ? o.geometry.index.count : o.geometry.attributes.position.count) / 3; });
  g.userData.tris = tris;
  return g;
}
function lights(scene) {
  scene.add(new T.HemisphereLight(0xffffff, 0x35554a, 1.5));
  const d = new T.DirectionalLight(0xffffff, 1.9); d.position.set(4, 9, 6); scene.add(d);
}
function stage3d(root, S, { bg = C.bg, fov = 38, w = S.W, h = S.H, x = 0, y = 0 } = {}) {
  const canvas = S.el('canvas', { width: w, height: h, style: `position:absolute;left:${x}px;top:${y}px;width:${w}px;height:${h}px` }, root);
  const renderer = new T.WebGLRenderer({ canvas, antialias: true, preserveDrawingBuffer: true, alpha: bg === null });
  renderer.setSize(w, h, false);
  const scene = new T.Scene();
  if (bg !== null) scene.background = new T.Color(bg);
  lights(scene);
  const camera = new T.PerspectiveCamera(fov, w / h, 0.1, 400);
  return { renderer, scene, camera, canvas, draw: () => renderer.render(scene, camera) };
}
// 나무를 한 방향에서 찍은 사진(투명 배경 캔버스). 빌보드와 임포스터의 재료다.
let snapper;
function snap(S, yaw, pitch = 0.12, size = 384) {
  if (!snapper) {
    const canvas = document.createElement('canvas');
    const renderer = new T.WebGLRenderer({ canvas, antialias: true, alpha: true, preserveDrawingBuffer: true });
    const scene = new T.Scene(); lights(scene);
    const tree = makeTree(S, 0); scene.add(tree);
    snapper = { renderer, scene, camera: new T.PerspectiveCamera(30, 1, 0.1, 100) };
  }
  const { renderer, scene, camera } = snapper;
  renderer.setSize(size, size, false); renderer.setClearColor(0x000000, 0);
  const R = 13;
  camera.position.set(Math.sin(yaw) * Math.cos(pitch) * R, 3.0 + Math.sin(pitch) * R, Math.cos(yaw) * Math.cos(pitch) * R);
  camera.lookAt(0, 3.0, 0);
  renderer.render(scene, camera);
  const out = document.createElement('canvas'); out.width = out.height = size;
  out.getContext('2d').drawImage(renderer.domElement, 0, 0);
  return out;
}
function card(S, tex, { frame = false } = {}) {
  const g = new T.Group();
  const m = new T.Mesh(new T.PlaneGeometry(6.4, 6.4), new T.MeshBasicMaterial({ map: tex, transparent: true, alphaTest: 0.35, side: T.DoubleSide }));
  m.position.y = 3.0; g.add(m);
  if (frame) {
    const e = new T.LineSegments(new T.EdgesGeometry(new T.PlaneGeometry(6.4, 6.4)), new T.LineBasicMaterial({ color: 0x34dbff }));
    e.position.y = 3.0; g.add(e);
    const bg = new T.Mesh(new T.PlaneGeometry(6.4, 6.4), new T.MeshBasicMaterial({ color: 0x34dbff, transparent: true, opacity: 0.12, side: T.DoubleSide, depthWrite: false }));
    bg.position.set(0, 3.0, -0.01); g.add(bg);
  }
  return g;
}
const tex = (canvas) => { const t = new T.CanvasTexture(canvas); t.colorSpace = T.SRGBColorSpace; return t; };
function ground(scene, size = 300, color = 0x8fc49a) {
  const m = new T.Mesh(new T.PlaneGeometry(size, size), new T.MeshLambertMaterial({ color }));
  m.rotation.x = -Math.PI / 2; scene.add(m); return m;
}
function gizmo() { // 카메라 모양 표식
  const g = new T.Group(), mat = new T.MeshLambertMaterial({ color: 0xf3c04a, flatShading: true });
  const body = new T.Mesh(new T.BoxGeometry(0.9, 0.7, 1.2), mat);
  const lens = new T.Mesh(new T.ConeGeometry(0.45, 0.8, 4), mat); lens.rotation.x = -Math.PI / 2; lens.position.z = -0.9; lens.rotation.y = Math.PI / 4;
  g.add(body, lens); return g;
}

// ── 화면 위 글자 요소
const pill = (S, root, text, { x, y, color = '#fff', size = 46, anchor = 'center' }) =>
  S.el('div', { text, style: `position:absolute;left:${x}px;top:${y}px;transform:translate(${anchor === 'center' ? '-50%' : '0'},-50%);padding:12px 26px;border-radius:999px;background:rgba(0,0,0,.78);color:${color};font-weight:800;font-size:${size}px;white-space:nowrap;visibility:hidden` }, root);
const sticker = (S, root, text, { x, y, size = 120, rot = -10 }) =>
  S.el('div', { text, 'data-rot': rot, style: `position:absolute;left:${x}px;top:${y}px;font-weight:900;font-size:${size}px;color:#1E6BFF;-webkit-text-stroke:14px #BFE9FF;paint-order:stroke fill;text-shadow:0 10px 0 rgba(0,0,0,.35);white-space:nowrap;visibility:hidden` }, root);
// at 부터 튀어나오듯 나타난다.
function pop(S, node, t, at, { until = Infinity, center = true } = {}) {
  const on = t >= at && t < until; S.show(node, on); if (!on) return;
  const k = S.ease.back(S.prog(t, at, at + 0.22)), rot = node.dataset.rot || 0;
  node.style.transform = `${center && !node.dataset.rot ? 'translate(-50%,-50%) ' : ''}scale(${k}) rotate(${rot}deg)`;
}
const overlay = (S, root) => S.svg('svg', { viewBox: `0 0 ${S.W} ${S.H}`, style: 'position:absolute;inset:0;width:100%;height:100%' }, root);
const marker = (S, o, color = '#E20322') => [0, 1].map(() => S.svg('path', { fill: 'none', stroke: color, 'stroke-width': 11, 'stroke-linecap': 'round', 'stroke-linejoin': 'round', visibility: 'hidden' }, o));
function circle(S, paths, t, at, cx, cy, rx, ry, until = Infinity) {
  paths.forEach((p, i) => {
    const on = t >= at && t < until; p.setAttribute('visibility', on ? 'visible' : 'hidden');
    if (on) p.setAttribute('d', S.wobble(cx, cy, rx + i * 9, ry + i * 7, { t, seed: 11 + i * 5, amp: 4 }));
  });
}

export default {
  // 멀리 숲이 보인다 → "종이" 에서 먼 나무들이 옆으로 돌며 납작한 판임이 드러난다.
  hook: {
    async setup(root, S, info) {
      T = await S.three();
      const v = stage3d(root, S, { bg: C.sky, fov: 40 });
      v.scene.fog = new T.Fog(0xa4d6df, 60, 170);
      ground(v.scene);
      const r = S.rng(21), photo = tex(snap(S, 0.4));
      const near = [[-5.5, 6], [6, 3], [-9, -6]].map(([x, z], i) => { const tr = makeTree(S, 1, 5 + i); tr.position.set(x, 0, z); tr.scale.setScalar(1.25); v.scene.add(tr); return tr; });
      const far = [];
      for (let i = 0; i < 46; i++) {
        const c = card(S, photo); c.position.set((r() - 0.5) * 120, 0, -48 - r() * 55); c.scale.setScalar(1.1 + r() * 0.7);
        v.scene.add(c); far.push(c);
      }
      const o = overlay(S, root);
      root._ = { v, far, near, mk: marker(S, o), arrow: S.svg('path', { fill: 'none', stroke: '#E20322', 'stroke-width': 12, 'stroke-linecap': 'round', visibility: 'hidden', d: S.arrow(170, 760, 330, 560) }, o), st: sticker(S, root, '종이?!', { x: 560, y: 180, size: 150 }) };
    },
    frame(root, t, info, S) {
      const { v, far, mk, arrow, st } = root._, flip = info.when('종이', 2.4);
      v.camera.position.set(0, 4.2 - t * 0.12, 26 - t * 1.6); v.camera.lookAt(0, 4.4, -40);
      const k = S.ease.inOut(S.prog(t, flip, flip + 0.9));
      far.forEach((c, i) => { c.rotation.y = k * (1.45 + (i % 5) * 0.02) * (i % 2 ? 1 : -1); });
      v.draw();
      circle(S, mk, t, 0.55, 540, 470, 330, 130);
      arrow.setAttribute('visibility', t >= 0.55 ? 'visible' : 'hidden');
      pop(S, st, t, flip + 0.25);
    },
  },

  // 정교한 나무 한 그루가 돌고, 그물(삼각형)이 드러나며 개수가 올라간다.
  detail: {
    async setup(root, S) {
      T = await S.three();
      const v = stage3d(root, S, { fov: 30 });
      const tree = makeTree(S, 0); v.scene.add(tree);
      const wire = makeTree(S, 0);
      wire.traverse((o) => { if (o.isMesh) o.material = new T.MeshBasicMaterial({ color: 0x34dbff, wireframe: true, transparent: true, opacity: 0 }); });
      wire.scale.setScalar(1.004); v.scene.add(wire);
      const count = S.el('div', { style: 'position:absolute;left:60px;top:56px;font-size:54px;font-weight:800;color:#9fb6bb' , html: '삼각형 <b style="display:block;font-size:150px;font-weight:900;color:#34DBFF;line-height:1.05;font-variant-numeric:tabular-nums">0</b>' }, root);
      root._ = { v, tree, wire, num: count.querySelector('b'), count };
    },
    frame(root, t, info, S) {
      const { v, tree, wire, num, count } = root._, a = info.when('삼각형', 2.4), b = info.when('들어가죠', a + 1.6);
      v.camera.position.set(0, 4.3, 17.5); v.camera.lookAt(0, 2.5, 0);
      tree.rotation.y = wire.rotation.y = t * 0.55;
      const k = S.prog(t, a - 0.5, a + 0.3);
      wire.traverse((o) => { if (o.isMesh) o.material.opacity = k * 0.75; });
      S.show(count, t >= a - 0.5);
      num.textContent = fmt(tree.userData.tris * S.ease.out(S.prog(t, a - 0.3, b)));
      v.draw();
    },
  },

  // 같은 나무로 숲을 채우면 삼각형 합계가 치솟는다.
  forest: {
    async setup(root, S) {
      T = await S.three();
      const v = stage3d(root, S, { fov: 40 });
      ground(v.scene, 300, 0x12332c);
      const N = 400, per = makeTree(S, 0).userData.tris, r = S.rng(4);
      const leaf = new T.InstancedMesh(new T.IcosahedronGeometry(1.5, 1), new T.MeshLambertMaterial({ color: 0x49a58e, flatShading: true }), N);
      const trunk = new T.InstancedMesh(new T.CylinderGeometry(0.2, 0.3, 2.2, 6), new T.MeshLambertMaterial({ color: 0x7a5a3c }), N);
      const pos = [], m = new T.Matrix4();
      for (let i = 0; i < N; i++) pos.push([(i % 20 - 9.5) * 4.2 + (r() - 0.5) * 2, (Math.floor(i / 20) - 9.5) * 4.2 + (r() - 0.5) * 2, 0.8 + r() * 0.5]);
      // 가운데에서 바깥으로 퍼지며 심긴다
      pos.sort((p, q) => Math.hypot(p[0], p[1]) - Math.hypot(q[0], q[1]));
      v.scene.add(leaf, trunk);
      const hud = S.el('div', { style: 'position:absolute;left:0;top:0;right:0;padding:50px 60px 90px;background:linear-gradient(#071216 62%,transparent);font-weight:800;font-size:50px;color:#9fb6bb;line-height:1.25', html: '나무 <b id="n" style="color:#fff">1</b>그루 × <span id="per" style="color:#fff"></span><div style="font-size:42px;margin-top:6px">한 프레임에 그릴 삼각형</div><b id="tot" style="display:block;font-size:118px;font-weight:900;line-height:1.1;font-variant-numeric:tabular-nums"></b>' }, root);
      hud.querySelector('#per').textContent = fmt(per);
      root._ = { v, leaf, trunk, pos, m, N, per, n: hud.querySelector('#n'), tot: hud.querySelector('#tot') };
    },
    frame(root, t, info, S) {
      const { v, leaf, trunk, pos, m, N, per, n, tot } = root._;
      const k = S.ease.inOut(S.prog(t, 0.2, info.dur - 0.7)), count = Math.max(1, Math.round(1 + (N - 1) * k * k));
      for (let i = 0; i < N; i++) {
        const [x, z, s] = pos[i], on = i < count ? s : 0;
        leaf.setMatrixAt(i, m.makeScale(on, on * 1.35, on).setPosition(x, 3.2 * on, z));
        trunk.setMatrixAt(i, m.makeScale(on, on, on).setPosition(x, 1.1 * on, z));
      }
      leaf.instanceMatrix.needsUpdate = trunk.instanceMatrix.needsUpdate = true;
      const shake = S.prog(t, info.when('버티지', 2.6), info.dur) * 0.35, r = S.rng(Math.floor(t * 30) + 1);
      v.camera.position.set(0 + (r() - 0.5) * shake, 16 + k * 44 + (r() - 0.5) * shake, 26 + k * 34); v.camera.lookAt(0, 0, -4);
      v.draw();
      n.textContent = fmt(count); tot.textContent = fmt(count * per);
      tot.style.color = `rgb(${Math.round(S.lerp(52, 255, k))},${Math.round(S.lerp(219, 42, k))},${Math.round(S.lerp(255, 85, k))})`;
    },
  },

  // 나무가 멀어지며 덜 정교한 모델로 바뀐다 → 세 단계를 나란히 놓고 LOD 라고 부른다.
  lod: {
    async setup(root, S) {
      T = await S.three();
      const v = stage3d(root, S, { fov: 34 });
      ground(v.scene, 400, 0x12332c);
      const trees = [0, 1, 2].map((l) => { const tr = makeTree(S, l); v.scene.add(tr); return tr; });
      const hud = S.el('div', { style: 'position:absolute;left:60px;top:50px;font-weight:800;font-size:50px;color:#9fb6bb', html: '삼각형 <b style="display:block;font-size:130px;font-weight:900;color:#34DBFF;line-height:1.05;font-variant-numeric:tabular-nums"></b>' }, root);
      const tags = trees.map((tr, l) => S.el('div', { style: `position:absolute;left:${190 + l * 350}px;top:640px;transform:translateX(-50%);text-align:center;font-weight:900;font-size:64px;color:${[C.cyan, C.yellow, C.red][l]};visibility:hidden`, html: `LOD ${l}<div style="font-size:46px;color:#fff;font-weight:800;margin-top:4px">${fmt(tr.userData.tris)}개</div>` }, root));
      const flash = S.el('div', { style: 'position:absolute;inset:0;background:#fff;opacity:0' }, root);
      const name = S.el('div', { html: 'LOD <span style="font-size:52px;color:#fff;font-weight:800">Level of Detail</span>', style: 'position:absolute;left:540px;top:150px;transform:translate(-50%,-50%);padding:16px 40px;border-radius:999px;background:rgba(0,0,0,.78);color:#E4FF0A;font-weight:900;font-size:100px;white-space:nowrap;visibility:hidden' }, root);
      root._ = { v, trees, hud, num: hud.querySelector('b'), tags, flash, name };
    },
    frame(root, t, info, S) {
      const { v, trees, hud, num, tags, flash, name } = root._;
      const b1 = info.beat(1), s1 = info.when('멀어질수록', 1.6), s2 = info.when('몰래', 4.2);
      if (t < b1) { // 한 그루가 멀어지며 바뀐다
        const level = t < s1 ? 0 : t < s2 ? 1 : 2;
        trees.forEach((tr, l) => { tr.visible = l === level; tr.position.set(0, 0, 0); tr.rotation.y = 0.4; });
        const d = S.lerp(17, 62, S.ease.inOut(S.prog(t, 0.3, b1 - 0.2)));
        v.camera.position.set(0, 4.6, d); v.camera.lookAt(0, 2.9, 0);
        const since = Math.min(...[s1, s2].map((s) => (t >= s ? t - s : 9)));
        flash.style.opacity = since < 0.12 ? 0.35 * (1 - since / 0.12) : 0;
        num.textContent = fmt(trees[level].userData.tris); S.show(hud, true);
        tags.forEach((x) => S.show(x, false)); S.show(name, false);
      } else { // 세 단계를 나란히
        const u = t - b1; flash.style.opacity = 0; S.show(hud, false);
        trees.forEach((tr, l) => { tr.visible = u >= l * 0.18; tr.position.set((l - 1) * 6.6, 0, 0); tr.rotation.y = 0.4 + u * 0.35; });
        v.camera.position.set(0, 6.2, 30); v.camera.lookAt(0, 0.6, 0);
        tags.forEach((x, l) => S.show(x, u >= l * 0.18));
        pop(S, name, t, info.when('LOD', b1 + 2.2));
      }
      v.draw();
    },
  },

  // 사진 한 장(빌보드)이 카메라를 따라 돈다 → 먼 숲 전체가 같은 방식.
  billboard: {
    async setup(root, S) {
      T = await S.three();
      const v = stage3d(root, S, { fov: 36 });
      ground(v.scene, 400, 0x12332c);
      const photo = tex(snap(S, 0.4));
      const one = card(S, photo, { frame: true }); v.scene.add(one);
      const r = S.rng(9), many = [];
      for (let i = 0; i < 60; i++) { const c = card(S, photo, { frame: true }); c.position.set((r() - 0.5) * 70, 0, -8 - r() * 46); c.scale.setScalar(0.6 + r() * 0.3); c.visible = false; v.scene.add(c); many.push(c); }
      const cam = gizmo(); v.scene.add(cam);
      const line = new T.Line(new T.BufferGeometry().setFromPoints([new T.Vector3(), new T.Vector3()]), new T.LineDashedMaterial({ color: 0xf3c04a, dashSize: 0.5, gapSize: 0.35 }));
      v.scene.add(line);
      root._ = { v, one, many, cam, line, name: pill(S, root, '빌보드', { x: 540, y: 130, color: C.lime, size: 100 }), photoTag: pill(S, root, '사진 한 장', { x: 540, y: 130, color: C.cyan, size: 60 }), game: pill(S, root, '오블리비언 (2006) · 방식 재현', { x: 50, y: 70, color: '#fff', size: 40, anchor: 'left' }) };
    },
    frame(root, t, info, S) {
      const { v, one, many, cam, line, name, photoTag, game } = root._, b1 = info.beat(1), b2 = info.beat(2);
      const wide = S.ease.inOut(S.prog(t, b2, b2 + 0.8));
      // 표식 카메라가 판 둘레를 왔다 갔다 한다
      const a = Math.sin(t * 0.9) * 1.25, R = S.lerp(12, 22, wide);
      cam.position.set(Math.sin(a) * R, 2.2, Math.cos(a) * R + S.lerp(0, 6, wide)); cam.lookAt(0, 2.6, S.lerp(0, -20, wide));
      const face = (c) => { c.rotation.y = Math.atan2(cam.position.x - c.position.x, cam.position.z - c.position.z); };
      face(one); one.visible = wide < 0.5;
      many.forEach((c) => { c.visible = wide >= 0.5; face(c); });
      line.geometry.setFromPoints([cam.position.clone(), new T.Vector3(0, 3, S.lerp(0, -20, wide))]); line.computeLineDistances(); line.visible = true;
      v.camera.position.set(S.lerp(9, 0, wide), S.lerp(15, 30, wide), S.lerp(32, 50, wide)); v.camera.lookAt(0, 2.0, S.lerp(2, -12, wide));
      v.draw();
      pop(S, photoTag, t, info.when('사진', 1.4), { until: b1 });
      pop(S, name, t, info.when('빌보드', b1 + 1.0), { until: b2 });
      pop(S, game, t, b2 + 0.15, { center: false });
    },
  },

  // 위로 올라가 내려다보면 판이 선 하나로 보인다.
  flat: {
    async setup(root, S) {
      T = await S.three();
      const v = stage3d(root, S, { fov: 34 });
      ground(v.scene, 400, 0x12332c);
      const c = card(S, tex(snap(S, 0.4)), { frame: true }); v.scene.add(c);
      const o = overlay(S, root);
      root._ = { v, c, mk: marker(S, o), st: sticker(S, root, '납작!', { x: 600, y: 150, size: 150 }) };
    },
    frame(root, t, info, S) {
      const { v, mk, st } = root._, up = info.when('위에서', 1.4), hit = info.when('납작한', 3.0);
      const k = S.ease.inOut(S.prog(t, up - 0.2, hit - 0.1)), ang = S.lerp(0.18, 1.52, k), R = 19;
      v.camera.position.set(0, 3 + Math.sin(ang) * R, Math.cos(ang) * R); v.camera.lookAt(0, S.lerp(3, 0.5, k), 0);
      v.draw();
      circle(S, mk, t, hit, 540, 545, 250, 70);
      pop(S, st, t, hit + 0.15);
    },
  },

  // 여러 방향에서 찍어 둔 사진 가운데, 보는 각도에 맞는 것을 고른다.
  impostor: {
    async setup(root, S) {
      T = await S.three();
      const v = stage3d(root, S, { fov: 36, w: 680, h: 860 });
      ground(v.scene, 400, 0x12332c);
      const N = 8, shots = Array.from({ length: N }, (_, i) => snap(S, (i / N) * Math.PI * 2, 0.12, 256));
      const tree = makeTree(S, 0); v.scene.add(tree);
      const texs = shots.map(tex), board = card(S, texs[0], { frame: true }); v.scene.add(board);
      const cam = gizmo(); v.scene.add(cam);
      const flash = new T.PointLight(0xffffff, 0, 60); v.scene.add(flash);
      const grid = S.el('div', { style: 'position:absolute;left:700px;top:60px;width:350px;display:grid;grid-template-columns:1fr 1fr;gap:14px' }, root);
      const cells = shots.map((c) => { const d = S.el('div', { style: 'height:168px;border-radius:14px;background:#0d2a2e;border:5px solid #1c4a50;overflow:hidden;display:flex;align-items:center;justify-content:center' }, grid); c.style.cssText = 'width:150px;height:150px;visibility:hidden'; d.appendChild(c); return d; });
      root._ = { v, tree, board, texs, cam, flash, cells, shots, N, name: pill(S, root, '임포스터', { x: 340, y: 110, color: C.lime, size: 96 }), game: pill(S, root, '포트나이트', { x: 340, y: 780, color: '#fff', size: 52 }) };
    },
    frame(root, t, info, S) {
      const { v, tree, board, texs, cam, flash, cells, shots, N, name, game } = root._;
      const swap = info.when('보는', 4.2), R = 13;
      // 앞부분: 표식 카메라가 한 바퀴 돌며 여덟 장을 찍는다. 뒷부분: 각도에 맞는 사진을 고른다.
      const shooting = t < swap, turn = shooting ? S.prog(t, 0.2, swap - 0.3) * (N - 0.01) / N : 0.06 + (t - swap) * 0.13;
      const yaw = turn * Math.PI * 2, idx = Math.round((((yaw / (Math.PI * 2)) % 1) + 1) % 1 * N) % N;
      cam.position.set(Math.sin(yaw) * R, 3.4, Math.cos(yaw) * R); cam.lookAt(0, 3, 0);
      tree.visible = shooting; board.visible = !shooting;
      board.children[0].material.map = texs[idx]; board.rotation.y = yaw;
      const taken = shooting ? Math.floor(turn * N + 0.5) : N - 1;
      const sinceShot = shooting ? (turn * N + 0.5) % 1 : 1;
      flash.position.copy(cam.position); flash.intensity = shooting && sinceShot < 0.2 ? 260 * (1 - sinceShot / 0.2) : 0;
      cells.forEach((d, i) => {
        shots[i].style.visibility = i <= taken ? 'visible' : 'hidden';
        const on = !shooting && i === idx;
        d.style.borderColor = on ? '#E4FF0A' : '#1c4a50'; d.style.background = on ? '#24484a' : '#0d2a2e';
      });
      v.camera.position.set(9, 15, 25); v.camera.lookAt(0, 2.4, 0);
      v.draw();
      pop(S, name, t, info.when('임포스터', info.beat(1) + 0.8));
      pop(S, game, t, info.when('포트나이트', info.beat(1) + 2.6));
    },
  },

  // 사진에서 진짜 모델로: 한 번에 바꾸면 툭 튄다 → 점점이 섞어 가며 바꾼다.
  dither: {
    async setup(root, S) {
      T = await S.three();
      const real = snap(S, 0.4, 0.12, 512), low = document.createElement('canvas'); low.width = low.height = 512;
      const tiny = document.createElement('canvas'); tiny.width = tiny.height = 72; tiny.getContext('2d').drawImage(real, 0, 0, 72, 72);
      const g = low.getContext('2d'); g.imageSmoothingEnabled = false; g.drawImage(tiny, 0, 0, 512, 512);
      const cv = S.el('canvas', { width: 512, height: 512, style: 'position:absolute;left:150px;top:70px;width:780px;height:780px;image-rendering:pixelated' }, root);
      root._ = { real, low, ctx: cv.getContext('2d'), a: pill(S, root, '사진', { x: 200, y: 110, color: C.cyan, size: 56 }), b: pill(S, root, '진짜 모델', { x: 860, y: 110, color: C.lime, size: 56 }), st: sticker(S, root, '툭!', { x: 640, y: 380, size: 170 }) };
      S.show(root._.a, true); S.show(root._.b, true);
    },
    frame(root, t, info, S) {
      const { real, low, ctx, a, b, st } = root._, hit = info.when('툭', 2.8), mix = info.when('점점이', 4.2);
      const BAYER = [0, 8, 2, 10, 12, 4, 14, 6, 3, 11, 1, 9, 15, 7, 13, 5], B = 8;
      // 툭: 한 프레임에 통째로 바뀐다. 그 뒤 사진으로 돌아가 점점이 섞인다.
      const p = t < hit ? 0 : t < mix - 0.35 ? 1 : S.prog(t, mix, info.dur - 0.5);
      const hard = t < mix - 0.35;
      ctx.clearRect(0, 0, 512, 512); ctx.drawImage(low, 0, 0);
      if (hard) { if (p) { ctx.clearRect(0, 0, 512, 512); ctx.drawImage(real, 0, 0); } }
      else for (let y = 0; y < 512; y += B) for (let x = 0; x < 512; x += B) {
        if ((BAYER[((y / B) % 4) * 4 + ((x / B) % 4)] + 0.5) / 16 < p) { ctx.clearRect(x, y, B, B); ctx.drawImage(real, x, y, B, B, x, y, B, B); }
      }
      a.style.opacity = 1 - p * 0.75; b.style.opacity = 0.25 + p * 0.75;
      pop(S, st, t, hit, { until: mix - 0.35 });
    },
  },

  // 숲 위를 날며 마무리: 가까이는 모델, 멀리는 사진.
  outro: {
    async setup(root, S) {
      T = await S.three();
      const v = stage3d(root, S, { bg: C.sky, fov: 42 });
      v.scene.fog = new T.Fog(0xa4d6df, 70, 210); ground(v.scene, 600);
      const r = S.rng(33), photo = tex(snap(S, 0.4));
      for (let i = 0; i < 26; i++) { const tr = makeTree(S, 1, 40 + i); tr.position.set((r() - 0.5) * 70, 0, 10 - r() * 60); tr.scale.setScalar(1 + r() * 0.5); v.scene.add(tr); }
      for (let i = 0; i < 60; i++) { const tr = makeTree(S, 2, 80 + i); tr.position.set((r() - 0.5) * 130, 0, -55 - r() * 50); tr.scale.setScalar(1 + r() * 0.5); v.scene.add(tr); }
      const cards = [];
      for (let i = 0; i < 150; i++) { const c = card(S, photo); c.position.set((r() - 0.5) * 260, 0, -110 - r() * 90); c.scale.setScalar(1 + r() * 0.6); v.scene.add(c); cards.push(c); }
      root._ = { v, cards, tags: [['모델', C.lime, 760], ['LOD', C.yellow, 560], ['빌보드', C.cyan, 400]].map(([s, c, y]) => pill(S, root, s, { x: 930, y, color: c, size: 46 })) };
    },
    frame(root, t, info, S) {
      const { v, cards, tags } = root._;
      v.camera.position.set(Math.sin(t * 0.25) * 4, 9 + t * 0.5, 46 - t * 4.2); v.camera.lookAt(0, 4, -80);
      cards.forEach((c) => { c.rotation.y = Math.atan2(v.camera.position.x - c.position.x, v.camera.position.z - c.position.z); });
      v.draw();
      tags.forEach((x, i) => pop(S, x, t, 0.5 + i * 0.35));
    },
  },
};
