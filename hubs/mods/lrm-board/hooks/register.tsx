import { atom, read, update } from 'claude-code'
import type { Register } from 'claude-code'

import type { AutoBoard, Board, BoardItem, BoardView, HistoryEntry } from '../types'
import {
  autoBanner,
  fmtAge,
  ageMin,
  ghLine,
  headCounts,
  hubLine,
  idleLines,
  mainAge,
  parseAuto,
  parseMain,
  pendingLine,
  progressBar,
  sourceNote,
} from './board'

const PANE = 'lrm-board'
const TITLE = 'LRM 현황'
// 메인 코디네이터가 손으로 쓰는 칸(승인·할 일·headline·기록)
const SOURCE = '__HOME__/.config/lrm-hubs/panel.json'
// 수집기(launchd ai.lrm.panelcollect)가 90초마다 쓰는 자동 칸. 메인 파일과 쓰는 사람이 달라 파일을 나눔
const AUTO_SOURCE = '__HOME__/.config/lrm-hubs/panel-auto.json'
const POLL_MS = 15000
const MAIN_CWD = '__HOME__/orca/LoveRevolutionMaker'

// 모양이 바뀌어 view-2. 옛 값은 초기값으로 읽힘
const view = atom(
  { plugin: 'lrm-board', key: 'view' } as const,
  { mtimeMs: 0, autoMtimeMs: 0, nowMs: 0 } as BoardView,
  { shape: 'view-2' },
)

const byPriority = (items: BoardItem[] | undefined) =>
  [...(items ?? [])].sort((a, b) => a.p - b.p)

const C = {
  approve: '#F5A623',
  todo: '#4FC3F7',
  hub: '#B39DDB',
  pending: '#FF8A65',
  recent: '#90A4AE',
  pr: '#A5D6A7',
  idle: '#80CBC4',
  history: '#FFB74D',
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

    // 파일마다 수정 시각이 바뀌었을 때만 다시 읽음. 한쪽이 없거나 깨져도 다른 쪽은 그대로 그림
    const refresh = async () => {
      const nowMs = await $.clock.now()
      const current = await read($, view)
      const stat = await $.fs.stat(SOURCE).catch(() => undefined)
      const autoStat = await $.fs.stat(AUTO_SOURCE).catch(() => undefined)
      const mtimeMs = stat?.mtimeMs ?? 0
      const autoMtimeMs = autoStat?.mtimeMs ?? 0

      let board = current.board
      let error = current.error
      if (stat === undefined) {
        board = undefined
        error = `파일 없음: ${SOURCE}`
      } else if (mtimeMs !== current.mtimeMs || current.error !== undefined) {
        try {
          board = parseMain(await $.fs.read(SOURCE))
          error = undefined
        } catch (err) {
          error = `읽기 실패: ${String(err)}`
        }
      }

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
      await update($, view, () => ({ board, error, mtimeMs, auto, autoError, autoMtimeMs, nowMs }))
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
    const { board, error, auto, autoError, mtimeMs, autoMtimeMs, nowMs } = await read($, view)

    const banner = autoBanner(auto, autoMtimeMs, autoError, nowMs)
    const main = mainAge(mtimeMs, nowMs)
    const counts = headCounts(board, auto)
    const src = auto?.sources

    const approvals = byPriority(board?.approvals)
    const todos = byPriority(board?.todos)
    const history: HistoryEntry[] = board?.history ?? []
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
    const pending = src?.pending?.value ?? undefined
    const push = src?.push?.value ?? undefined
    const workers = src?.workers?.value ?? undefined
    const gh = src?.gh
    const idle = idleLines(src?.idle?.value)

    return (
      <Box flexDirection="column">
        {banner.level !== 'ok' && <Text bold color={C.error} wrap="wrap">{banner.text}</Text>}
        <Text wrap="wrap">
          <Text color={main.old ? C.stale : C.meta}>{main.text}</Text>
          <Text dimColor>{` · ${banner.level === 'ok' ? banner.text : '자동 멈춤'} · 작업 중 ${counts.busy} · 막힘 ${counts.blocked}`}</Text>
        </Text>
        {main.old && mtimeMs > 0 && <Text color={C.stale}>{`메인 칸이 ${fmtAge(ageMin(mtimeMs, nowMs))} 갱신됨 — 승인·할 일은 낡았을 수 있음`}</Text>}
        {board?.headline !== undefined && <Text wrap="wrap">{board.headline}</Text>}
        {error !== undefined && <Text color={C.error}>{error}</Text>}

        {(board?.progress?.length ?? 0) > 0 && section('📊 진행', C.gauge)}
        {(board?.progress ?? []).map(p => {
          const g = progressBar(p)
          const color = p.state === 'blocked' ? C.error : p.state === 'done' ? C.meta : C.gauge
          return (
            <Box flexDirection="column">
              <Text bold color={C.title} wrap="wrap">{p.title}</Text>
              <Text color={color}>{g.text}</Text>
              {p.steps !== undefined && <Text color={C.detail} wrap="wrap">{`└ ${p.steps}`}</Text>}
            </Box>
          )
        })}

        {section(`🙋 내가 승인할 것 (${approvals.length})`, C.approve)}
        {approvals.length === 0 && <Text dimColor>없음</Text>}
        {itemRows(approvals, C.approve)}

        {section(`🛠️ 내가 할 일 (${todos.length})`, C.todo)}
        {todos.length === 0 && <Text dimColor>없음</Text>}
        {itemRows(todos, C.todo)}

        {section(`⏳ 결정 대기 (${pending?.count ?? '?'})`, C.pending, sourceNote(src?.pending, nowMs))}
        {pending !== undefined && pending.items.length === 0 && <Text dimColor>없음</Text>}
        {pending !== undefined && tree(pending.items.map(p => [pendingLine(p), C.detail]))}

        {autoHubs !== undefined ? (
          <Box flexDirection="column">
            {section(`🔄 허브 (${autoHubs.rows.length})`, C.hub, `${sourceNote(src?.hubs, nowMs)}${autoHubs.truncated === true ? ' · 터미널 목록 잘림' : ''}`)}
            {autoHubs.rows.map(h => (
              <Text wrap="wrap">
                <Text color={STATE_COLOR[h.state] ?? C.meta}>{'● '}</Text>
                <Text bold>{h.name}</Text>
                <Text dimColor>{` ${hubLine(h)}`}</Text>
              </Text>
            ))}
          </Box>
        ) : (
          <Box flexDirection="column">
            {section(`🔄 허브 (${board?.hubs?.length ?? 0})`, C.hub, '메인 기록')}
            {(board?.hubs ?? []).map(hub => (
              <Text wrap="wrap">
                <Text color={STATE_COLOR[hub.state] ?? C.meta}>{'● '}</Text>
                <Text bold>{hub.name}</Text>
                <Text dimColor>{hub.note !== undefined ? ` ${hub.note}` : ''}</Text>
              </Text>
            ))}
          </Box>
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

        {section('🧾 최근 기록', C.recent, sourceNote(src?.workers, nowMs))}
        {workers !== undefined &&
          tree([
            [`살아 있는 워커 ${workers.live}`, C.meta],
            ...workers.recent.map(w => [
              `${w.status === 'succeeded' ? '✅' : w.status === 'failed' ? '❌' : '⏹️'} ${w.title ?? '(제목 없음)'}${w.at !== null ? `  · ${fmtAge(ageMin(w.at * 1000, nowMs))}` : ''}`,
              C.detail,
            ] as [string, string]),
          ])}
        {push !== undefined && push.lines.length > 0 && (
          <Box flexDirection="column">
            <Text dimColor>{`Slack→메인 전달  ${sourceNote(src?.push, nowMs)}`}</Text>
            {tree(push.lines.map(l => [`${l.at}  ${l.event}${l.reason !== null ? `(${l.reason})` : ''}`, C.detail]))}
          </Box>
        )}

        {section(`📜 진행 기록 (${history.length})`, C.history, '메인 기록')}
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
