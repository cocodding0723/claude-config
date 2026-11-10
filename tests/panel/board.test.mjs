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

test('headCounts: 자동 허브 값 우선, 없으면 메인 칸 hubs', () => {
  const a = auto(1, { sources: { hubs: { ok: true, err: null, at: 1, stale: false, value: { rows: [], counts: { busy: 2, approval: 1, idle: 3, unknown: 0 } } } } })
  const main = { hubs: [{ name: 'x', state: 'busy' }, { name: 'y', state: 'blocked' }, { name: 'z', state: 'waiting' }] }
  assert.deepEqual(b.headCounts(main, a), { busy: 2, blocked: 1, source: 'auto' })
  assert.deepEqual(b.headCounts(main, undefined), { busy: 1, blocked: 2, source: 'main' })
  assert.deepEqual(b.headCounts(undefined, undefined), { busy: 0, blocked: 0, source: 'main' })
})
