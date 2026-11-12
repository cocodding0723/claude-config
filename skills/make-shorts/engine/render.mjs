#!/usr/bin/env node
// 프로젝트 폴더의 장면을 프레임 단위로 캡처해 무음 mp4 로 만든다.
//   node render.mjs <프로젝트> [--fps 30] [--workers 4] [--out build/video.mp4]
//   node render.mjs <프로젝트> --stills 0.5,3,12.2     → build/check/still_*.png (빠른 눈 확인용)
//   node render.mjs <프로젝트> --sheet 12                → build/check/sheet.jpg (고르게 뽑은 12칸 타일 한 장)
//   node render.mjs <프로젝트> --check                  → 자막 넘침·줄 수, 쌓인 자료 넘침, 못 찾은 낱말 검사만
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { spawn, spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright-core';

const here = path.dirname(fileURLToPath(import.meta.url));
const argv = process.argv.slice(2);
const proj = path.resolve(argv[0] || '.');
const opt = (k, d) => { const i = argv.indexOf('--' + k); return i < 0 ? d : (argv[i + 1] && !argv[i + 1].startsWith('--') ? argv[i + 1] : true); };
const fps = Number(opt('fps', 30));
const workers = Number(opt('workers', Math.max(2, Math.min(6, os.cpus().length - 2))));
const out = path.resolve(proj, opt('out', 'build/video.mp4'));

const TYPES = { '.html': 'text/html', '.js': 'text/javascript', '.mjs': 'text/javascript', '.json': 'application/json', '.css': 'text/css', '.woff2': 'font/woff2', '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp', '.svg': 'image/svg+xml', '.mp4': 'video/mp4', '.glb': 'model/gltf-binary' };
const server = http.createServer((req, res) => {
  const u = decodeURIComponent(new URL(req.url, 'http://x').pathname);
  const [base, rel] = u.startsWith('/project/') ? [proj, u.slice(9)] : [path.join(here, 'runtime'), u.slice(1) || 'index.html'];
  const f = path.join(base, rel);
  if (!f.startsWith(base) || !fs.existsSync(f) || fs.statSync(f).isDirectory()) { res.writeHead(404); return res.end('없음: ' + u); }
  res.writeHead(200, { 'Content-Type': TYPES[path.extname(f)] || 'application/octet-stream', 'Cache-Control': 'no-store' });
  fs.createReadStream(f).pipe(res);
});
// 영상 자료는 재생하지 않고 프레임 이미지로 풀어 둔다. 그래야 어느 시각이든 같은 그림이 나온다.
{
  const script = JSON.parse(fs.readFileSync(path.join(proj, 'script.json'), 'utf8'));
  const meta = {}, root = path.join(proj, 'build/clips');
  for (const sc of Object.values(script.scenes || {})) for (const it of sc.items || []) {
    if (!it.clip) continue;
    const from = typeof it.from === 'number' ? it.from : 0, dur = typeof it.dur === 'number' ? it.dur : 8;
    const key = `${it.clip}|${from}|${dur}`, name = key.replace(/[^0-9A-Za-z가-힣.-]+/g, '_'), dir = path.join(root, name);
    if (!fs.existsSync(path.join(dir, 'f_00001.jpg'))) {
      fs.mkdirSync(dir, { recursive: true });
      const r = spawnSync('ffmpeg', ['-y', '-loglevel', 'error', '-ss', String(from), '-t', String(dur), '-i', path.join(proj, it.clip), '-vf', "fps=30,scale='min(1080,iw)':-2", '-q:v', '3', path.join(dir, 'f_%05d.jpg')], { stdio: 'inherit' });
      if (r.status) { console.error('영상 자료를 못 풀었다: ' + it.clip); process.exit(1); }
    }
    meta[key] = { dir: '/project/build/clips/' + encodeURIComponent(name), n: fs.readdirSync(dir).filter((f) => f.startsWith('f_')).length, fps: 30 };
  }
  fs.mkdirSync(path.join(proj, 'build'), { recursive: true });
  fs.mkdirSync(root, { recursive: true }); fs.writeFileSync(path.join(root, 'meta.json'), JSON.stringify(meta));
}
await new Promise((r) => server.listen(0, '127.0.0.1', r));
const url = `http://127.0.0.1:${server.address().port}/index.html`;

function findBrowser() {
  if (process.env.SHORTS_CHROME) return { executablePath: process.env.SHORTS_CHROME };
  const cache = path.join(os.homedir(), 'Library/Caches/ms-playwright');
  if (fs.existsSync(cache)) {
    for (const d of fs.readdirSync(cache).filter((x) => x.startsWith('chromium_headless_shell-')).sort().reverse()) {
      const sub = fs.readdirSync(path.join(cache, d)).find((x) => x.startsWith('chrome-'));
      for (const name of ['chrome-headless-shell', 'headless_shell']) {
        const exe = sub && path.join(cache, d, sub, name);
        if (exe && fs.existsSync(exe)) return { executablePath: exe };
      }
    }
  }
  return { channel: 'chrome' };
}
// angle 은 3D(WebGL) 장면을 헤드리스에서 GPU 로 그리게 한다.
const browser = await chromium.launch({ ...findBrowser(), args: ['--use-angle=metal', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist', '--font-render-hinting=none'] });

async function openPage() {
  const page = await browser.newPage({ viewport: { width: 1080, height: 1920 }, deviceScaleFactor: 1 });
  page.on('pageerror', (e) => console.error('[페이지 오류]', e.message));
  page.on('console', (m) => { if (m.type() === 'error' && !m.text().includes('404')) console.error('[콘솔]', m.text()); });
  await page.goto(url);
  const ok = await page.evaluate(() => window.__ready);
  if (!ok) { console.error(await page.evaluate(() => window.__error)); await browser.close(); server.close(); process.exit(1); }
  return page;
}
const done = async (code) => { await browser.close(); server.close(); process.exit(code); };

fs.mkdirSync(path.join(proj, 'build/check'), { recursive: true });

if (opt('check', false)) {
  const page = await openPage();
  const meta = await page.evaluate(() => window.__meta);
  let bad = 0;
  for (const c of meta.captions) {
    await page.evaluate((t) => window.__seek(t), (c.start + c.end) / 2);
    const b = await page.evaluate(() => window.__capBox());
    const why = [];
    if (b.x < 30 || b.x + b.w > 1050) why.push('가로 넘침');
    if (b.lines > 2) why.push(b.lines + '줄');
    if (c.end - c.start < 0.35) why.push('너무 짧게 뜸(' + (c.end - c.start).toFixed(2) + '초)');
    if (why.length) { bad++; console.log(`✗ ${c.start.toFixed(2)}초 "${c.text.replace(/\n/g, ' / ')}" — ${why.join(', ')}`); }
  }
  const extra = await page.evaluate(() => window.__overflow());
  for (const o of extra.overflow) { bad++; console.log(`✗ 장면 ${o.id}: 쌓인 자료가 영역을 ${o.px}px 넘는다 — h 를 줄이거나 clear 로 비운다`); }
  for (const w of [...new Set(extra.warn)]) { bad++; console.log('✗ ' + w); }
  console.log(bad ? `문제 ${bad}건` : meta.captions.length ? `자막 ${meta.captions.length}개 모두 통과` : '이 모드는 문장 자막이 없다');
  await done(bad ? 1 : 0);
}

const sheet = opt('sheet', null);
if (sheet) {
  const page = await openPage();
  const { duration } = await page.evaluate(() => window.__meta);
  const n = sheet === true ? 12 : Number(sheet), cols = Math.min(6, n);
  const f = path.join(proj, 'build/check/sheet.jpg');
  const ff = spawn('ffmpeg', ['-y', '-loglevel', 'error', '-f', 'image2pipe', '-c:v', 'mjpeg', '-i', '-', '-vf', `scale=270:-1,tile=${cols}x${Math.ceil(n / cols)}`, '-frames:v', '1', f], { stdio: ['pipe', 'inherit', 'inherit'] });
  const times = [];
  for (let i = 0; i < n; i++) {
    const t = (i + 0.5) * duration / n; times.push(t.toFixed(1));
    await page.evaluate((x) => window.__seek(x), t);
    ff.stdin.write(await page.screenshot({ type: 'jpeg', quality: 85 }));
  }
  ff.stdin.end(); await new Promise((r) => ff.on('close', r));
  console.log(f + '\n칸 시각(초): ' + times.join(' '));
  await done(0);
}

const stills = opt('stills', null);
if (stills) {
  const page = await openPage();
  for (const s of String(stills).split(',')) {
    const t = Number(s);
    await page.evaluate((x) => window.__seek(x), t);
    const f = path.join(proj, 'build/check', `still_${t.toFixed(2).padStart(6, '0')}.png`);
    await page.screenshot({ path: f });
    console.log(f);
  }
  await done(0);
}

const probe = await openPage();
const { duration } = await probe.evaluate(() => window.__meta);
await probe.close();
const total = Math.ceil(duration * fps);
const per = Math.ceil(total / workers);
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'shorts-'));
const t0 = Date.now();
let rendered = 0;
const tick = setInterval(() => process.stderr.write(`\r${rendered}/${total} 프레임`), 1000);

await Promise.all(Array.from({ length: workers }, async (_, w) => {
  const a = w * per, b = Math.min(total, a + per);
  if (a >= b) return;
  const seg = path.join(tmp, `seg${String(w).padStart(2, '0')}.mp4`);
  const ff = spawn('ffmpeg', ['-y', '-loglevel', 'error', '-f', 'image2pipe', '-framerate', String(fps), '-c:v', 'mjpeg', '-i', '-', '-c:v', 'libx264', '-preset', 'medium', '-crf', '17', '-pix_fmt', 'yuv420p', '-color_range', 'tv', '-vf', 'scale=in_range=full:out_range=tv', seg], { stdio: ['pipe', 'inherit', 'inherit'] });
  const closed = new Promise((r) => ff.on('close', r));
  const page = await openPage();
  for (let f = a; f < b; f++) {
    await page.evaluate((t) => window.__seek(t), f / fps);
    const buf = await page.screenshot({ type: 'jpeg', quality: 96 });
    if (!ff.stdin.write(buf)) await new Promise((r) => ff.stdin.once('drain', r));
    rendered++;
  }
  ff.stdin.end();
  await closed;
  await page.close();
}));
clearInterval(tick);

const list = path.join(tmp, 'list.txt');
fs.writeFileSync(list, fs.readdirSync(tmp).filter((f) => f.startsWith('seg')).sort().map((f) => `file '${path.join(tmp, f)}'`).join('\n'));
fs.mkdirSync(path.dirname(out), { recursive: true });
const r = spawnSync('ffmpeg', ['-y', '-loglevel', 'error', '-f', 'concat', '-safe', '0', '-i', list, '-c', 'copy', '-movflags', '+faststart', out], { stdio: 'inherit' });
fs.rmSync(tmp, { recursive: true, force: true });
console.error(`\n${total}프레임 ${((Date.now() - t0) / 1000).toFixed(1)}초 → ${out}`);
await done(r.status || 0);
