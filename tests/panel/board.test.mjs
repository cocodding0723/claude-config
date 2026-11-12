// lrm-board mod 의 순수 함수 시험. node 내장 러너·타입 제거만 사용(의존성 없음): node --test tests/panel/board.test.mjs
// BOARD_TS=<경로> 로 변형 사본을 시험할 수 있음
import { test } from 'node:test'
import assert from 'node:assert/strict'
import path from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const here = path.dirname(fileURLToPath(import.meta.url))
const target = process.env.BOARD_TS ?? path.join(here, '../../hubs/mods/lrm-board/hooks/board.ts')
const b = await import(pathToFileURL(target).href)

const NOW = Date.UTC(2026, 10, 10, 12, 0, 0)
const sec = ms => Math.floor(ms / 1000)
const auto = (ageMinutes, extra = {}) => ({ version: 1, updatedAt: sec(NOW - ageMinutes * 60000), sources: {}, ...extra })

test('parseAuto: 형식 확인, 깨진 JSON·모양 다름은 예외', () => {
  assert.equal(b.parseAuto(JSON.stringify(auto(1))).version, 1)
  assert.throws(() => b.parseAuto('{"version": 1, "sour'))
  assert.throws(() => b.parseAuto('{"version": 1}'))
  assert.throws(() => b.parseAuto('null'))
})

test('parseMain: 객체만 받음', () => {
  assert.deepEqual(b.parseMain('{"approvals":[]}'), { approvals: [] })
  assert.throws(() => b.parseMain('[]'))
})

test('autoBanner: 파일 없음·깨짐·10분 초과는 경고, 10분 이하는 정상', () => {
  assert.equal(b.autoBanner(undefined, 0, undefined, NOW).level, 'missing')
  assert.equal(b.autoBanner(undefined, NOW, 'SyntaxError', NOW).level, 'broken')
  assert.equal(b.autoBanner(auto(10), NOW, undefined, NOW).level, 'ok')
  const stale = b.autoBanner(auto(11), NOW, undefined, NOW)
  assert.equal(stale.level, 'stale')
  assert.match(stale.text, /⚠️ 자동 수집이 11분째 멈춤/)
})

test('autoStamp: 파일 안 epoch 우선(수정 시각이 최근이어도 epoch 가 오래되면 멈춤)', () => {
  // 복사·백업 복원으로 수정 시각만 새로워진 경우: 데이터 자체는 30분 전 수집
  assert.equal(b.autoBanner(auto(30), NOW, undefined, NOW).level, 'stale')
  assert.equal(b.autoStamp(auto(30), NOW, NOW), NOW - 30 * 60000)
})

test('autoStamp: epoch 가 없거나 미래(시계 이상)면 수정 시각', () => {
  const mtime = NOW - 3 * 60000
  assert.equal(b.autoStamp({ version: 1, updatedAt: Number.NaN, sources: {} }, mtime, NOW), mtime)
  assert.equal(b.autoStamp(auto(-60), mtime, NOW), mtime)
  assert.equal(b.autoStamp(auto(-1), mtime, NOW), NOW + 60000)
  assert.equal(b.autoStamp(undefined, 0, NOW), undefined)
})

test('mainAge: 수정 시각 기준 30분 초과면 old', () => {
  assert.equal(b.mainAge(NOW - 30 * 60000, NOW).old, false)
  assert.equal(b.mainAge(NOW - 31 * 60000, NOW).old, true)
  assert.equal(b.mainAge(0, NOW).old, true)
})

test('fmtAge: 방금·분·시간·일', () => {
  assert.equal(b.fmtAge(0), '방금')
  assert.equal(b.fmtAge(5), '5분 전')
  assert.equal(b.fmtAge(125), '2시간 5분 전')
  assert.equal(b.fmtAge(60 * 72), '3일 전')
  assert.equal(b.fmtAge(undefined), '알 수 없음')
})

test('sourceNote: 실패 원료는 오류 종류와 남은 값의 나이', () => {
  assert.equal(b.sourceNote({ ok: true, err: null, at: sec(NOW), stale: false, value: {} }, NOW), '자동')
  assert.equal(b.sourceNote({ ok: true, err: null, at: sec(NOW), stale: false, value: null, off: true }, NOW), '자동 · 설정 없음')
  assert.equal(b.sourceNote({ ok: false, err: 'timeout', at: sec(NOW - 7 * 60000), stale: true, value: {} }, NOW), '자동 · ⚠ 수집 실패(timeout) · 7분 전 값')
  assert.equal(b.sourceNote({ ok: false, err: 'exit', at: null, stale: true, value: null }, NOW), '자동 · ⚠ 수집 실패(exit) · 값 없음')
})

test('hubLine: 상태·출력 없음·중복·끊김', () => {
  assert.equal(b.hubLine({ hub: 'coord-a-hub', name: 'a', agent: 'claude', state: 'busy', quietMin: 0, connected: true, dup: false }), '작업 중')
  assert.equal(b.hubLine({ hub: 'coord-a-hub', name: 'a', agent: 'claude', state: 'approval', quietMin: 12, connected: false, dup: true }), '승인창 · 12분 출력 없음 · 👥중복 · 🔌끊김')
})

test('pendingLine: 옛 카드는 제목·나이만, 번호 카드는 결정 번호·보류·충돌', () => {
  assert.equal(b.pendingLine({ title: '🙋 결정 A', ageMin: 3 }), '🙋 결정 A  · 3분 전')
  assert.equal(b.pendingLine({ title: '배포 순서', ageMin: null, id: 'D-0002', state: 'hold' }), '배포 순서 · D-0002 — 보류')
  assert.equal(b.pendingLine({ title: '문서 위치', ageMin: 0, id: 'D-0003', state: 'conflict' }), '문서 위치 · D-0003 — 선택이 여러 개, 확인 필요  · 방금')
  assert.equal(b.pendingLine({ title: '알림', ageMin: null, id: 'D-0004', state: null }), '알림 · D-0004')
})

test('ghLine: --limit 만큼 꽉 차면 숫자 뒤에 +', () => {
  assert.equal(b.ghLine({ repos: [], open: 8, ciFail: 4 }), '열린 PR 8 · CI 실패 4')
  assert.equal(b.ghLine({ repos: [], open: 100, ciFail: 0, capped: true }), '열린 PR 100+ · CI 실패 0')
})

test('headCounts: 자동 허브 값 우선, 없으면 메인 칸 hubs', () => {
  const a = auto(1, { sources: { hubs: { ok: true, err: null, at: 1, stale: false, value: { rows: [], counts: { busy: 2, approval: 1, idle: 3, unknown: 0 } } } } })
  const main = { hubs: [{ name: 'x', state: 'busy' }, { name: 'y', state: 'blocked' }, { name: 'z', state: 'waiting' }] }
  assert.deepEqual(b.headCounts(main, a), { busy: 2, blocked: 1, source: 'auto' })
  assert.deepEqual(b.headCounts(main, undefined), { busy: 1, blocked: 2, source: 'main' })
  assert.deepEqual(b.headCounts(undefined, undefined), { busy: 0, blocked: 0, source: 'main' })
})

test('idleLines: 값 없음·부분 null 이어도 깨지지 않음, 디스크 경고 표시', () => {
  assert.deepEqual(b.idleLines(undefined), [])
  assert.deepEqual(b.idleLines(null), [])
  assert.deepEqual(b.idleLines({ terminals: null, workers: null, workspaces: null, disk: null, wsSize: null }), [])
  const v = {
    terminals: { total: 36, agents: 18 },
    workers: { live: 7, succeeded: 86, failed: 2, stopped: 5, leftover: 1, more: 0, items: [] },
    workspaces: {
      total: 217, noTerminal: 197, more: 177, truncated: false,
      items: [
        { name: 'ws-a', path: '~/a', branch: 'feat/a', ageDays: 49, merged: true },
        { name: 'ws-b', path: '~/b', branch: 'feat/b', ageDays: null, merged: null },
        { name: 'ws-c', path: '~/c', branch: 'feat/c', ageDays: 3, merged: false },
        { name: 'ws-d', path: '~/d', branch: 'feat/d', ageDays: 1, merged: false },
      ],
    },
    disk: { freeGB: 27.2, usedPct: 97, minGB: 50, warn: true },
    wsSize: { gb: null, at: null, dir: '~/orca/workspaces' },
  }
  const lines = b.idleLines(v)
  assert.deepEqual(lines.map(l => l.text), [
    '터미널 없는 워크스페이스 197/217',
    '오래 쉰 것: ws-a 49일 병합됨, ws-b, ws-c 3일 외 194개',
    '종료 뒤 남은 터미널 1 · 살아 있는 워커 7',
    '디스크 여유 27.2GB (97% 사용) ⚠ 50GB 미만',
    '터미널 36개 (에이전트 18)',
  ])
  assert.deepEqual(lines.map(l => l.warn), [false, false, false, true, false])
  const ok = b.idleLines({ ...v, disk: { ...v.disk, freeGB: 80, warn: false }, wsSize: { gb: 12.5, at: 1, dir: '~/orca/workspaces' } })
  assert.equal(ok.some(l => l.warn), false)
  assert.equal(ok.at(-1).text, '~/orca/workspaces 12.5GB')
})

test('idleLines: 워커를 끝까지 못 셌으면 하한 표시, 목록 잘림, du 시간 초과는 이전 값과 함께 표시', () => {
  const base = { terminals: null, disk: null, wsSize: null, workspaces: null }
  const w = { live: 7, succeeded: 1, failed: 0, stopped: 0, leftover: 256, more: 236, items: [] }
  assert.equal(b.idleLines({ ...base, workers: { ...w, seen: 6000, total: 7000, complete: false } })[0].text, '종료 뒤 남은 터미널 256 · 살아 있는 워커 7 (7000건 중 6000건만 셈)')
  assert.equal(b.idleLines({ ...base, workers: { ...w, seen: 2034, total: 2034, complete: true } })[0].text, '종료 뒤 남은 터미널 256 · 살아 있는 워커 7')
  const ws = { total: 9, noTerminal: 1, more: 0, truncated: true, items: [] }
  assert.equal(b.idleLines({ ...base, workers: null, workspaces: ws })[0].text, '터미널 없는 워크스페이스 1/9 · 목록 잘림')
  const dir = '~/orca/workspaces'
  assert.equal(b.idleLines({ ...base, workers: null, wsSize: { gb: 12.5, at: 1, dir, err: 'timeout' } })[0].text, '~/orca/workspaces 12.5GB · ⚠ 측정 시간 초과, 이전 값')
  assert.equal(b.idleLines({ ...base, workers: null, wsSize: { gb: null, at: null, dir, err: 'timeout' } })[0].text, '~/orca/workspaces 크기 ⚠ 측정 시간 초과')
  assert.deepEqual(b.idleLines({ ...base, workers: null, wsSize: { gb: null, at: null, dir, err: null } }), [])
})

test('progressBar: 퍼센트·막대·상태 꼬리, 깨진 값은 0~100% 로 자름', () => {
  assert.equal(b.progressBar({ done: 5, total: 9, eta: '내일 15:00', state: 'on' }, 10).text, '██████░░░░ 56% 5/9  내일 15:00')
  assert.equal(b.progressBar({ done: 1, total: 5, eta: '오늘 밤', state: 'blocked' }, 10).text, '██░░░░░░░░ 20% 1/5  막힘 · 오늘 밤')
  assert.equal(b.progressBar({ done: 1, total: 1, state: 'done' }, 4).text, '████ 100% 1/1  완료')
  assert.equal(b.progressBar({ done: 3, total: 0 }, 4).pct, 0)
  assert.equal(b.progressBar({ done: 9, total: 3 }, 4).pct, 100)
  assert.equal(b.progressBar({ done: NaN, total: 3 }, 4).bar, '░░░░')
})

test('progressGroups: 허브별로 묶고 묶음 표시, 값이 없으면 undefined(옛 손 칸으로 복귀)', () => {
  assert.equal(b.progressGroups(undefined), undefined)
  assert.equal(b.progressGroups(null), undefined)
  const g = b.progressGroups({
    counts: { doing: 2, wait: 1, block: 0 },
    notes: [],
    items: [
      { key: 'a', bucket: 'wait', title: 'ui: 병합 대기', group: 'ui', link: '' },
      { key: 'b', bucket: 'doing', title: 'ui: 버튼', group: 'ui', link: '' },
      { key: 'c', bucket: 'doing', title: 'F1 로그인', group: '장부', link: '' },
    ],
  })
  assert.deepEqual(g.map(x => x.group), ['ui', '장부'])
  assert.deepEqual(g[0].lines, ['🔄 ui: 버튼', '⏳ ui: 병합 대기'])
})

test('hubLine: 하위 작업 공간 수, 10개 넘으면 경고', () => {
  const h = { hub: 'coord-a-hub', name: 'a', agent: 'claude', state: 'busy', quietMin: 0, connected: true, dup: false }
  assert.equal(b.hubLine(h, { children: 3, active: 1, warn: false }), '작업 중 · 하위 3개(활동 1)')
  assert.equal(b.hubLine(h, { children: 12, active: 2, warn: true }), '작업 중 · 하위 12개(활동 2) ⚠️')
  assert.equal(b.hubLine(h, { children: 0, active: 0, warn: false }), '작업 중')
  assert.equal(b.hubLine(h), '작업 중')
})
