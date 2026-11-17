import { atom, read, update } from 'claude-code'
import type { Register } from 'claude-code'

import type { AutoBoard, BoardView } from '../types'
import {
  PANEL_CONFIG,
  askLines,
  autoBanner,
  autoStamp,
  detailText,
  featureCounts,
  featureLines,
  headerLine,
  parseAuto,
  pickNotify,
  problemLines,
  todoLines,
} from './board'
import type { Line, SectionId } from './board'

// 사용자에게 필요한 4칸만(11-17): 🚨 문제(있을 때만) · 🙋 답할 것 · 🔧 직접 할 일 · 🔄 AI 진행. 칸 순서·임계값은 board.ts PANEL_CONFIG.
// 허브 표·PR·쉬는 자원·최근 기록·원료 상태는 /board-detail 로만 봄(수집기 데이터는 그대로)
const PANE = 'lrm-board'
const TITLE = 'LRM 현황'
// 수집기(launchd ai.lrm.panelcollect)가 60초마다·작업판 변경 때 쓰는 자동 칸. 결정·할 일·헤드라인의 정본은 작업판(bd). 승인은 결정 대기 칸의 배지
const AUTO_SOURCE = '__HOME__/.config/lrm-hubs/panel-auto.json'
const POLL_MS = 5000
const MAIN_CWD = '__HOME__/orca/LoveRevolutionMaker'

// 알림 기록을 더해 모양이 바뀜: view-4. 옛 값은 초기값으로 읽힘
const view = atom(
  { plugin: 'lrm-board', key: 'view' } as const,
  { autoMtimeMs: 0, nowMs: 0 } as BoardView,
  { shape: 'view-4' },
)

const C = {
  problem: '#EF5350',
  ask: '#FF8A65',
  todo: '#4FC3F7',
  ai: '#66BB6A',
  title: '#FFFFFF',
  detail: '#C8C8C8',
  meta: '#9E9E9E',
  stale: '#EF5350',
} as const

const MARK_COLOR: Record<string, string> = { '?': '#FFCA28', '!': '#EF5350', '✕': '#EF5350', '✻': '#66BB6A', '✓': '#90A4AE', '-': '#616161' }

const SECTION: Record<SectionId, { label: string; color: string }> = {
  problem: { label: '🚨 문제', color: C.problem },
  ask: { label: '🙋 답할 것', color: C.ask },
  todo: { label: '🔧 직접 할 일', color: C.todo },
  ai: { label: '🔄 AI 진행', color: C.ai },
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    await $.command.register({
      name: 'lrm-board',
      description: 'LRM 오케스트레이션 현황 패널 열기',
    })
    await $.command.register({
      name: 'board-detail',
      description: '패널에서 뺀 참고 정보(허브·PR·쉬는 자원·최근 기록·진행 카드 담당·원료 상태)',
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

      // 글자는 분 단위라 파일이 그대로면 분이 바뀔 때만 다시 그림(5초마다 그리지 않음)
      const sameMinute = Math.floor(nowMs / 60000) === Math.floor(current.nowMs / 60000)
      if (auto === current.auto && autoMtimeMs === current.autoMtimeMs && autoError === current.autoError && sameMinute) return

      // 데스크톱 알림: 🙋·🚨 새 항목만, 같은 항목 재전송 막기·시간당 상한(PANEL_CONFIG)
      const banner = autoBanner(auto, autoMtimeMs, autoError, nowMs)
      const feats = featureLines(auto?.sources.board?.value?.features, auto?.sources.ai?.value, nowMs)
      const items = [
        ...problemLines(auto, banner, feats, nowMs).map(l => ({ key: l.key, text: l.text, head: '🚨 LRM 문제' })),
        ...askLines(auto?.sources.board?.value?.decisions, nowMs).map(l => ({ key: l.key, text: l.text, head: '🙋 LRM 답할 것' })),
      ]
      const pick = pickNotify(items, current.notified ?? {}, current.sentAt ?? [], nowMs, current.notified === undefined)
      for (const n of pick.send) void $.ui.notify(n.text, { title: n.title }).catch(() => undefined)

      await update($, view, () => ({ auto, autoError, autoMtimeMs, nowMs, notified: pick.seen, sentAt: pick.sent }))
    }

    await refresh()
    $.clock.every(POLL_MS, () => void refresh())
    // 허브·워커 세션은 /lrm-board 로만 열고, 메인 체크아웃에서만 자동으로 띄움
    if (e.cwd === MAIN_CWD) {
      void $.ui.open({ id: PANE, title: TITLE })
    }

    return next(e)
  })

  on('command.run', { command: 'board-detail' }, async $ => {
    const { auto, nowMs } = await read($, view)

    return { text: detailText(auto, nowMs) }
  })

  on('command.run', { command: 'lrm-board' }, async $ => {
    await $.ui.open({ id: PANE, title: TITLE })

    return { text: 'LRM 현황 패널을 열었다.' }
  })

  on('ui.render', { component: 'Pane', requestId: PANE }, async ($, e) => {
    const { Box, Text } = $.ui.resolve(e)
    const { auto, autoError, autoMtimeMs, nowMs } = await read($, view)

    const banner = autoBanner(auto, autoMtimeMs, autoError, nowMs)
    const board = auto?.sources.board?.value ?? undefined
    const feats = featureLines(board?.features, auto?.sources.ai?.value, nowMs)
    const lines: Record<SectionId, Line[]> = {
      problem: problemLines(auto, banner, feats, nowMs),
      ask: askLines(board?.decisions, nowMs),
      todo: todoLines(board?.todos, nowMs),
      ai: [],
    }
    const head = headerLine(
      { problem: lines.problem.length, ask: lines.ask.length, todo: lines.todo.length, ai: feats.length },
      banner, autoStamp(auto, autoMtimeMs, nowMs), nowMs,
    )
    const readFail = board === undefined

    // 글머리 기호는 폭 고정 칸(3칸)에 두어 애매 폭 글자가 줄을 밀지 않게 함
    const row = (mark: string, markColor: string, text: string, color: string, tail?: string) => (
      <Box flexDirection="row">
        <Box width={3} flexShrink={0}>
          <Text color={markColor}>{mark}</Text>
        </Box>
        <Box flexGrow={1} flexShrink={1}>
          <Text color={color} wrap="wrap">{text}</Text>
        </Box>
        {tail !== undefined && tail !== '' && (
          <Box flexShrink={0} marginLeft={1}>
            <Text color={C.meta}>{tail}</Text>
          </Box>
        )}
      </Box>
    )
    const title = (id: SectionId, n: number, note?: string) => (
      <Box marginTop={1}>
        <Text wrap="wrap">
          <Text bold color={SECTION[id].color}>{`${SECTION[id].label} ${n}`}</Text>
          {note !== undefined && <Text dimColor>{`  ${note}`}</Text>}
        </Text>
      </Box>
    )
    const listed = (id: SectionId) => {
      const ls = lines[id]
      if (readFail && id !== 'problem') return <Text color={C.problem}>읽기 실패</Text>
      if (ls.length === 0) return <Text dimColor>없음</Text>
      return ls.map((l, i) => (
        <Box flexDirection="column">
          {row(id === 'problem' ? '!' : `${i + 1}`, SECTION[id].color, l.text, C.title)}
          {l.sub !== undefined && row('', C.meta, l.sub, C.detail)}
        </Box>
      ))
    }

    const block = (id: SectionId) => {
      if (id === 'problem') {
        return lines.problem.length === 0 ? null : (
          <Box flexDirection="column">
            {title('problem', lines.problem.length)}
            {listed('problem')}
          </Box>
        )
      }
      if (id === 'ai') {
        return (
          <Box flexDirection="column">
            {title('ai', feats.length, feats.length > 0 ? featureCounts(feats) : undefined)}
            {readFail ? <Text color={C.problem}>읽기 실패</Text> : feats.length === 0 ? <Text dimColor>쉬는 중</Text> :
              feats.map(f => row(f.mark, MARK_COLOR[f.mark] ?? C.meta, f.text, f.mark === '-' ? C.meta : C.title, f.age))}
          </Box>
        )
      }
      return (
        <Box flexDirection="column">
          {title(id, lines[id].length, id === 'ask' ? '막는 AI 수, 오래된 순' : undefined)}
          {listed(id)}
        </Box>
      )
    }

    return (
      <Box flexDirection="column">
        <Text bold={head.stale} color={head.stale ? C.stale : C.meta} wrap="wrap">{head.stale ? `${head.text} · ${banner.text}` : head.text}</Text>
        {PANEL_CONFIG.order.map(id => block(id))}
        <Box marginTop={1}>
          <Text dimColor>{'자세히: /board-detail'}</Text>
        </Box>
      </Box>
    )
  })
}
