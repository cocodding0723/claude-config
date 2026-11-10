# 결정·할 일 칸반(board) 설계

메인 코디네이터가 사용자에게 묻는 결정과 맡긴 할 일을 한 곳에서 상태로 관리하는 구조를 적는다. 작성일은 2026-11-10 이다.

## 쉬운 말 3줄 요약

- 무엇이 바뀌나: 결정·할 일마다 Orca 작업(task) 하나를 만들고, 결정에는 결정 관문(gate)을 붙인다. 답했는지·끝났는지는 Orca 가 기억하고, Slack 재알림·옆 패널·HTML 보드는 모두 이것만 읽는다.
- 왜: 지금 재알림은 "Slack 반응이 있나·`slack-done.txt` 에 적혔나"만 봐서, 메인이 대화로 처리한 결정도 계속 "확인 필요"로 다시 온다. 상태를 한 곳에 두면 이미 처리한 항목은 다시 묻지 않는다.
- 사용자가 할 일: 없음. 메인이 `board.sh init` 으로 칸반 Run 을 한 번 만들고, 오늘 보낸 결정을 `board_import.sh --apply` 로 옮긴 뒤 사용자 답과 대조한다.

## 1. 구성

```text
 메인 터미널(쓰기 전용 자리)                Orca 정본                       읽기만 하는 쪽
 ─────────────────────────────            ─────────────────             ─────────────────────────────
 board.sh decide|todo ──task-create──▶     Run(칸반)                     slack_pending.sh(2시간 재알림·"대기" 명령)
            │          gate-create ──▶      ├ task  (할 일·결정)  ◀──읽기── panel_collect.py(옆 패널 칸반 4열)
            └─ 알림 스크립트(Slack 전송,    └ gate  (결정 관문)   ◀──읽기── board.sh list|show|html|sync
               '참고: 보드 task_…')
 board.sh answer|start|done|cancel ──gate-resolve·task-update──▶
            └─ Slack ✅ 반응 + 스레드 한 줄 + 할 일 목록 체크

 Slack 이모지 답 ──폴러(slack_poll.sh)──▶ slack-inbox.jsonl(기록만) ──▶ board.sh sync(목록) → --apply(메인이 resolve)
```

- 쓰기(task-create·gate-create·gate-resolve·task-update)는 메인 터미널의 `board.sh` 한 곳에서만 한다. Orca 는 Run 을 만든 터미널 신원으로 쓰기를 막는다(다른 터미널은 `consumer_fenced`). `board.sh` 는 이 오류를 "메인 터미널에서 실행하세요"로 바꿔 보여 준다.
- 읽기(task-list·gate-list `--run`)는 어디서나 된다. 폴러·재알림·패널은 읽기만 하므로 펜스에 걸리지 않는다.
- 폴러는 이모지 답을 inbox 에 기록만 하고 resolve 하지 않는다. 폴러는 launchd·소켓 수신기 프로세스라 메인 터미널이 아니고, 답 해석을 메인이 한 번 보고 확정해야 잘못된 해석이 바로 확정되지 않는다.

## 2. 데이터

| 무엇 | 어디에 | 비고 |
|---|---|---|
| Run id | `LRM_BOARD_RUN` 환경 변수 → 없으면 `~/.config/lrm-hubs/board-run` 파일 | `board.sh init` 이 `run-current`(바인딩된 Run) 또는 `run-create` 로 정해 파일에 씀. 설정은 `board.py` 의 `Conf` 한 곳 |
| 제목 | task `task_title` | 같은 제목 판정은 이모지·굵게·공백을 뺀 값으로 비교 |
| 메타 | task `spec` 마지막 줄 `board-meta: {"kind":"decide","hub":"ui","ts":…,"channel":…}` | 파싱은 `board.py` 의 `parse_meta()` 한 곳 |
| Slack ts·채널(새 항목) | `slack-sent.jsonl` 기록의 `board_id` | 아래 「판단」 참고 |
| 답 원문 | gate `resolution` | 할 일·진행·완료 메모는 task `result` |
| 이모지 답 | `slack-inbox.jsonl` 반응 줄의 `board_id` | 폴러가 보낸 기록에서 옮겨 적음 |

판단: task spec 은 만든 뒤 고칠 수 없다. 항목 id 를 Slack 메시지 '참고:' 줄에 넣으려면 task 를 먼저 만들어야 하므로, 새 항목의 Slack ts 는 만들 때 알 수 없다. 그래서 새 항목은 알림 스크립트가 보낸 기록(`slack-sent.jsonl`)에 `board_id` 를 남기고 `parse_meta()` 가 둘을 합친다. 이관 항목은 ts 를 이미 알므로 spec 메타에 바로 넣는다.

## 3. 상태

```text
            board.sh decide                       board.sh todo
                 │                                      │
                 ▼                                      ▼
   ┌───────────────────────────┐            ┌──────────────────────┐
   │ 대기  task=blocked         │            │ 대기  task=pending    │
   │       gate=pending        │            └──────────┬───────────┘
   └────────────┬──────────────┘                       │
        answer (gate-resolve)                          │
                ▼                                      │
   ┌───────────────────────────┐                       │
   │ 답함  task=ready           │                       │
   │       gate=resolved       │                       │
   └────────────┬──────────────┘                       │
                └──────────── start ───────────────────┤
                                ▼                      │
                   ┌───────────────────────┐           │
                   │ 진행  task=dispatched  │           │
                   └──────────┬────────────┘           │
                    done      │      cancel            │
                ┌─────────────┴──────────────┐         │
                ▼                            ▼         │
   ┌──────────────────────┐   ┌───────────────────────────────┐
   │ 완료 task=completed  │   │ 취소 task=failed, result=     │
   └──────────────────────┘   │      "cancelled: 원문"         │
                              └───────────────────────────────┘
   대기·답함에서도 done·cancel 가능. 대기 중 결정에 done·cancel 하면 gate 를 먼저 같은 원문으로 resolve 한다.
```

Orca 에는 "답함"·"취소" 상태가 없어 아래처럼 맞춘다. 판정은 `board.py` 의 `item_state()` 한 곳이다.

| 칸반 상태 | Orca 값 | 판정 |
|---|---|---|
| 대기 | 결정: gate `pending`(task `blocked`) · 할 일: task `pending`/`ready`, gate 없음 | pending gate 가 있으면 대기 |
| 답함 | gate `resolved` + task `ready`/`pending`/`blocked` | resolved gate 만 있고 끝나지 않음 |
| 진행 | task `dispatched` | `start` 가 넣음 |
| 막힘 | task `blocked`(pending gate 없음) 또는 `failed`(결과가 `cancelled` 로 시작하지 않음) | 사람이 풀어야 함 |
| 완료 | task `completed` | |
| 취소 | task `failed` + result `cancelled…` | Orca `task-update --help` 의 취소 규약(failed + cancelled) |

잘못된 전이(완료·취소된 항목을 다시 바꾸기, 할 일에 `answer`, 이미 답한 결정에 `answer`)는 Orca 를 부르기 전에 거부하고 종료 코드 3 을 준다.

## 4. 기존 코드와의 관계

| 기존 | 바뀌는 것 |
|---|---|
| `slack_notify.sh` | `--board-ref <id>` 추가: '참고:' 줄에 `보드 <id>` 를 붙이고 보낸 기록에 `board_id` 를 남김. 다른 호출은 그대로 |
| `slack_poll.sh`(launchd 2분 + 소켓 수신기가 같은 스크립트를 부름) | 반응·답글 줄에 보낸 기록의 `board_id` 를 옮겨 적음. resolve 는 안 함 |
| `slack_pending.sh`(2시간 재알림·"대기" 명령) | Run 이 설정돼 있으면 `gate-list --status pending` 만 대기 목록에 올림. Run 이 없으면 옛 판정 그대로. Run 은 있는데 Orca 읽기가 실패하면 목록을 보내지 않고 실패로 끝냄(답한 것을 다시 묻는 것보다 안 묻는 쪽이 낫다) |
| `panel_collect.py` | `sources.board` 추가(대기·진행·막힘·완료 오늘 4열). `sources.pending` 도 Run 이 있으면 pending gate 기준 |
| `slack-done.txt`·`slack-list-map.tsv` | 그대로 읽음. 할 일 목록 행 체크는 `slack-list-map.tsv` 의 ts→행 id 를 써서 `slackLists.items.update` 로 함 |

## 5. 대안과 선택 근거

| 안 | 장점 | 단점 | 결정 |
|---|---|---|---|
| 직접 만든 원장(`board.jsonl`, 파일 잠금) | Orca 없이 동작 | 상태 정본이 Orca 칸반과 둘로 갈림. 잠금·접기 코드를 새로 유지 | 폐기(11-10 메인 지시) |
| sqlite 원장 | 동시 쓰기 안전 | 위와 같은 이중 정본 문제. 사람이 grep 으로 못 봄 | 기각 |
| Orca task·gate 정본 + `board.sh` 래퍼 | Orca 앱 칸반·다른 허브와 같은 상태를 봄. 쓰기 권한(펜스)을 Orca 가 강제 | "답함·취소" 상태가 없어 매핑 필요. spec 을 못 고쳐 Slack ts 를 따로 둠 | 채택 |

## 6. 한계

- `start` 는 task 를 `dispatched` 로 바꾼다. 워커 없이 이 값을 넣는 것은 `task-update --help` 가 허용 값으로 적어 둔 것만 근거다(시험 Run 에서 실험하지 않음). 거부되면 `board.sh` 가 Orca 오류를 그대로 보여 주고 항목은 바뀌지 않는다.
- 할 일 목록 체크는 체크 열 id(`SLACK_LIST_DONE_COLUMN`)와 봇 권한 `lists:write` 가 있어야 한다. 없으면 건너뛰고 한 줄 알린다.
- Slack 쪽 표시(✅·스레드 한 줄)가 실패해도 Orca 상태 변경은 되돌리지 않는다. 결과의 `slack` 칸에 실패 사유를 남긴다.
- `sync --apply` 는 반응 하나로 모인 답만 확정한다. 선택이 갈린 결정(충돌)·스레드 글 답은 목록에만 보이고 메인이 `answer` 로 원문을 넣는다.

## 7. 구현 순서

| 순서 | 커밋 | 범위 |
|---|---|---|
| 1 | 설계 | 이 문서 |
| 2 | `board.sh` 코어 | `board.py`(설정·Orca 호출·메타 파싱·상태 판정·Slack 표시)·`board.sh`·init·answer·start·done·cancel·list·show, 가짜 orca·curl 시험 |
| 3 | 알림 연동 | `board.sh decide|todo`·같은 제목 덧붙임·`slack_notify.sh --board-ref` |
| 4 | 폴러·재알림 | 폴러 `board_id` 기록·`slack_pending.sh` 대기 판정·`board.sh sync` |
| 5 | 패널 | `panel_collect.py` `sources.board`·mod 칸반 4열·`panel.md` |
| 6 | HTML 보드 | `board.sh html` |
| 7 | 이관 | `board_import.sh` |
