export type BoardLink = { label: string; href: string }

export type BoardItem = {
  /** 확인 목록 원료 항목의 키. 작업판 승인 카드와 겹침 판정에 씀 */
  key?: string
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
/** 진행 스냅샷(progress_collect.py). bucket: doing 진행 중·wait 답 대기·block 막힘, group 은 허브 이름·장부·PR·결정 */
export type AutoProgressItem = { key: string; bucket: 'doing' | 'wait' | 'block'; title: string; group: string; link: string }
/** 허브(slug)별 하위 작업 공간 수. warn 은 10개 초과 */
export type AutoWorkspaces = Record<string, { children: number; active: number; warn: boolean }>
export type AutoProgress = { counts: { doing: number; wait: number; block: number }; items: AutoProgressItem[]; notes: string[]; groups?: AutoWorkspaces }
/** 확인 목록(confirm_items.py)의 안 함 항목. 문서 사이트·Slack 할 일 목록과 같은 원천. urgency 3 이 가장 급함 */
export type AutoConfirmItem = { key: string; kind: string; kind_label: string; title: string; hub: string; urgency: number; how: string }
export type AutoConfirm = { todo: number; done: number; items: AutoConfirmItem[] }
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

/** 작업판 카드 한 장. 시각은 epoch 초, beatAt 은 마지막 갱신(heartbeat) */
export type AutoCard = {
  id: string
  title: string
  p: number
  who: string | null
  createdAt: number | null
  beatAt: number | null
  startedAt: number | null
  comments: number
  /** 결정·할 일 카드만. 선택지·추천 */
  desc?: string | null
  /** 결정 카드의 승인 성격 배지(label gate:approval, 옛 kind:approval). 없으면 null */
  gate?: string | null
}

/** 작업판(beads, ~/orca/board) 요약. 패널의 정본. 옛 파일은 doing·counts 만 있음 */
export type AutoWorkBoard = {
  counts: { in_progress: number; open: number; blocked: number }
  doing: AutoCard[]
  more: number
  blocked?: AutoCard[]
  decisions?: AutoCard[]
  todos?: AutoCard[]
  headline?: { id: string; title: string; done: number; total: number; epics: number } | null
  recent?: Array<{ id: string; title: string; at: number | null; reason: string | null }>
}

/** 수집기(panel_collect.sh)가 쓰는 panel-auto.json */
export type AutoBoard = {
  version: number
  /** epoch 초. 수집기가 기록 순간에 씀 */
  updatedAt: number
  updatedAtText?: string
  /** epoch 초. 파일을 쓴 순간(회차가 길면 updatedAt 보다 늦음) */
  writtenAt?: number
  sources: {
    board?: AutoSource<AutoWorkBoard>
    hubs?: AutoSource<AutoHubs>
    pending?: AutoSource<AutoPending>
    progress?: AutoSource<AutoProgress>
    confirm?: AutoSource<AutoConfirm>
    push?: AutoSource<AutoPush>
    workers?: AutoSource<AutoWorkers>
    gh?: AutoSource<AutoGh>
    idle?: AutoSource<AutoIdle>
  }
}

export type BoardView = {
  auto?: AutoBoard
  autoMtimeMs: number
  /** 마지막 폴링 시각(ms). 오래됨 판정용 */
  nowMs: number
  autoError?: string
}

declare module 'claude-code' {
  interface PluginState {
    'lrm-board': { view: Shaped<BoardView> }
  }
}
