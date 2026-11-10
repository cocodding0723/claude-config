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

export type Board = {
  updatedAt: string
  headline?: string
  approvals: BoardItem[]
  todos: BoardItem[]
  hubs: HubRow[]
  /** 오늘 진행 기록, 최신이 앞 */
  history?: HistoryEntry[]
}

export type BoardView = { board?: Board; error?: string; mtimeMs: number }

declare module 'claude-code' {
  interface PluginState {
    'lrm-board': { view: Shaped<BoardView> }
  }
}
