import { atom, read, update } from 'claude-code'
import type { Register } from 'claude-code'

import type { Board, BoardItem, BoardView, HistoryEntry } from '../types'

const PANE = 'lrm-board'
const TITLE = 'LRM 현황'
// 메인 코디네이터가 순찰·보고 때 갱신하는 파일
const SOURCE = '__HOME__/.config/lrm-hubs/panel.json'
const POLL_MS = 15000
const MAIN_CWD = '__HOME__/orca/LoveRevolutionMaker'

const view = atom({ plugin: 'lrm-board', key: 'view' } as const, { mtimeMs: 0 } as BoardView, {
  shape: 'view-1',
})

const byPriority = (items: BoardItem[] | undefined) =>
  [...(items ?? [])].sort((a, b) => a.p - b.p)

const C = {
  approve: '#F5A623',
  todo: '#4FC3F7',
  hub: '#B39DDB',
  history: '#FFB74D',
  title: '#FFFFFF',
  detail: '#C8C8C8',
  how: '#81C784',
  link: '#64B5F6',
  meta: '#9E9E9E',
  error: '#EF5350',
} as const

const STATE_COLOR: Record<string, string> = {
  busy: '#66BB6A',
  waiting: '#FFCA28',
  blocked: '#EF5350',
  idle: '#9E9E9E',
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    await $.command.register({
      name: 'lrm-board',
      description: 'LRM 오케스트레이션 현황 패널 열기',
    })

    const refresh = async () => {
      const stat = await $.fs.stat(SOURCE).catch(() => undefined)
      if (stat === undefined) {
        await update($, view, () => ({ mtimeMs: 0, error: `파일 없음: ${SOURCE}` }))
        return
      }

      const current = await read($, view)
      if (stat.mtimeMs === current.mtimeMs && current.error === undefined) {
        return
      }

      try {
        const text = await $.fs.read(SOURCE)
        const board = JSON.parse(text) as Board
        await update($, view, () => ({ board, mtimeMs: stat.mtimeMs }))
      } catch (err) {
        await update($, view, old => ({ ...old, mtimeMs: stat.mtimeMs, error: `읽기 실패: ${String(err)}` }))
      }
    }

    await refresh()
    $.clock.every(POLL_MS, () => void refresh())
    // 허브·워커 세션은 /lrm-board 로만 열고, 메인 체크아웃에서만 자동으로 띄움
    if (e.cwd === MAIN_CWD) {
      void $.ui.open({ id: PANE, title: TITLE })
    }

    return next(e)
  })

  on('command.run', { command: 'lrm-board' }, async $ => {
    await $.ui.open({ id: PANE, title: TITLE })

    return { text: 'LRM 현황 패널을 열었다.' }
  })

  on('ui.render', { component: 'Pane', requestId: PANE }, async ($, e) => {
    const { Box, Text } = $.ui.resolve(e)
    const { board, error } = await read($, view)

    if (board === undefined) {
      return (
        <Box flexDirection="column">
          <Text color="warning">{error ?? '불러오는 중'}</Text>
        </Box>
      )
    }

    const approvals = byPriority(board.approvals)
    const todos = byPriority(board.todos)
    const hubs = board.hubs ?? []
    const history: HistoryEntry[] = board.history ?? []
    // 허브별 가지로 묶고, 허브 순서는 가장 최근 기록 순
    const historyGroups: Array<[string, HistoryEntry[]]> = []
    for (const h of history) {
      const group = historyGroups.find(([name]) => name === h.hub)
      if (group === undefined) {
        historyGroups.push([h.hub, [h]])
      } else {
        group[1].push(h)
      }
    }
    const busy = hubs.filter(hub => hub.state === 'busy').length
    const waiting = hubs.filter(hub => hub.state === 'waiting' || hub.state === 'blocked').length


    // 줄바꿈돼도 둘째 줄이 글머리표 뒤에 맞춰지도록 머리·본문을 따로 둠
    const hang = (mark: string, text: string, color: string, key?: string) => (
      <Box flexDirection="row">
        <Box width={3} flexShrink={0}>
          <Text color={C.meta}>{`${mark} `}</Text>
        </Box>
        <Box flexGrow={1} flexShrink={1}>
          <Text color={color} wrap={key === 'link' ? 'truncate-end' : 'wrap'}>{text}</Text>
        </Box>
      </Box>
    )

    const itemRows = (items: BoardItem[], color: string) =>
      items.map(item => {
        // 카드 안 내용은 트리로: 이유·배경·답법·링크를 가지로 붙임
        const rows: Array<[string, string, string?]> = []
        if (item.why !== undefined) rows.push([item.why, C.meta])
        for (const line of item.detail ?? []) rows.push([line, C.detail])
        if (item.how !== undefined) rows.push([`답: ${item.how}`, C.how])
        for (const link of item.links ?? []) rows.push([`${link.label}  ${link.href}`, C.link, 'link'])

        return (
          <Box flexDirection="column" borderStyle="round" borderColor={color} paddingX={1} marginBottom={1}>
            <Text wrap="wrap">
              <Text bold color={color}>{`${item.p}  `}</Text>
              <Text bold color={C.title}>{item.title}</Text>
            </Text>
            {rows.map(([text, c, key], i) => hang(i === rows.length - 1 ? '└' : '├', text, c ?? C.detail, key))}
          </Box>
        )
      })

    return (
      <Box flexDirection="column">
        <Text dimColor>{`갱신 ${board.updatedAt} · 작업 중 ${busy} · 막힘 ${waiting}`}</Text>
        {board.headline !== undefined && <Text wrap="wrap">{board.headline}</Text>}
        {error !== undefined && <Text color={C.error}>{error}</Text>}

        <Box marginTop={1}>
          <Text bold color={C.approve}>{`🙋 내가 승인할 것 (${approvals.length})`}</Text>
        </Box>
        {approvals.length === 0 && <Text dimColor>없음</Text>}
        {itemRows(approvals, C.approve)}

        <Box>
          <Text bold color={C.todo}>{`🛠️ 내가 할 일 (${todos.length})`}</Text>
        </Box>
        {todos.length === 0 && <Text dimColor>없음</Text>}
        {itemRows(todos, C.todo)}

        <Box>
          <Text bold color={C.hub}>{`🔄 허브 (${hubs.length})`}</Text>
        </Box>
        {hubs.map(hub => (
          <Text wrap="wrap">
            <Text color={STATE_COLOR[hub.state] ?? C.meta}>{'● '}</Text>
            <Text bold>{hub.name}</Text>
            <Text dimColor>{hub.note !== undefined ? ` ${hub.note}` : ''}</Text>
          </Text>
        ))}

        <Box marginTop={1}>
          <Text bold color={C.history}>{`📜 진행 기록 (${history.length})`}</Text>
        </Box>
        {historyGroups.map(([hub, entries]) => (
          <Box flexDirection="column" marginBottom={1}>
            <Text bold color={C.title}>{hub}</Text>
            {entries.map((h, i) =>
              hang(
                i === entries.length - 1 ? '└' : '├',
                `${h.at !== "" ? `${h.at}  ` : ""}${h.text}${h.ref !== undefined ? `  (${h.ref})` : ''}`,
                C.detail,
              ),
            )}
          </Box>
        ))}
      </Box>
    )
  })
}
