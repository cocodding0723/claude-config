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
  /** 진행 게이지(메인이 씀). 맨 위 고정 표시 */
  progress?: ProgressItem[]
}

/** state: on 진행 · blocked 막힘(빨강) · done 끝 */
export type ProgressItem = {
  title: string
  done: number
  total: number
  eta?: string
  state?: 'on' | 'blocked' | 'done'
  steps?: string
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

export type AutoHubs = {
  rows: AutoHub[]
  counts: { busy: number; approval: number; idle: number; unknown: number }
  /** Orca 가 터미널 목록을 잘랐으면 true(허브가 빠졌을 수 있음). 옛 파일엔 없음 */
  truncated?: boolean
}
/** 결정 대기 한 건. id·state 는 번호 카드(slack_notify.sh --options)만, 옛 카드는 null(옛 파일엔 키 없음) */
export type AutoPendingItem = { title: string; ageMin: number | null; id?: string | null; state?: 'hold' | 'conflict' | null }
export type AutoPending = { count: number; items: AutoPendingItem[] }
export type AutoPush = { lines: Array<{ at: string; event: string; reason: string | null }> }
export type AutoWorkers = { live: number; recent: Array<{ title: string | null; status: string; at: number | null }> }
/** 쉬는 자원. 부분마다 따로 실패해 한 부분이 null 일 수 있음 */
export type AutoIdle = {
  terminals: { total: number; agents: number } | null
  workers: {
    live: number; succeeded: number; failed: number; stopped: number; abandoned?: number
    leftover: number; more: number
    items: Array<{ dispatch: string; title: string | null; status: string; terminal: string }>
    /** 센 행 수·Orca 전체 행 수(page.total)·끝 쪽까지 셌는지. 옛 파일엔 없음 */
    seen?: number; total?: number | null; complete?: boolean
    /** Orca 가 준 터미널 상태별 총계(worker-list counts) */
    terminalStates?: Record<string, number> | null
  } | null
  workspaces: {
    total: number; noTerminal: number; more: number; truncated: boolean
    items: Array<{ name: string; path: string; branch: string; ageDays: number | null; merged: boolean | null }>
  } | null
  disk: { freeGB: number; usedPct: number; minGB: number; warn: boolean } | null
  /** err: 마지막 측정 실패 종류(timeout 등). 실패해도 gb·at 은 직전 성공 값 */
  wsSize: { gb: number | null; at: number | null; dir: string; err?: string | null; tried?: number } | null
}
/** capped: gh --limit 만큼 꽉 차 숫자가 하한 */
export type AutoGh = {
  repos: Array<{ repo: string; open: number; ciFail: number; capped?: boolean }>
  open: number; ciFail: number; capped?: boolean
}

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
    idle?: AutoSource<AutoIdle>
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
