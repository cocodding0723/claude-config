// 패널의 순수 함수: 파일 해석·오래됨 판정·표시 문자열. 엔진($) 없이 node 로 시험함(tests/panel/board.test.mjs)
import type { AutoAgents, AutoAi, AutoBoard, AutoCard, AutoConfirm, AutoFeature, AutoHub, AutoIdle, AutoKid, AutoProgress, AutoSource, AutoWorkBoard, BoardItem, EpicView } from '../types'

/**
 * 패널 설정 한 곳(11-17). 칸 순서·임계 시간·알림 상한은 여기만 고친다.
 * order: 위에서부터 그릴 칸. problem 은 항목이 있을 때만 보임
 */
export const PANEL_CONFIG = {
  order: ['problem', 'ask', 'todo', 'ai'] as const,
  /** 수집 시각이 이만큼 지나면 머리 줄 빨강(수집 주기 60초의 3배) */
  staleMin: 3,
  /** 진행 중 기능이 이만큼 변화 없으면 '멈춤 의심'으로 문제 칸에 */
  stuckMin: 15,
  /** 끝난 기능을 '안 봄'으로 보여 주는 시간 */
  doneShowMin: 60,
  /** AI 진행 줄이 이보다 많으면 정상 작업 중인 기능을 한 줄로 접음 */
  aiFoldMax: 8,
  /** AI 진행 칸 워커 줄 상한(도는 워커 먼저). 넘친 수는 '외 N개' 한 줄 */
  workerRows: 6,
  /** 펼친 에픽 하나가 그리는 하위 카드 줄 상한. 넘친 수는 '외 N개' 한 줄 */
  epicKidsMax: 5,
  /** 하위 줄 제목 글자 수 상한(넘으면 …). 패널 폭을 렌더에서 알 수 없어 고정값으로 한 줄을 지킴 */
  kidTitleMax: 16,
  /** 하위 줄 담당 이름 글자 수 상한(넘으면 …). 긴 이름 하나가 폭 40 에서 줄을 3줄로 꺾는 것을 막음 */
  kidWhoMax: 12,
  /** 진행 중 카드의 마지막 heartbeat 가 이만큼 지나면 '오래됨' 표시. 오래됨 기준은 여기 한 곳 */
  beatStaleMin: 30,
  /** 진행률 막대 칸 수 */
  barCells: 8,
  /** 데스크톱 알림: 시간당 상한, 같은 항목 다시 알림 간격 */
  notifyPerHour: 5,
  renotifyMin: 30,
} as const

export type SectionId = (typeof PANEL_CONFIG.order)[number]

/** 자동 칸이 이만큼 넘게 갱신되지 않으면 맨 위 빨강 */
export const AUTO_STALE_MIN = PANEL_CONFIG.staleMin
/** 확인 목록 원료가 이만큼 갱신되지 않으면 낡은 것으로 보고 작업판 결정 카드를 앞세움 */
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

/** 승인 성격 결정 앞에 붙는 배지. 승인 칸을 따로 두지 않고 결정 대기 한 칸에서 이 배지로만 구분(11-17) */
export const APPROVAL_BADGE = '✋ 승인'

/**
 * 확인 목록(수집기의 confirm 원료) 안 함 항목 → 결정 대기 칸 항목(승인 배지). 급함 3 → p 1.
 * 원료가 없으면 undefined. 작업판 결정 카드와의 합치기는 mergeApprovals
 */
export const confirmApprovals = (v: AutoConfirm | null | undefined): BoardItem[] | undefined => {
  if (v === null || v === undefined || !Array.isArray(v.items)) return undefined
  return v.items.map(i => ({
    key: i.key,
    p: Math.max(1, 4 - (Number.isFinite(i.urgency) ? i.urgency : 1)),
    title: `${APPROVAL_BADGE} ${i.hub !== '' && i.hub !== '미상' ? `${i.title} (${i.hub})` : i.title}`,
    why: i.kind_label,
    how: i.how,
  }))
}

/** 제목 비교용: 공백 정리, 앞의 승인 배지·끝의 ' (허브)' 꼬리표 제거 */
const titleKey = (t: string) => t.replace(/\s+/g, ' ').trim().replace(APPROVAL_BADGE, '').trim().replace(/ \([^()]*\)$/, '')

export type ApprovalMerge = { items: BoardItem[]; /** 확인 목록 원료가 낡음 → 작업판 카드를 앞세웠음 */ stale: boolean }

/**
 * 결정 대기 칸 합치기: 확인 목록 원료 항목 + 제목·키가 겹치지 않는 작업판 결정 카드(hand).
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
export const BEAT_STALE_MIN = PANEL_CONFIG.beatStaleMin

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
}
const APPROVAL_HOW = 'board-answer <id> "<승인·보류 원문>"'

/** 결정·할 일 카드 → 카드 칸 항목. p 는 bd 우선순위(0 최우선)+1, 설명은 줄마다 detail. gate:approval 결정은 제목 앞에 승인 배지 */
export const cardItems = (cards: AutoCard[] | undefined, kind: keyof typeof ANSWER_HOW, nowMs: number): BoardItem[] =>
  (cards ?? []).map(c => {
    const age = ageOf(c.createdAt, nowMs)
    const why = [c.id, c.who ?? undefined, age !== undefined ? fmtAge(age) : undefined, c.comments > 0 ? `코멘트 ${c.comments}` : undefined]
      .filter((x): x is string => x !== undefined).join(' · ')
    return {
      key: `board:${c.id}`,
      p: (Number.isFinite(c.p) ? c.p : 2) + 1,
      title: c.gate === 'approval' ? `${APPROVAL_BADGE} ${c.title}` : c.title,
      why,
      detail: (c.desc ?? '').split(/\n+/).map(l => l.trim()).filter(l => l !== ''),
      how: (c.gate === 'approval' ? APPROVAL_HOW : ANSWER_HOW[kind] ?? '').replace('<id>', c.id),
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


// ---- 11-17 4칸 패널: 🚨 문제 · 🙋 답할 것 · 🔧 직접 할 일 · 🔄 AI 진행 ----------------------------

/** 분 단위 경과(초 표시 없음 → 분이 바뀔 때만 글자가 바뀜) */
export const fmtMin = (sec: number | null | undefined, nowMs: number): string => {
  const m = ageOf(sec, nowMs)
  if (m === undefined) return '?'
  if (m < 1) return '방금'
  if (m < 60) return `${m}분`
  const h = Math.floor(m / 60)
  return h < 48 ? `${h}시간` : `${Math.floor(h / 24)}일`
}

export type Line = { key: string; text: string; sub?: string; warn?: boolean }

/** 🙋 답할 것: 막는 AI 수 많은 순 → 오래 기다린 순. 줄마다 'AI n곳 대기 m분' */
export const askLines = (cards: AutoCard[] | undefined, nowMs: number): Line[] =>
  [...(cards ?? [])]
    .sort((a, b) => (b.waiting ?? 0) - (a.waiting ?? 0) || (a.createdAt ?? 0) - (b.createdAt ?? 0))
    .map(c => ({
      key: `ask:${c.id}`,
      text: `${c.gate === 'approval' ? `${APPROVAL_BADGE} ` : ''}${c.title}`,
      sub: `${(c.waiting ?? 0) > 0 ? `AI ${c.waiting}곳 ` : ''}대기 ${fmtMin(c.createdAt, nowMs)} · 답: ${(c.gate === 'approval' ? APPROVAL_HOW : ANSWER_HOW.decision ?? '').replace('<id>', c.id)}`,
    }))

/** 🔧 직접 할 일: 사용자만 할 수 있는 카드, 오래된 순 */
export const todoLines = (cards: AutoCard[] | undefined, nowMs: number): Line[] =>
  [...(cards ?? [])]
    .sort((a, b) => (a.createdAt ?? 0) - (b.createdAt ?? 0))
    .map(c => ({ key: `todo:${c.id}`, text: c.title, sub: `${fmtMin(c.createdAt, nowMs)} · 끝나면 bd close ${c.id}` }))

export type AiMark = '?' | '!' | '✕' | '✻' | '✓' | '-'
export type FeatureLine = { key: string; mark: AiMark; state: string; text: string; age: string; order: number; folded?: boolean }

const MARK_ORDER: Record<AiMark, number> = { '?': 0, '!': 1, '✕': 2, '✻': 3, '✓': 4, '-': 5 }

/** 세션 상태 파일 중 이 기능 작업 공간(경로에 feat-<slug> 또는 /<slug>/) 것 */
const featureSessions = (f: AutoFeature, ai: AutoAi | null | undefined) =>
  f.slug === null || ai === null || ai === undefined ? [] :
    ai.sessions.filter(s => s.cwd.includes(`feat-${f.slug}`) || s.cwd.includes(`/${f.slug}/`) || s.cwd.endsWith(`/${f.slug}`))

/** 기능 하나의 상태. 입력 필요 → 멈춤 의심 → 막힘 → 작업 중 → 끝남(안 봄) → 쉼 */
export const featureState = (f: AutoFeature, ai: AutoAi | null | undefined, nowMs: number, cfg = PANEL_CONFIG): { mark: AiMark; state: string } => {
  if (f.closedAt !== null) return { mark: '✓', state: '끝남, 아직 안 봄' }
  const sess = featureSessions(f, ai)
  if (f.asks > 0 || sess.some(s => s.state === 'needs_input')) return { mark: '?', state: '답 기다림' }
  const quiet = ageOf(f.lastAt, nowMs)
  const sessQuiet = sess.length > 0 ? ageOf(Math.max(...sess.map(s => s.at)), nowMs) : undefined
  const busy = f.doingCount > 0 || sess.some(s => s.state === 'working')
  const idleMin = sessQuiet === undefined ? quiet : Math.min(quiet ?? sessQuiet, sessQuiet)
  if (busy && idleMin !== undefined && idleMin >= cfg.stuckMin) return { mark: '!', state: `변화 없음 ${idleMin}분, 멈춤 의심` }
  if (f.blocked > 0) return { mark: '✕', state: `막힘 ${f.blocked}` }
  if (busy) return { mark: '✻', state: '작업 중' }
  return { mark: '-', state: '쉬는 중' }
}

/** 🔄 AI 진행: 기능마다 한 줄(상태·이름·지금 하는 일·마지막 변화). 끝난 기능은 doneShowMin 동안만. 많으면 작업 중 접기 */
export const featureLines = (features: AutoFeature[] | undefined, ai: AutoAi | null | undefined, nowMs: number, cfg = PANEL_CONFIG): FeatureLine[] => {
  const rows = (features ?? [])
    .filter(f => f.closedAt === null || (ageOf(f.closedAt, nowMs) ?? Infinity) <= cfg.doneShowMin)
    .map(f => {
      const st = featureState(f, ai, nowMs, cfg)
      const now = f.doing[0]
      const what = st.mark === '✻' && now !== undefined ? `${now.title}${f.doingCount > 1 ? ` 외 ${f.doingCount - 1}` : ''}` : st.state
      return { key: `ai:${f.id}`, mark: st.mark, state: st.state, text: `${f.title} · ${what} (${f.done}/${f.total})`,
               age: fmtMin(f.closedAt ?? f.lastAt, nowMs), order: MARK_ORDER[st.mark] }
    })
    .sort((a, b) => a.order - b.order)
  if (rows.length <= cfg.aiFoldMax) return rows
  const working = rows.filter(r => r.mark === '✻')
  const rest = rows.filter(r => r.mark !== '✻')
  return [...rest.filter(r => r.order < MARK_ORDER['✻']),
    { key: 'ai:fold', mark: '✻', state: '작업 중', text: `작업 중 ${working.length}개 (펼치기: /board-detail)`, age: '', order: 3, folded: true },
    ...rest.filter(r => r.order > MARK_ORDER['✻'])]
}

/** AI 진행 머리 줄 개수 */
export const featureCounts = (lines: FeatureLine[]): string => {
  const n = (m: AiMark) => lines.filter(l => l.mark === m).length
  const work = lines.filter(l => l.mark === '✻').reduce((x, l) => x + (l.folded === true ? Number(/작업 중 (\d+)개/.exec(l.text)?.[1] ?? 0) : 1), 0)
  return [`입력 필요 ${n('?')}`, `작업 ${work}`, n('!') > 0 ? `멈춤 ${n('!')}` : '', n('✓') > 0 ? `끝남 ${n('✓')}` : ''].filter(x => x !== '').join(' / ')
}

export type WorkerLine = { key: string; mark: string; text: string; age: string; done: boolean }

const WORKER_MARK: Record<string, string> = { succeeded: '✅', failed: '❌', stopped: '⏹', abandoned: '⏹' }

/** 시:분(이 컴퓨터 시간대) */
const clock = (sec: number) => {
  const d = new Date(sec * 1000)
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

/**
 * 🔄 AI 진행 칸 워커 줄: 도는 워커(▶ 작업 공간 · 허브 · 모델, 경과) → 최근 끝난 워커(✅·❌·⏹, 끝난 시각).
 * 합쳐 workerRows 줄까지, 넘치면 마지막에 '외 N개'. 원료가 없으면 빈 배열
 */
export const workerLines = (v: AutoAgents | null | undefined, nowMs: number, cfg: { workerRows: number } = PANEL_CONFIG): WorkerLine[] => {
  if (v === null || v === undefined) return []
  const desc = (a: { name: string; hub: string | null; agent: string | null; model: string | null }) =>
    [a.name, a.hub, (a.model ?? a.agent ?? '').replace(/^claude-/, '')].filter(x => x !== null && x !== '').join(' · ')
  const run = v.running.map(a => ({ key: `w:${a.dispatch}`, mark: '▶', text: desc(a), age: a.startedAt ? `경과 ${fmtMin(a.startedAt, nowMs)}` : '작업 중', done: false }))
  const fin = v.finished.map(a => ({
    key: `w:${a.dispatch}`, mark: WORKER_MARK[a.status ?? ''] ?? '•', text: desc(a),
    age: a.finishedAt ? `${clock(a.finishedAt)} 끝` : '끝', done: true,
  }))
  const all = [...run, ...fin]
  const extra = v.runningMore
  if (all.length + extra <= cfg.workerRows) return all
  const shown = all.slice(0, cfg.workerRows - 1)
  return [...shown, { key: 'w:more', mark: '', text: `외 ${all.length - shown.length + extra}개 (/board-detail)`, age: '', done: true }]
}

/** 워커 줄 머리 글: 실행 n · m분 안 끝남 k(실패 f) */
export const workerCounts = (v: AutoAgents | null | undefined): string => {
  if (v === null || v === undefined) return ''
  const fail = v.finished.filter(a => a.status === 'failed').length
  return `워커 실행 ${v.running.length + v.runningMore} · ${v.windowMin}분 안 끝남 ${v.finished.length}${fail > 0 ? `(실패 ${fail})` : ''}`
}

/**
 * 🚨 문제: 있을 때만. 증상만 씀. 수집 끊김 → 멈춤 의심 → 막힌 카드 → CI 실패 → 디스크 부족 → 작업판 읽기 실패
 * gh-state.json(PR·CI 감시기)이 생기면 그쪽을 읽을 자리. 지금은 스키마 확정 전이라 기존 gh 원료의 CI 실패 수를 씀
 */
export const problemLines = (auto: AutoBoard | undefined, banner: Banner, features: FeatureLine[], nowMs: number): Line[] => {
  const out: Line[] = []
  if (banner.level !== 'ok') out.push({ key: `p:collect:${banner.level}`, text: banner.text.replace(/^⚠️\s*/, ''), warn: true })
  for (const f of features.filter(l => l.mark === '!')) out.push({ key: `p:stuck:${f.key}`, text: f.text, sub: '할 일: 그 기능 코디네이터에 상태 확인 또는 재시작', warn: true })
  const board = auto?.sources.board
  for (const c of board?.value?.blocked ?? []) out.push({ key: `p:blocked:${c.id}`, text: `막힌 카드: ${c.title}`, sub: `${c.who ?? '담당 없음'} · ${fmtMin(c.beatAt, nowMs)} · bd show ${c.id}` })
  const gh = auto?.sources.gh?.value
  if (gh !== null && gh !== undefined && gh.ciFail > 0) out.push({ key: `p:ci:${gh.ciFail}`, text: `PR CI 실패 ${gh.ciFail}건`, sub: gh.repos.filter(r => r.ciFail > 0).map(r => `${r.repo} ${r.ciFail}`).join(', ') })
  const disk = auto?.sources.idle?.value?.disk
  if (disk !== null && disk !== undefined && disk.warn) out.push({ key: 'p:disk', text: `디스크 여유 ${disk.freeGB}GB (${disk.minGB}GB 미만)`, warn: true })
  if (board !== undefined && !board.ok && board.off !== true) out.push({ key: `p:board:${board.err ?? '?'}`, text: `작업판 읽기 실패(${board.err ?? '?'}) — 결정·할 일이 낡았을 수 있음`, warn: true })
  return out
}

/** 맨 위 한 줄: 칸별 개수 + 수집 시각. 낡으면 stale=true(빨강) */
export const headerLine = (counts: { problem: number; ask: number; todo: number; ai: number }, banner: Banner, stampMs: number | undefined, nowMs: number) => {
  const m = stampMs === undefined ? undefined : Math.max(0, Math.floor((nowMs - stampMs) / 60000))
  const at = m === undefined ? '수집 시각 모름' : m < 1 ? '수집 1분 안' : `수집 ${m}분 전`
  return { text: `문제 ${counts.problem} / 답할 것 ${counts.ask} / 할 일 ${counts.todo} / AI ${counts.ai} · ${at}`, stale: banner.level !== 'ok' }
}

/**
 * 데스크톱 알림 고르기(🙋·🚨 새 항목만). seen: 항목 키 → 마지막 알림 ms, sent: 최근 알림 ms 목록.
 * 같은 키는 renotifyMin 안에 다시 보내지 않음, 한 시간에 notifyPerHour 를 넘으면 남은 것을 한 번에 묶음.
 * 반환: 보낼 글(없으면 빈 배열)과 갱신된 기록. 처음 그리는 회차(first)는 기록만 하고 보내지 않음(패널 열 때 몰아 울림 방지)
 */
export const pickNotify = (
  items: Array<{ key: string; text: string; head: string }>,
  seen: Record<string, number>,
  sent: number[],
  nowMs: number,
  first: boolean,
  cfg = PANEL_CONFIG,
): { send: Array<{ title: string; text: string }>; seen: Record<string, number>; sent: number[] } => {
  const hour = nowMs - 3600_000
  const keep = new Set(items.map(i => i.key))
  const nextSeen: Record<string, number> = {}
  for (const [k, v] of Object.entries(seen)) if (keep.has(k)) nextSeen[k] = v
  const recent = sent.filter(t => t > hour)
  const due = items.filter(i => { const t = seen[i.key]; return t === undefined || nowMs - t >= cfg.renotifyMin * 60000 })
  for (const i of due) nextSeen[i.key] = nowMs
  if (first || due.length === 0) return { send: [], seen: nextSeen, sent: recent }
  const room = Math.max(0, cfg.notifyPerHour - recent.length)
  if (room === 0) return { send: [], seen: nextSeen, sent: recent }
  const one = due.slice(0, room - 1)
  const rest = due.slice(room - 1)
  const send = [...one, ...(rest.length === 1 ? rest : [])].map(i => ({ title: i.head, text: i.text }))
  if (rest.length > 1) send.push({ title: 'LRM 현황', text: `새 항목 ${rest.length}개 — 패널 확인` })
  return { send, seen: nextSeen, sent: [...recent, ...send.map(() => nowMs)] }
}

/** /board-detail 글: 패널에서 뺀 참고 정보(허브·PR·쉬는 자원·최근 기록·진행 카드 담당·원료 상태) */
export const detailText = (auto: AutoBoard | undefined, nowMs: number): string => {
  if (auto === undefined) return '자동 수집 파일 없음'
  const src = auto.sources
  const out: string[] = []
  const feats = src.board?.value?.features ?? []
  out.push('## 기능별 진행 카드')
  for (const f of feats) {
    out.push(`- ${f.title} (${f.done}/${f.total}) ${f.id}`)
    for (const d of f.doing) out.push(`    - ${d.title} · ${d.who ?? '담당 없음'} · ${fmtMin(d.beatAt, nowMs)}`)
  }
  const hubs = src.hubs?.value
  if (hubs) {
    out.push('## 허브')
    for (const h of hubs.rows) out.push(`- ${h.name}: ${hubLine(h, src.progress?.value?.groups?.[h.name])}`)
  }
  if (src.gh?.value) out.push('## PR', `- ${ghLine(src.gh.value)}`)
  out.push(`- PR 감시기 gh-state.json: ${src.ghState?.ok === true ? '있음(읽는 자리만, 스키마 확정 전)' : '없음 → 기존 gh 원료 사용'}`)
  const idle = idleLines(src.idle?.value)
  if (idle.length > 0) out.push('## 쉬는 자원', ...idle.map(l => `- ${l.text}`))
  const recent = recentLines(src.board?.value, nowMs)
  if (recent.length > 0) out.push('## 최근 기록', ...recent.map(l => `- ${l}`))
  if (src.workers?.value) out.push(`- 살아 있는 워커 ${src.workers.value.live}`)
  const ag = src.agents?.value
  if (ag) {
    out.push(`## 워커 (${workerCounts(ag)})`)
    for (const l of workerLines(ag, nowMs, { workerRows: Infinity })) out.push(`- ${l.mark} ${l.text} · ${l.age}`)
  }
  out.push('## 원료 상태')
  for (const [k, v] of Object.entries(src)) out.push(`- ${k}: ${sourceNote(v as AutoSource<unknown>, nowMs)}`)
  return out.join('\n')
}


// ---- 11-17 에픽 하위 작업 펼치기 ------------------------------------------------------------------

/** 펼침 기본값: 전체 펼침(사용자 요청 '다 보고싶음') */
export const DEFAULT_EPIC_VIEW: EpicView = { all: true, epics: {} }

/** panel-view.json 해석. 없거나 깨지면 기본값(패널이 죽지 않게 예외 없음) */
export const parseEpicView = (text: string | undefined): EpicView => {
  if (text === undefined) return DEFAULT_EPIC_VIEW
  try {
    const d = JSON.parse(text) as Partial<EpicView> | null
    if (d === null || typeof d !== 'object') return DEFAULT_EPIC_VIEW
    const epics: Record<string, boolean> = {}
    for (const [k, v] of Object.entries(d.epics !== null && typeof d.epics === 'object' ? d.epics : {})) {
      if (typeof v === 'boolean') epics[k] = v
    }
    return { all: typeof d.all === 'boolean' ? d.all : true, epics }
  } catch {
    return DEFAULT_EPIC_VIEW
  }
}

export const epicOpen = (view: EpicView, id: string): boolean => view.epics[id] ?? view.all

/**
 * /board-epics 인자 적용. on·off 는 전체 값을 바꾸고 에픽별 예외를 지움, 인자 없음은 전체 반전,
 * 그 밖은 에픽 id(또는 접두어 한 개)의 현재 상태 반전. ids 에 없는 값은 error
 */
export const applyEpicToggle = (view: EpicView, arg: string, ids: string[]): { view: EpicView; error?: string } => {
  const a = arg.trim()
  if (a === 'on' || a === 'off') return { view: { all: a === 'on', epics: {} } }
  if (a === '' || a === 'toggle') return { view: { all: !view.all, epics: {} } }
  const hit = ids.includes(a) ? [a] : ids.filter(i => i.endsWith(a) || i.startsWith(a))
  if (hit.length !== 1) {
    return { view, error: hit.length === 0 ? `에픽 '${a}' 없음. 목록: ${ids.join(' ')}` : `'${a}' 가 여러 에픽과 맞음: ${hit.join(' ')}` }
  }
  return { view: { all: view.all, epics: { ...view.epics, [hit[0]]: !epicOpen(view, hit[0]) } } }
}

/** 진행률 막대 `▰▰▱▱`. n/m 은 에픽 줄 제목 끝 한 곳에만 있어 여기선 뺌. 하위 0이면 빈 문자열(펼칠 것 없음) */
export const epicBar = (done: number, total: number, cells = PANEL_CONFIG.barCells): string => {
  if (total <= 0) return ''
  const n = Math.round((Math.min(done, total) / total) * cells)
  return `${'▰'.repeat(n)}${'▱'.repeat(cells - n)}`
}

const KID_MARK: Record<AutoKid['st'], string> = { todo: '○', doing: '◐', blocked: '✕', done: '✓' }

const EVIDENCE_TEXT: Record<NonNullable<AutoKid['ev']>, string> = { none: '증거 없음', ok: '증거 ✓', stale: '증거 낡음', unk: '증거 ?' }

/**
 * 진행 중 하위 카드의 둘째 줄 `갱신 12분 · 증거 ✓`. 담당은 첫 줄에 있어 되풀이하지 않음.
 * 짧게 고정(최대 약 26칸)해 폭 40 에서도 한 줄. 오래됨이면 '⚠' 를 붙이고 warn
 */
export const kidBeat = (k: AutoKid, nowMs: number, cfg = PANEL_CONFIG): { text: string; warn: boolean } | undefined => {
  if (k.st !== 'doing') return undefined
  const m = ageOf(k.beatAt ?? k.at, nowMs)
  const warn = m === undefined || m >= cfg.beatStaleMin
  return { text: `갱신 ${fmtMin(k.beatAt ?? k.at, nowMs)}${warn ? ' ⚠' : ''} · ${EVIDENCE_TEXT[k.ev ?? 'none'] ?? EVIDENCE_TEXT.none}`, warn }
}

/** 펼친 에픽의 하위 카드 줄: ○◐✓ 제목 · 담당 · 마지막 갱신 (하위 N). 진행 중이면 sub(heartbeat·증거). epicKidsMax 넘으면 '외 N개' */
export const kidLines = (kids: AutoKid[] | undefined, nowMs: number, cfg = PANEL_CONFIG): Array<{ mark: string; text: string; age: string; sub?: string; warn?: boolean }> => {
  const ks = kids ?? []
  const out = ks.slice(0, cfg.epicKidsMax).map(k => ({
    mark: KID_MARK[k.st] ?? '○',
    text: [k.title.length > cfg.kidTitleMax ? `${k.title.slice(0, cfg.kidTitleMax)}…` : k.title, k.who === null ? undefined : (k.who.length > cfg.kidWhoMax ? `${k.who.slice(0, cfg.kidWhoMax)}…` : k.who), k.subs > 0 ? `하위 ${k.subs}` : undefined].filter(x => x !== undefined).join(' · '),
    age: fmtMin(k.at, nowMs),
    ...(() => { const b = kidBeat(k, nowMs, cfg); return b === undefined ? {} : { sub: b.text, warn: b.warn } })(),
  }))
  if (ks.length > cfg.epicKidsMax) out.push({ mark: '', text: `외 ${ks.length - cfg.epicKidsMax}개`, age: '' })
  return out
}

/**
 * 에픽 줄 아래 보조 줄: 첫 줄 `▾ ▰▰▱▱`(꼬리에 두면 제목 칸이 줄어 줄이 꺾임), 펼침이면 하위 목록.
 * 하위가 0이면 아무것도 없음(기존 한 줄 유지)
 */
export const epicRows = (f: AutoFeature, open: boolean, nowMs: number, cfg = PANEL_CONFIG) => {
  if (f.total <= 0 && (f.kids ?? []).length === 0) return { bar: undefined, kids: [] as ReturnType<typeof kidLines> }
  return { bar: `${open ? '▾' : '▸'} ${epicBar(f.done, f.total, cfg.barCells)}`, kids: open ? kidLines(f.kids, nowMs, cfg) : [] }
}
