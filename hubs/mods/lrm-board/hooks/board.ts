// 패널의 순수 함수: 파일 해석·오래됨 판정·표시 문자열. 엔진($) 없이 node 로 시험함(tests/panel/board.test.mjs)
import type { AutoBoard, AutoHub, AutoIdle, AutoProgress, AutoSource, Board } from '../types'

/** 자동 칸이 이만큼 갱신되지 않으면 맨 위 경고. 수집 주기 90초의 6배 이상 */
export const AUTO_STALE_MIN = 10
/** 메인 칸(panel.json)이 이만큼 지나면 연한 경고. 메인은 순찰·결정 때만 고침 */
export const MAIN_STALE_MIN = 30
/** 파일 안 시각이 지금보다 이만큼 앞서면 시계가 틀린 것으로 보고 수정 시각을 씀 */
const FUTURE_SLACK_MS = 2 * 60 * 1000

export const STATE_LABEL: Record<string, string> = {
  busy: '작업 중',
  approval: '승인창',
  idle: '대기',
  unknown: '확인불가',
}

/** panel-auto.json 해석. 모양이 다르면 예외(메인 칸은 그대로 그림) */
export const parseAuto = (text: string): AutoBoard => {
  const d = JSON.parse(text) as AutoBoard
  if (d === null || typeof d !== 'object' || typeof d.updatedAt !== 'number' || d.sources === null || typeof d.sources !== 'object') {
    throw new Error('자동 칸 형식 아님')
  }
  return d
}

export const parseMain = (text: string): Board => {
  const d = JSON.parse(text) as Board
  if (d === null || typeof d !== 'object' || Array.isArray(d)) {
    throw new Error('메인 칸 형식 아님')
  }
  return d
}

/**
 * 자동 칸의 기준 시각(ms). 파일 안 epoch(수집기가 쓰는 순간의 시계)를 먼저 쓰고,
 * 없거나 미래면 파일 수정 시각. 사람이 쓰는 글자 시각은 실제와 어긋난 전례가 있어 쓰지 않음
 */
export const autoStamp = (auto: AutoBoard | undefined, mtimeMs: number, nowMs: number): number | undefined => {
  const epochMs = auto !== undefined && Number.isFinite(auto.updatedAt) ? auto.updatedAt * 1000 : undefined
  if (epochMs !== undefined && epochMs > 0 && epochMs <= nowMs + FUTURE_SLACK_MS) return epochMs
  return mtimeMs > 0 ? mtimeMs : undefined
}

export const ageMin = (stampMs: number | undefined, nowMs: number): number | undefined =>
  stampMs === undefined ? undefined : Math.max(0, Math.floor((nowMs - stampMs) / 60000))

export const fmtAge = (min: number | undefined): string => {
  if (min === undefined) return '알 수 없음'
  if (min < 1) return '방금'
  if (min < 60) return `${min}분 전`
  const h = Math.floor(min / 60)
  return h < 48 ? `${h}시간 ${min % 60}분 전` : `${Math.floor(h / 24)}일 전`
}

export type Banner = { level: 'ok' | 'stale' | 'missing' | 'broken'; text: string }

/** 맨 위 경고. 파일 없음·깨짐·10분 초과 */
export const autoBanner = (
  auto: AutoBoard | undefined,
  autoMtimeMs: number,
  autoError: string | undefined,
  nowMs: number,
): Banner => {
  if (autoMtimeMs === 0) return { level: 'missing', text: '⚠️ 자동 수집 파일 없음 — launchd ai.lrm.panelcollect 확인' }
  if (auto === undefined) return { level: 'broken', text: `⚠️ 자동 수집 파일을 못 읽음${autoError !== undefined ? ` (${autoError})` : ''}` }
  const min = ageMin(autoStamp(auto, autoMtimeMs, nowMs), nowMs)
  if (min !== undefined && min > AUTO_STALE_MIN) return { level: 'stale', text: `⚠️ 자동 수집이 ${min}분째 멈춤` }
  return { level: 'ok', text: `자동 ${fmtAge(min)}` }
}

/** 메인 칸은 파일 수정 시각 기준(파일 안 updatedAt 글자는 손으로 써서 어긋남) */
export const mainAge = (mtimeMs: number, nowMs: number): { old: boolean; text: string } => {
  if (mtimeMs === 0) return { old: true, text: '메인 칸 없음' }
  const min = ageMin(mtimeMs, nowMs) ?? 0
  return { old: min > MAIN_STALE_MIN, text: `메인 ${fmtAge(min)}` }
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

/** 맨 위 숫자. 자동 허브 값이 있으면 그것, 없으면 메인 칸의 옛 hubs */
export const headCounts = (board: Board | undefined, auto: AutoBoard | undefined): { busy: number; blocked: number; source: 'auto' | 'main' } => {
  const hubs = auto?.sources.hubs?.value
  if (hubs !== undefined && hubs !== null) {
    return { busy: hubs.counts.busy, blocked: hubs.counts.approval, source: 'auto' }
  }
  const rows = board?.hubs ?? []
  return {
    busy: rows.filter(r => r.state === 'busy').length,
    blocked: rows.filter(r => r.state === 'waiting' || r.state === 'blocked').length,
    source: 'main',
  }
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
 * 값이 없으면 undefined → 호출한 쪽이 옛 손 칸으로 돌아감
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
