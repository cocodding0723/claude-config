import { atom, read, update } from 'claude-code'
import type { Register } from 'claude-code'

import type { AutoBoard, BoardItem, BoardView } from '../types'
import {
  autoBanner,
  blockedLines,
  boardLines,
  cardItems,
  fmtAge,
  ageMin,
  ghLine,
  headCounts,
  headlineText,
  hubLine,
  idleLines,
  mergeApprovals,
  parseAuto,
  progressGroups,
  recentLines,
  sourceNote,
} from './board'

// Slack 칸 없음: 봇이 켜져도 Slack 결정 카드의 원천은 작업판 결정 카드라 결정 대기 한 칸이면 됨
const PANE = 'lrm-board'
const TITLE = 'LRM 현황'
// 수집기(launchd ai.lrm.panelcollect)가 60초마다·작업판 변경 때 쓰는 자동 칸. 결정·할 일·헤드라인의 정본은 작업판(bd). 승인은 결정 대기 칸의 배지
const AUTO_SOURCE = '__HOME__/.config/lrm-hubs/panel-auto.json'
const POLL_MS = 5000
const MAIN_CWD = '__HOME__/orca/LoveRevolutionMaker'

// 손 칸(panel.json)을 없애 모양이 바뀜: view-3. 옛 값은 초기값으로 읽힘
const view = atom(
  { plugin: 'lrm-board', key: 'view' } as const,
  { autoMtimeMs: 0, nowMs: 0 } as BoardView,
  { shape: 'view-3' },
)

const byPriority = (items: BoardItem[] | undefined) =>
  [...(items ?? [])].sort((a, b) => a.p - b.p)

const C = {
  todo: '#4FC3F7',
  hub: '#B39DDB',
  pending: '#FF8A65',
  recent: '#90A4AE',
  pr: '#A5D6A7',
  idle: '#80CBC4',
  title: '#FFFFFF',
  detail: '#C8C8C8',
  how: '#81C784',
  link: '#64B5F6',
  meta: '#9E9E9E',
  error: '#EF5350',
  gauge: '#66BB6A',
  stale: '#FFCA28',
} as const

const STATE_COLOR: Record<string, string> = {
  busy: '#66BB6A',
  approval: '#EF5350',
  waiting: '#FFCA28',
  blocked: '#EF5350',
  idle: '#9E9E9E',
  unknown: '#616161',
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    await $.command.register({
      name: 'lrm-board',
      description: 'LRM 오케스트레이션 현황 패널 열기',
    })

    // 수정 시각이 바뀌었을 때만 다시 읽음
    const refresh = async () => {
      const nowMs = await $.clock.now()
      const current = await read($, view)
      const autoStat = await $.fs.stat(AUTO_SOURCE).catch(() => undefined)
      const autoMtimeMs = autoStat?.mtimeMs ?? 0

      let auto: AutoBoard | undefined = current.auto
      let autoError = current.autoError
      if (autoStat === undefined) {
        auto = undefined
        autoError = undefined
      } else if (autoMtimeMs !== current.autoMtimeMs || current.autoError !== undefined) {
        try {
          auto = parseAuto(await $.fs.read(AUTO_SOURCE))
          autoError = undefined
        } catch (err) {
          auto = undefined
          autoError = String(err)
        }
      }

      // nowMs 도 매번 갱신해 '멈춤 N분' 이 파일 변화 없이도 늘어나게 함
      await update($, view, () => ({ auto, autoError, autoMtimeMs, nowMs }))
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
    const { auto, autoError, autoMtimeMs, nowMs } = await read($, view)

    const banner = autoBanner(auto, autoMtimeMs, autoError, nowMs)
    const counts = headCounts(auto)
    const src = auto?.sources
    const workBoard = src?.board?.value ?? undefined

    const decisionMerge = mergeApprovals(src?.confirm, cardItems(workBoard?.decisions, 'decision', nowMs), nowMs)
    const decisions = byPriority(decisionMerge.items)
    const todos = byPriority(cardItems(workBoard?.todos, 'todo', nowMs))

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
    const tree = (lines: Array<[string, string]>) =>
      lines.map(([text, color], i) => hang(i === lines.length - 1 ? '└' : '├', text, color))
    const section = (label: string, color: string, note?: string) => (
      <Box marginTop={1}>
        <Text wrap="wrap">
          <Text bold color={color}>{label}</Text>
          {note !== undefined && <Text dimColor>{`  ${note}`}</Text>}
        </Text>
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

    const autoHubs = src?.hubs?.value ?? undefined
    const progress = progressGroups(src?.progress?.value)
    const workers = src?.workers?.value ?? undefined
    const gh = src?.gh
    const idle = idleLines(src?.idle?.value)
    const doing = boardLines(workBoard, nowMs)
    const blocked = blockedLines(workBoard)
    const headline = headlineText(workBoard)
    const recent = recentLines(workBoard, nowMs)
    const boardNote = sourceNote(src?.board, nowMs)

    return (
      <Box flexDirection="column">
        <Text bold={banner.level !== 'ok'} color={banner.level === 'ok' ? C.meta : C.error} wrap="wrap">{banner.text}</Text>
        {counts !== undefined && <Text dimColor>{`허브 작업 중 ${counts.busy} · 승인창 ${counts.blocked}`}</Text>}
        {headline !== undefined && <Text bold wrap="wrap">{headline}</Text>}

        {section(`🙋 결정 대기 (${decisions.length})`, C.pending, decisionMerge.stale ? `${boardNote} · ⚠ 확인 목록이 낡음 — 작업판 카드 우선` : boardNote)}
        {decisions.length === 0 && <Text dimColor>없음</Text>}
        {itemRows(decisions, C.pending)}

        {section(`🛠️ 내가 할 일 (${todos.length})`, C.todo, boardNote)}
        {todos.length === 0 && <Text dimColor>없음</Text>}
        {itemRows(todos, C.todo)}

        {section(
          `🔄 진행 (진행 중 ${workBoard?.counts.in_progress ?? '?'} · 열림 ${workBoard?.counts.open ?? '?'} · 막힘 ${workBoard?.counts.blocked ?? '?'})`,
          C.gauge,
          boardNote,
        )}
        {doing !== undefined && doing.length === 0 && <Text dimColor>진행 중 카드 없음</Text>}
        {doing !== undefined && doing.map((l, i) => hang(i === doing.length - 1 ? '└' : '├', l.text, l.warn ? C.stale : C.detail))}
        {blocked.length > 0 && section(`🚧 막힘 (${blocked.length})`, C.error)}
        {blocked.length > 0 && tree(blocked.map(l => [l, C.detail] as [string, string]))}

        {progress !== undefined && section(`📋 허브 진행 (진행 중 ${src?.progress?.value?.counts.doing ?? 0} · 답 대기 ${src?.progress?.value?.counts.wait ?? 0} · 막힘 ${src?.progress?.value?.counts.block ?? 0})`, C.hub, sourceNote(src?.progress, nowMs))}
        {progress !== undefined && progress.map(g => (
          <Box flexDirection="column">
            <Text bold color={C.title}>{g.group}</Text>
            {tree(g.lines.map(l => [l, C.detail] as [string, string]))}
          </Box>
        ))}

        {autoHubs !== undefined ? (
          <Box flexDirection="column">
            {section(`🖥️ 허브 (${autoHubs.rows.length})`, C.hub, `${sourceNote(src?.hubs, nowMs)}${autoHubs.truncated === true ? ' · 터미널 목록 잘림' : ''}`)}
            {autoHubs.rows.map(hub => (
              <Text wrap="wrap">
                <Text color={STATE_COLOR[hub.state] ?? C.meta}>{'● '}</Text>
                <Text bold>{hub.name}</Text>
                <Text dimColor>{` ${hubLine(hub, src?.progress?.value?.groups?.[hub.name])}`}</Text>
              </Text>
            ))}
          </Box>
        ) : (
          <Text dimColor>허브 값 없음</Text>
        )}

        {section('🔀 PR', C.pr, sourceNote(gh, nowMs))}
        {gh?.value !== undefined && gh.value !== null ? (
          <Text wrap="wrap">{ghLine(gh.value)}</Text>
        ) : (
          <Text dimColor>{gh?.off === true ? 'PANEL_GH_REPOS 설정 없음' : '값 없음'}</Text>
        )}

        {section('😴 쉬는 자원', C.idle, `${sourceNote(src?.idle, nowMs)} · 정리는 사람이`)}
        {idle.length === 0 && <Text dimColor>값 없음</Text>}
        {idle.map(l => <Text color={l.warn ? C.error : C.detail} wrap="wrap">{l.text}</Text>)}

        {section('🧾 최근 기록', C.recent, boardNote)}
        {recent.length === 0 && <Text dimColor>닫힌 카드 없음</Text>}
        {recent.length > 0 && tree(recent.map(l => [l, C.detail] as [string, string]))}
        {workers !== undefined && <Text dimColor>{`살아 있는 워커 ${workers.live}  ${sourceNote(src?.workers, nowMs)}`}</Text>}
      </Box>
    )
  })
}
