// 패널의 순수 함수: 파일 해석·오래됨 판정·표시 문자열. 엔진($) 없이 node 로 시험함(tests/panel/board.test.mjs)
import type { AutoBoard, AutoCard, AutoConfirm, AutoHub, AutoIdle, AutoProgress, AutoSource, AutoWorkBoard, BoardItem } from '../types'

/** 자동 칸이 이만큼 넘게 갱신되지 않으면 맨 위 ⚠️. 수집 주기 60초 + 한 회차 시간의 여유 */
export const AUTO_STALE_MIN = 3
/** 확인 목록 원료가 이만큼 갱신되지 않으면 낡은 것으로 보고 작업판 승인 카드를 앞세움 */
export const CONFIRM_STALE_MIN = 10
/** 파일 안 시각이 지금보다 이만큼 앞서면 시계가 틀린 것으로 보고 수정 시각을 씀 */
const FUTURE_SLACK_MS = 2 * 60 * 1000

export const STATE_LABEL: Record<string, string> = {
  busy: '작업 중',
  approval: '승인창',
  idle: '대기',
  unknown: '확인불가',
}

/** panel-auto.json 해석. 모양이 다르면 예외 */
export const parseAuto = (text: string): AutoBoard => {
  const d = JSON.parse(text) as AutoBoard
  if (d === null || typeof d !== 'object' || typeof d.updatedAt !== 'number' || d.sources === null || typeof d.sources !== 'object') {
    throw new Error('자동 칸 형식 아님')
  }
  return d
}

/**
 * 자동 칸의 기준 시각(ms). 파일 안 epoch(수집기가 쓰는 순간의 시계)를 먼저 쓰고,
 * 없거나 미래면 파일 수정 시각. 사람이 쓰는 글자 시각은 실제와 어긋난 전례가 있어 쓰지 않음
 */
export const autoStamp = (auto: AutoBoard | undefined, mtimeMs: number, nowMs: number): number | undefined => {
  // writtenAt(쓴 순간)이 있으면 그것, 옛 파일은 updatedAt(회차 시작)
  const sec = auto?.writtenAt !== undefined && Number.isFinite(auto.writtenAt) ? auto.writtenAt : auto?.updatedAt
  const epochMs = sec !== undefined && Number.isFinite(sec) ? sec * 1000 : undefined
  if (epochMs !== undefined && epochMs > 0 && epochMs <= nowMs + FUTURE_SLACK_MS) return epochMs
  return mtimeMs > 0 ? mtimeMs : undefined
}

export const ageMin = (stampMs: number | undefined, nowMs: number): number | undefined =>
  stampMs === undefined ? undefined : Math.max(0, Math.floor((nowMs - stampMs) / 60000))

/** 맨 위 나이 표시용 초 단위 */
export const fmtAgeSec = (stampMs: number | undefined, nowMs: number): string => {
  if (stampMs === undefined) return '알 수 없음'
  const s = Math.max(0, Math.floor((nowMs - stampMs) / 1000))
  if (s < 60) return `${s}초 전`
  return s < 3600 ? `${Math.floor(s / 60)}분 ${s % 60}초 전` : fmtAge(Math.floor(s / 60))
}

export const fmtAge = (min: number | undefined): string => {
  if (min === undefined) return '알 수 없음'
  if (min < 1) return '방금'
  if (min < 60) return `${min}분 전`
  const h = Math.floor(min / 60)
  return h < 48 ? `${h}시간 ${min % 60}분 전` : `${Math.floor(h / 24)}일 전`
}

export type Banner = { level: 'ok' | 'stale' | 'missing' | 'broken'; text: string }

/** 맨 위 데이터 나이. 파일 없음·깨짐·AUTO_STALE_MIN 초과는 ⚠️ */
export const autoBanner = (
  auto: AutoBoard | undefined,
  autoMtimeMs: number,
  autoError: string | undefined,
  nowMs: number,
): Banner => {
  if (autoMtimeMs === 0) return { level: 'missing', text: '⚠️ 자동 수집 파일 없음 — launchd ai.lrm.panelcollect 확인' }
  if (auto === undefined) return { level: 'broken', text: `⚠️ 자동 수집 파일을 못 읽음${autoError !== undefined ? ` (${autoError})` : ''}` }
  const stamp = autoStamp(auto, autoMtimeMs, nowMs)
  const age = fmtAgeSec(stamp, nowMs)
  if (stamp !== undefined && nowMs - stamp > AUTO_STALE_MIN * 60000) {
    return { level: 'stale', text: `⚠️ 데이터 ${age} — 자동 수집 멈춤(launchd ai.lrm.panelcollect 확인)` }
  }
  return { level: 'ok', text: `데이터 ${age}` }
}

/** 원료 꼬리표: 실패면 오류 종류와 남아 있는 값의 나이 */
export const sourceNote = <T>(src: AutoSource<T> | undefined, nowMs: number): string => {
  if (src === undefined) return '자동 · 없음'
  if (src.off === true) return '자동 · 설정 없음'
  if (src.ok) return '자동'
  const age = src.at !== null ? fmtAge(ageMin(src.at * 1000, nowMs)) : '값 없음'
  return `자동 · ⚠ 수집 실패(${src.err ?? '?'}) · ${src.at !== null ? `${age} 값` : age}`
}

export const hubLine = (h: AutoHub, ws?: { children: number; active: number; warn: boolean }): string => {
  const parts = [STATE_LABEL[h.state] ?? h.state]
  if (h.quietMin !== null && h.quietMin >= 1) parts.push(`${h.quietMin}분 출력 없음`)
  if (h.dup) parts.push('👥중복')
  if (!h.connected) parts.push('🔌끊김')
  if (ws !== undefined && ws.children > 0) parts.push(`하위 ${ws.children}개(활동 ${ws.active})${ws.warn ? ' ⚠️' : ''}`)
  return parts.join(' · ')
}

const PENDING_STATE: Record<string, string> = { hold: '보류', conflict: '선택이 여러 개, 확인 필요' }

/** ⏳ 결정 대기 한 줄. 번호 카드는 결정 번호와 보류·충돌 상태를 붙임 */
export const pendingLine = (p: import('../types').AutoPendingItem): string => {
  const head = p.id ? `${p.title} · ${p.id}` : p.title
  const state = p.state ? ` — ${PENDING_STATE[p.state] ?? p.state}` : ''
  return `${head}${state}${p.ageMin !== null ? `  · ${fmtAge(p.ageMin)}` : ''}`
}

/** 🔀 PR 줄. gh 가 --limit 만큼 꽉 차서 더 있을 수 있으면 숫자 뒤에 + */
export const ghLine = (g: { open: number; ciFail: number; capped?: boolean }): string =>
  `열린 PR ${g.open}${g.capped === true ? '+' : ''} · CI 실패 ${g.ciFail}`

/** 맨 위 숫자: 허브 작업 중·승인창 수. 자동 허브 값이 없으면 undefined */
export const headCounts = (auto: AutoBoard | undefined): { busy: number; blocked: number } | undefined => {
  const hubs = auto?.sources.hubs?.value
  return hubs === undefined || hubs === null ? undefined : { busy: hubs.counts.busy, blocked: hubs.counts.approval }
}

/** 쉬는 자원 칸에서 이름을 보여 줄 오래 쉰 워크스페이스 수 */
const IDLE_NAMES = 3

export type IdleLine = { text: string; warn: boolean }

/** 😴 쉬는 자원 칸 줄. 값이 없는 부분은 건너뜀(칸이 비어도 패널은 그대로) */
export const idleLines = (v: AutoIdle | null | undefined): IdleLine[] => {
  if (v === null || v === undefined) return []
  const out: IdleLine[] = []
  if (v.workspaces) {
    const w = v.workspaces
    out.push({ text: `터미널 없는 워크스페이스 ${w.noTerminal}/${w.total}${w.truncated ? ' · 목록 잘림' : ''}`, warn: false })
    const names = w.items.slice(0, IDLE_NAMES).map(i => `${i.name}${i.ageDays !== null ? ` ${i.ageDays}일` : ''}${i.merged === true ? ' 병합됨' : ''}`)
    const rest = w.noTerminal - names.length
    if (names.length > 0) out.push({ text: `오래 쉰 것: ${names.join(', ')}${rest > 0 ? ` 외 ${rest}개` : ''}`, warn: false })
  }
  if (v.workers) {
    const w = v.workers
    // 쪽 상한에 걸려 전체를 못 셌으면 하한임을 표시(옛 파일은 complete 가 없어 표시 안 함)
    const part = w.complete === false ? ` (${w.total ?? '?'}건 중 ${w.seen ?? '?'}건만 셈)` : ''
    out.push({ text: `종료 뒤 남은 터미널 ${w.leftover} · 살아 있는 워커 ${w.live}${part}`, warn: false })
  }
  if (v.disk) {
    const d = v.disk
    out.push({ text: `디스크 여유 ${d.freeGB}GB (${d.usedPct}% 사용)${d.warn ? ` ⚠ ${d.minGB}GB 미만` : ''}`, warn: d.warn })
  }
  if (v.terminals) out.push({ text: `터미널 ${v.terminals.total}개 (에이전트 ${v.terminals.agents})`, warn: false })
  if (v.wsSize) {
    const s = v.wsSize
    const note = s.err === 'timeout' ? '측정 시간 초과' : s.err ? `측정 실패(${s.err})` : ''
    if (s.gb !== null) out.push({ text: `${s.dir} ${s.gb}GB${note ? ` · ⚠ ${note}, 이전 값` : ''}`, warn: false })
    else if (note) out.push({ text: `${s.dir} 크기 ⚠ ${note}`, warn: false })
  }
  return out
}

// 진행 게이지 한 줄: 막대·퍼센트·남은 시각. 값이 깨져도(0·음수·NaN) 0~100% 로 자름
export const progressBar = (p: { done: number; total: number; eta?: string; state?: string }, width = 12) => {
  const total = Number.isFinite(p.total) && p.total > 0 ? p.total : 0
  const done = Number.isFinite(p.done) ? Math.min(Math.max(p.done, 0), total) : 0
  const pct = total === 0 ? 0 : Math.round((done / total) * 100)
  const fill = Math.round((pct / 100) * width)
  const bar = '█'.repeat(fill) + '░'.repeat(width - fill)
  const tail = p.state === 'done' ? '완료' : p.state === 'blocked' ? `막힘${p.eta ? ` · ${p.eta}` : ''}` : (p.eta ?? '')
  return { bar, pct, text: `${bar} ${pct}% ${done}/${total}${tail ? `  ${tail}` : ''}` }
}

export const BUCKET_LABEL: Record<string, string> = { doing: '🔄 진행 중', wait: '⏳ 답 대기', block: '🚧 막힘' }
const BUCKET_ORDER = ['doing', 'wait', 'block'] as const

export type ProgressGroup = { group: string; lines: string[] }

/**
 * 진행 칸: 허브(그룹)별로 묶고 줄 앞에 묶음 표시. 그룹 순서는 처음 나온 순.
 * 값이 없으면 undefined → 호출한 쪽이 칸을 숨김
 */
export const progressGroups = (v: AutoProgress | null | undefined): ProgressGroup[] | undefined => {
  if (v === null || v === undefined || !Array.isArray(v.items)) return undefined
  const groups: ProgressGroup[] = []
  for (const bucket of BUCKET_ORDER) {
    for (const it of v.items.filter(i => i.bucket === bucket)) {
      let g = groups.find(x => x.group === it.group)
      if (g === undefined) {
        g = { group: it.group, lines: [] }
        groups.push(g)
      }
      g.lines.push(`${BUCKET_LABEL[bucket]?.split(' ')[0] ?? ''} ${it.title}`)
    }
  }
  return groups
}

/**
 * '내가 승인할 것' 칸: 확인 목록(수집기의 confirm 원료) 안 함 항목. 급함 3 → p 1.
 * 원료가 없으면 undefined. 작업판 승인 카드와의 합치기는 mergeApprovals
 */
export const confirmApprovals = (v: AutoConfirm | null | undefined): BoardItem[] | undefined => {
  if (v === null || v === undefined || !Array.isArray(v.items)) return undefined
  return v.items.map(i => ({
    key: i.key,
    p: Math.max(1, 4 - (Number.isFinite(i.urgency) ? i.urgency : 1)),
    title: i.hub !== '' && i.hub !== '미상' ? `${i.title} (${i.hub})` : i.title,
    why: i.kind_label,
    how: i.how,
  }))
}

/** 제목 비교용: 공백 정리, 끝의 ' (허브)' 꼬리표 제거 */
const titleKey = (t: string) => t.replace(/\s+/g, ' ').trim().replace(/ \([^()]*\)$/, '')

export type ApprovalMerge = { items: BoardItem[]; /** 확인 목록 원료가 낡음 → 작업판 카드를 앞세웠음 */ stale: boolean }

/**
 * '내가 승인할 것' 칸 합치기: 확인 목록 원료 항목 + 제목·키가 겹치지 않는 작업판 승인 카드(hand).
 * 겹치면 원료 항목에 카드의 why·detail 을 붙임. 원료가 없거나 빈 배열이면 카드만.
 * 원료가 낡았으면(CONFIRM_STALE_MIN 초과·시각 없음) 카드를 앞세우고 겹치는 건 카드 것을 씀. 낡은 원료 항목에는 ⚠ 표시
 */
export const mergeApprovals = (
  src: AutoSource<AutoConfirm> | undefined,
  hand: BoardItem[] | undefined,
  nowMs: number,
): ApprovalMerge => {
  const handItems = hand ?? []
  const auto = confirmApprovals(src?.value)
  if (auto === undefined || auto.length === 0) return { items: handItems, stale: false }
  const min = src?.at !== null && src?.at !== undefined ? ageMin(src.at * 1000, nowMs) : undefined
  const stale = min === undefined || min > CONFIRM_STALE_MIN
  const same = (a: BoardItem, h: BoardItem) =>
    (a.key !== undefined && a.key === h.key) || titleKey(a.title) === titleKey(h.title)
  if (stale) {
    const rest = auto.filter(a => !handItems.some(h => same(a, h))).map(a => ({ ...a, why: `${a.why ?? ''} · ⚠ 낡음` }))
    return { items: [...handItems, ...rest], stale }
  }
  const merged = auto.map(a => {
    const h = handItems.find(x => same(a, x))
    if (h === undefined) return a
    return {
      ...a,
      why: h.why === undefined ? a.why : a.why === undefined ? h.why : `${a.why} · ${h.why}`,
      detail: h.detail ?? a.detail,
    }
  })
  return { items: [...merged, ...handItems.filter(h => !auto.some(a => same(a, h)))], stale }
}

const ageOf = (sec: number | null | undefined, nowMs: number) =>
  sec === null || sec === undefined ? undefined : ageMin(sec * 1000, nowMs)

/** 진행 중 카드가 이만큼 갱신(heartbeat) 없으면 ⚠ */
export const BEAT_STALE_MIN = 30

/** 🔄 진행 줄: id 제목 · 담당 · 경과 · 마지막 신호. 신호가 BEAT_STALE_MIN 넘으면 warn */
export const boardLines = (v: AutoWorkBoard | null | undefined, nowMs = 0): Array<{ text: string; warn: boolean }> | undefined => {
  if (v === null || v === undefined) return undefined
  const out = v.doing.map(d => {
    const parts = [`${d.id} ${d.title}`]
    if (d.who) parts.push(d.who)
    const run = ageOf(d.startedAt, nowMs)
    if (run !== undefined) parts.push(`경과 ${fmtAge(run).replace(/ 전$/, '')}`)
    const beat = ageOf(d.beatAt, nowMs)
    if (beat !== undefined) parts.push(`신호 ${fmtAge(beat)}`)
    const warn = beat !== undefined && beat > BEAT_STALE_MIN
    return { text: `${parts.join(' · ')}${warn ? ' ⚠' : ''}`, warn }
  })
  if (v.more > 0) out.push({ text: `외 ${v.more}개`, warn: false })
  return out
}

/** 🚧 막힘 줄 */
export const blockedLines = (v: AutoWorkBoard | null | undefined): string[] =>
  (v?.blocked ?? []).map(d => `${d.id} ${d.title}${d.who ? ` · ${d.who}` : ''}`)

export const ANSWER_HOW: Record<string, string> = {
  decision: 'board-answer <id> "<답 원문>"',
  todo: '끝나면 bd close <id>',
  approval: 'board-answer <id> "<승인·거절 원문>"',
}

/** 결정·할 일·승인 카드 → 카드 칸 항목. p 는 bd 우선순위(0 최우선)+1, 설명은 줄마다 detail */
export const cardItems = (cards: AutoCard[] | undefined, kind: keyof typeof ANSWER_HOW, nowMs: number): BoardItem[] =>
  (cards ?? []).map(c => {
    const age = ageOf(c.createdAt, nowMs)
    const why = [c.id, c.who ?? undefined, age !== undefined ? fmtAge(age) : undefined, c.comments > 0 ? `코멘트 ${c.comments}` : undefined]
      .filter((x): x is string => x !== undefined).join(' · ')
    return {
      key: `board:${c.id}`,
      p: (Number.isFinite(c.p) ? c.p : 2) + 1,
      title: c.title,
      why,
      detail: (c.desc ?? '').split(/\n+/).map(l => l.trim()).filter(l => l !== ''),
      how: (ANSWER_HOW[kind] ?? '').replace('<id>', c.id),
    }
  })

/** 맨 위 헤드라인: 최우선 열린 epic 제목과 진척 */
export const headlineText = (v: AutoWorkBoard | null | undefined): string | undefined => {
  const h = v?.headline
  if (h === null || h === undefined) return undefined
  const more = h.epics > 1 ? ` · 열린 epic ${h.epics}` : ''
  return `🎯 ${h.title} (${h.done}/${h.total})${more}`
}

/** 🧾 최근 닫힌 카드 줄 */
export const recentLines = (v: AutoWorkBoard | null | undefined, nowMs: number): string[] =>
  (v?.recent ?? []).map(r => {
    const age = ageOf(r.at, nowMs)
    return `✅ ${r.title}${age !== undefined ? `  · ${fmtAge(age)}` : ''}`
  })
