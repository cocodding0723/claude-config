import { atom, read, update } from 'claude-code'
import type { Register } from 'claude-code'

import type { AutoBoard, BoardView, EpicView } from '../types'
import {
  PANEL_CONFIG,
  askLines,
  autoBanner,
  autoStamp,
  applyEpicToggle,
  detailText,
  epicOpen,
  epicRows,
  parseEpicView,
  featureCounts,
  featureLines,
  headerLine,
  parseAuto,
  pickNotify,
  problemLines,
  todoLines,
  workerCounts,
  workerLines,
} from './board'
import type { Line, SectionId } from './board'

// 사용자에게 필요한 4칸만(11-17): 🚨 문제(있을 때만) · 🙋 답할 것 · 🔧 직접 할 일 · 🔄 AI 진행. 칸 순서·임계값은 board.ts PANEL_CONFIG.
// 허브 표·PR·쉬는 자원·최근 기록·원료 상태는 /board-detail 로만 봄(수집기 데이터는 그대로)
const PANE = 'lrm-board'
const TITLE = 'LRM 현황'
// 수집기(launchd ai.lrm.panelcollect)가 60초마다·작업판 변경 때 쓰는 자동 칸. 결정·할 일·헤드라인의 정본은 작업판(bd). 승인은 결정 대기 칸의 배지
const AUTO_SOURCE = '__HOME__/.config/lrm-hubs/panel-auto.json'
// 에픽 펼침 상태 파일(경로는 여기 한 곳). 패널을 다시 열어도 유지됨
const VIEW_FILE = '__HOME__/.config/lrm-hubs/panel-view.json'
const POLL_MS = 5000
const MAIN_CWD = '__HOME__/orca/LoveRevolutionMaker'

// 에픽 펼침 상태를 더해 모양이 바뀜: view-5. 옛 값은 초기값으로 읽힘
const view = atom(
  { plugin: 'lrm-board', key: 'view' } as const,
  { autoMtimeMs: 0, nowMs: 0 } as BoardView,
  { shape: 'view-5' },
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

const MARK_COLOR: Record<string, string> = { '?': '#FFCA28', '!': '#EF5350', '✕': '#EF5350', '✻': '#66BB6A', '✓': '#90A4AE', '◐': '#66BB6A', '○': '#9E9E9E', '✕': '#EF5350', '-': '#616161' }

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
      name: 'board-epics',
      description: '에픽 아래 하위 작업 펼치기 on/off. 인자: on · off · (없음=전체 반전) · 에픽 id(그 에픽만 반전)',
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

      // 펼침 상태 파일: 수정 시각이 바뀌었을 때만 다시 읽음(다른 세션에서 바꾼 것도 반영)
      const viewStat = await $.fs.stat(VIEW_FILE).catch(() => undefined)
      const viewMtimeMs = viewStat?.mtimeMs ?? 0
      let epicView: EpicView | undefined = current.epicView
      if (epicView === undefined || viewMtimeMs !== current.viewMtimeMs) {
        epicView = parseEpicView(viewStat === undefined ? undefined : await $.fs.read(VIEW_FILE).catch(() => undefined))
      }

      // 글자는 분 단위라 파일이 그대로면 분이 바뀔 때만 다시 그림(5초마다 그리지 않음)
      const sameMinute = Math.floor(nowMs / 60000) === Math.floor(current.nowMs / 60000)
      if (auto === current.auto && autoMtimeMs === current.autoMtimeMs && autoError === current.autoError && sameMinute && viewMtimeMs === current.viewMtimeMs) return

      // 데스크톱 알림: 🙋·🚨 새 항목만, 같은 항목 재전송 막기·시간당 상한(PANEL_CONFIG)
      const banner = autoBanner(auto, autoMtimeMs, autoError, nowMs)
      const feats = featureLines(auto?.sources.board?.value?.features, auto?.sources.ai?.value, nowMs)
      const items = [
        ...problemLines(auto, banner, feats, nowMs).map(l => ({ key: l.key, text: l.text, head: '🚨 LRM 문제' })),
        ...askLines(auto?.sources.board?.value?.decisions, nowMs).map(l => ({ key: l.key, text: l.text, head: '🙋 LRM 답할 것' })),
      ]
      const pick = pickNotify(items, current.notified ?? {}, current.sentAt ?? [], nowMs, current.notified === undefined)
      for (const n of pick.send) void $.ui.notify(n.text, { title: n.title }).catch(() => undefined)

      await update($, view, () => ({ auto, autoError, autoMtimeMs, nowMs, notified: pick.seen, sentAt: pick.sent, epicView, viewMtimeMs }))
    }

    await refresh()
    $.clock.every(POLL_MS, () => void refresh())
    // 허브·워커 세션은 /lrm-board 로만 열고, 메인 체크아웃에서만 자동으로 띄움
    if (e.cwd === MAIN_CWD) {
      void $.ui.open({ id: PANE, title: TITLE })
    }

    return next(e)
  })

  on('command.run', { command: 'board-epics' }, async ($, e) => {
    const { auto, epicView } = await read($, view)
    const ids = (auto?.sources.board?.value?.features ?? []).filter(f => f.closedAt === null).map(f => f.id)
    const r = applyEpicToggle(epicView ?? parseEpicView(undefined), e.args, ids)
    if (r.error !== undefined) return { text: r.error }
    try {
      await $.fs.write(VIEW_FILE, `${JSON.stringify(r.view, null, 2)}\n`)
    } catch (err) {
      return { text: `상태 파일 쓰기 실패(${VIEW_FILE}): ${String(err)}. 폴더 권한을 확인한다.` }
    }
    const st = await $.fs.stat(VIEW_FILE).catch(() => undefined)
    await update($, view, cur => ({ ...cur, epicView: r.view, viewMtimeMs: st?.mtimeMs ?? 0 }))

    return { text: `에픽 하위 작업: 기본 ${r.view.all ? '펼침' : '접힘'}, 예외 ${Object.keys(r.view.epics).length}개` }
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
    const { auto, autoError, autoMtimeMs, nowMs, epicView } = await read($, view)
    const ev = epicView ?? parseEpicView(undefined)

    const banner = autoBanner(auto, autoMtimeMs, autoError, nowMs)
    const board = auto?.sources.board?.value ?? undefined
    const feats = featureLines(board?.features, auto?.sources.ai?.value, nowMs)
    const agents = auto?.sources.agents
    const workers = workerLines(agents?.value, nowMs)
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
    const anyOpen = (board?.features ?? []).some(x => x.closedAt === null && x.total > 0 && epicOpen(ev, x.id))

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
            {title('ai', feats.length, feats.length > 0 ? `${featureCounts(feats)}${anyOpen ? ' · 접기: /board-epics off' : ''}` : undefined)}
            {readFail ? <Text color={C.problem}>읽기 실패</Text> : feats.length === 0 ? <Text dimColor>쉬는 중</Text> :
              feats.map(f => {
                const src = (board?.features ?? []).find(x => `ai:${x.id}` === f.key && x.closedAt === null)
                if (src === undefined) return row(f.mark, MARK_COLOR[f.mark] ?? C.meta, f.text, f.mark === '-' ? C.meta : C.title, f.age)
                const rows = epicRows(src, epicOpen(ev, src.id), nowMs)
                return (
                  <Box flexDirection="column">
                    {row(f.mark, MARK_COLOR[f.mark] ?? C.meta, f.text, C.title, f.age)}
                    {rows.bar !== undefined && row('', C.meta, rows.bar, C.meta)}
                    {rows.kids.map(k => (
                      <Box flexDirection="column">
                        {row(k.mark, MARK_COLOR[k.mark] ?? C.meta, `  ${k.text}`, k.mark === '✓' ? C.meta : C.detail, k.age)}
                        {k.sub !== undefined && row('', C.meta, `  ${k.sub}`, k.warn ? C.stale : C.meta)}
                      </Box>
                    ))}
                  </Box>
                )
              })}
            {agents?.value && (
              <Box marginTop={feats.length > 0 ? 1 : 0}>
                <Text dimColor>{workerCounts(agents.value)}</Text>
              </Box>
            )}
            {agents !== undefined && !agents.ok && <Text dimColor>{`워커 목록 ⚠ 수집 실패(${agents.err ?? '?'})`}</Text>}
            {workers.map(w => row(w.mark, w.done ? C.meta : C.ai, w.text, w.done ? C.detail : C.title, w.age))}
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
          <Text dimColor>{'자세히: /board-detail · 하위 작업 펼치기: /board-epics [on|off|에픽 id]'}</Text>
        </Box>
      </Box>
    )
  })
}
