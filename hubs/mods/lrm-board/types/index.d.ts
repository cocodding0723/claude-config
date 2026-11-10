export type BoardLink = { label: string; href: string }

export type BoardItem = {
  /** 1 이 가장 급함 */
  p: number
  title: string
  /** 풀리는 것·이유 한 줄 */
  why?: string
  /** 답하는 법·명령 한 줄 */
  how?: string
  /** 결정에 필요한 배경·선택지 설명, 줄마다 한 사실 */
  detail?: string[]
  /** Jira·PR·Slack 링크 */
  links?: BoardLink[]
}

export type HubRow = {
  name: string
  /** busy · waiting · idle · blocked */
  state: string
  note?: string
}

export type HistoryEntry = {
  /** HH:MM */
  at: string
  /** 허브 이름 또는 '사용자'·'메인' */
  hub: string
  text: string
  /** PR·티켓 같은 짧은 꼬리표 */
  ref?: string
}

/** 메인이 손으로 쓰는 panel.json. 수집기는 이 파일을 쓰지 않음 */
export type Board = {
  updatedAt?: string
  headline?: string
  approvals?: BoardItem[]
  todos?: BoardItem[]
  /** 자동 칸(panel-auto.json)이 없을 때만 표시하는 옛 허브 칸 */
  hubs?: HubRow[]
  /** 오늘 진행 기록, 최신이 앞 */
  history?: HistoryEntry[]
}

/** 원료 하나. at 은 마지막 성공 시각(epoch 초), tried 는 마지막 시도 시각 */
export type AutoSource<T> = {
  ok: boolean
  err: string | null
  at: number | null
  tried?: number | null
  stale: boolean
  /** gh 저장소 설정이 없어 건너뜀 */
  off?: boolean
  value: T | null
}

export type AutoHub = {
  hub: string
  name: string
  agent: string
  /** busy · approval · idle · unknown */
  state: string
  quietMin: number | null
  connected: boolean
  dup: boolean
}

export type AutoHubs = { rows: AutoHub[]; counts: { busy: number; approval: number; idle: number; unknown: number } }
export type AutoPending = { count: number; items: Array<{ title: string; ageMin: number | null }> }
export type AutoPush = { lines: Array<{ at: string; event: string; reason: string | null }> }
export type AutoWorkers = { live: number; recent: Array<{ title: string | null; status: string; at: number | null }> }
export type AutoGh = { repos: Array<{ repo: string; open: number; ciFail: number }>; open: number; ciFail: number }

/** 수집기(panel_collect.sh)가 쓰는 panel-auto.json */
export type AutoBoard = {
  version: number
  /** epoch 초. 수집기가 기록 순간에 씀 */
  updatedAt: number
  updatedAtText?: string
  sources: {
    hubs?: AutoSource<AutoHubs>
    pending?: AutoSource<AutoPending>
    push?: AutoSource<AutoPush>
    workers?: AutoSource<AutoWorkers>
    gh?: AutoSource<AutoGh>
  }
}

export type BoardView = {
  board?: Board
  auto?: AutoBoard
  /** 파일 수정 시각(ms). 0 은 없음 */
  mtimeMs: number
  autoMtimeMs: number
  /** 마지막 폴링 시각(ms). 오래됨 판정용 */
  nowMs: number
  error?: string
  autoError?: string
}

declare module 'claude-code' {
  interface PluginState {
    'lrm-board': { view: Shaped<BoardView> }
  }
}
