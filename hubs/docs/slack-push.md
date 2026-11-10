# Slack 메시지 메인 전달(push) 런북 — 대상 터미널 지정

Slack 사용자 메시지를 메인 세션에 넘기는 `slack_push` 는 이제 pane 제목이 아니라 `main.env` 에 적은 핸들 하나로만 대상을 정한다. 기준 커밋은 `a9474f9`(main), 작성일은 2026-11-10 이다. 옵션 전체는 `slack_push.sh --help` 에 있다.

## 쉬운 말 3줄 요약

- 무엇이 바뀌었나: 메인 세션 터미널을 이름표(pane 제목)로 찾지 않고, 설정 파일에 적은 터미널 번호(핸들)로 찾는다.
- 왜: 이름표를 붙여도 Orca 조회 결과에 보이지 않아 메인을 못 찾고 메시지가 계속 쌓였다.
- 사용자가 할 일: 메인 세션을 새로 띄울 때마다 `set_main_terminal.sh <새 핸들>` 을 한 번 실행한다. 잊으면 Slack 에 '메인 연결 끊김' 경고가 온다.

## 0. 알아야 할 것

용어:

- **핸들**: Orca 가 터미널마다 주는 `term_…` 형식의 이름. Orca 를 재시작하거나 세션을 새로 띄우면 바뀐다.
- **main.env**: `~/.config/lrm-hubs/main.env`. orca-main 설정 파일이기도 해서 다른 줄(프로젝트 이름·경로 등)이 함께 있다. `slack_push` 는 그중 `MAIN_TERMINAL=` 줄만 읽는다.
- **보류**: 전달하지 않고 묶음을 상태 파일에 남겨 두는 것. 다음 회차(기본 30초 뒤)에 처음부터 다시 확인한다.

전달 직전 확인 순서:

```
main.env 의 MAIN_TERMINAL 줄 ──(없음·형식 무효)──▶ 보류 no_main_handle ─┐
        │                                                                 │
orca terminal show --terminal <핸들> --json ──(핸들 무효 코드)──▶ handle_gone ┤
        │   (그 밖 오류·시간 초과·응답 불명) ─────▶ show_fail (알림 없음) │
connected·orphaned ──▶ handle_dead                                        ├─▶ '메인 연결 끊김' 알림(사유마다 1회)
writable ──────────▶ not_writable                                         │
worktreePath ──────▶ wrong_worktree (메인 체크아웃 아님)                  │
agentIdentity ─────▶ not_claude                                           │
paneTitle(있을 때만) ▶ pane_title_mismatch ──────────────────────────────┘
        │
화면 끝(preview) 판별 ──▶ dialog·draft·empty_preview·no_idle_marker (알림 없음)
        │
terminal send --retry-request <고정 UUID> ──▶ 영수증
        │
turn_started 있음 ──▶ 전달 완료(confirm=receipt)
input_accepted 만 ──▶ 접수 뒤 화면 재확인(7절) ──▶ 전달 완료(confirm=screen) 또는 보류
```

## 1. 대상 지정

`main.env` 에 다음 한 줄을 둔다. 따옴표·`export` 를 붙이면 무효다.

```
MAIN_TERMINAL=term_<16진·하이픈>
```

- 값은 `^term_[0-9a-f-]+$` 형식만 받는다. 서로 다른 값이 두 줄 이상이어도 무효다.
- 손으로 고치지 않고 `bash ~/.config/lrm-hubs/set_main_terminal.sh <핸들>` 을 쓴다. 이 도구는 `MAIN_TERMINAL` 줄만 바꾸고 다른 줄은 순서대로 보존한다. 임시 파일에 쓴 뒤 원자적으로 바꾸며 권한은 600 으로 맞춘다. 파일 내용·핸들 값은 출력하지 않는다. `main.env` 가 심볼릭 링크면 링크는 그대로 두고 최종 대상 파일을 교체한다. 교체하는 줄만 LF 로 쓰고 다른 줄은 원형대로 보존하므로 CRLF 파일에서는 줄 끝이 섞일 수 있다(읽는 쪽이 값을 strip 해 영향 없음).
- 로그(`slack-push.log`)에는 핸들 앞 13자만 남긴다(`handle=term_xxxxxxxx`).
- 시험용 환경 변수: `LRM_MAIN_ENV`(파일 경로), `LRM_PUSH_HANDLE`(핸들 직접 지정, main.env 보다 우선), `LRM_PUSH_NOTIFY`(알림 스크립트).

## 2. 판정 — 보류 사유

| 사유 | 뜻 | 알림 | 조치 |
|---|---|---|---|
| `no_main_handle` | `MAIN_TERMINAL` 줄 없음·형식 무효·값 둘 이상 | 1회 | `set_main_terminal.sh <핸들>` |
| `handle_gone` | `show` 가 핸들 무효 오류(`terminal_handle_stale`·`terminal_gone`·`terminal_not_found`)를 돌려줌, 또는 응답 handle 이 요청과 다름 | 1회 | 새 핸들로 갱신 |
| `handle_dead` | `connected` 가 true 아님 또는 `orphaned` | 1회 | 메인 세션 상태 확인 후 갱신 |
| `not_writable` | `writable` 이 true 아님 | 1회 | 메인 세션 상태 확인 |
| `wrong_worktree` | `worktreePath` 가 메인 체크아웃(`~/orca/LoveRevolutionMaker`)이 아님 | 1회 | 핸들이 다른 터미널을 가리킴. 메인 핸들로 갱신 |
| `not_claude` | `agentIdentity` 가 `claude` 가 아님(키 없음 포함) | 1회 | 같음 |
| `pane_title_mismatch` | `paneTitle` 이 있고 `MAIN` 과 다름 | 1회 | 제목을 `MAIN` 으로 고치거나 핸들 확인 |
| `show_fail` | `show` 시간 초과·응답 모양 불명·위 3개 외 오류(`runtime_unavailable` 등 Orca 쪽 일시 오류) | 없음 | 저절로 풀리지 않으면 Orca 상태 확인 |
| `dialog`·`draft`·`empty_preview`·`no_idle_marker` | 화면이 입력 대기가 아님 | 없음 | 메인 화면 정리(그대로 두면 다음 회차에 전달) |

화면 판별·전송 재시도는 바뀌지 않았다(`--help`). `unsubmitted` 는 접수 뒤 화면 재확인으로도 제출을 확인하지 못할 때만 남는다(7절).

## 3. 메인 재시작 때 갱신 절차

메인 세션을 새로 띄운 직후(Orca 재시작 포함) 한 번:

1. 메인 세션에서 자기 핸들을 찾는다. 예: `orca terminal list --worktree path:$HOME/orca/LoveRevolutionMaker --json | jq -r '.result.terminals[] | select(.agentIdentity=="claude") | .handle'`. 후보가 둘 이상이면 `preview` 로 자기 화면을 고른다.
2. `bash ~/.config/lrm-hubs/set_main_terminal.sh <핸들>` → `MAIN_TERMINAL 기록함 — main.env` 출력.
3. 다음 전달 때 `slack-push.log` 에 `push link_ok`(끊김 알림 뒤였다면) 또는 `push ok` 가 남는지 본다.

`restart_hub.sh`·`resume_all.sh` 는 허브 코디네이터만 다시 띄운다(`restart_hub.sh` 는 `workspaces/LoveRevolutionMaker/<허브>`, `resume_all.sh` 는 `coord-*-hub`). 메인을 띄우는 경로가 없어 이 두 스크립트에서 핸들을 바꾸지 않는다. 메인 재시작 절차는 저장소 밖 문서가 담는다. 다음 두 곳에 위 1~2단계를 넣어야 한다.

- orca-main 스킬: `~/.claude/skills/orca-main/SKILL.md`(재시작 절), `references/workflow.md`(Orca 재시작 줄), `references/handoff-template.md`
- 메인 인계 파일: `~/.config/lrm-hubs/MAIN-HANDOFF.md`(orca-main 설정의 `HANDOFF_FILE`)

## 4. '메인 연결 끊김' 알림

- 표의 '알림 1회' 사유로 보류하면 `slack_notify.sh --kind warn` 으로 보낸다. 본문은 무엇이 안 됐나·할 일 두 줄이고 사유 코드만 담는다. 핸들 값·메시지 본문은 넣지 않는다.
- 보낸 사유는 `~/.config/lrm-hubs/.slack-push-alert.json`(600)에 남긴다. 같은 사유는 연결이 확인될 때까지 다시 보내지 않는다. 끊긴 동안 사유가 바뀌면 새 사유만 1회 더 보낸다.
- 핸들이 메인 세션으로 확인되면(화면 상태로 보류 중이어도) 이 파일을 지우고 로그에 `push link_ok` 를 남긴다. 그 뒤 다시 끊기면 다시 알린다.
- 기록은 보내기 전에 한다. 알림이 실패해도(`push alert … result=fail_rc1` 등) 30초마다 다시 보내 Slack 을 채우지 않는다. 대신 실패한 알림은 재시도하지 않으므로 로그로 확인한다.
- 알림 실패는 전달·수집을 막지 않는다.

## 5. 실측 절차

라이브 반영(설치) 뒤 한 번. 1~3 은 정상 경로, 4~5 는 끊김 경로다.

1. 메인 세션에서 3절 1~2단계로 핸들을 기록한다.
2. `orca terminal show --terminal <핸들> --json | jq '.result.terminal | {connected, writable, orphaned, agentIdentity, worktreePath, has_pane: has("paneTitle")}'` → `connected·writable` true, `orphaned` false 또는 없음, `agentIdentity` `"claude"`, `worktreePath` 메인 체크아웃. `has_pane` 이 true 이면 값이 `MAIN` 인지 본다.
3. `.slack-push.off` 가 있으면 치운 뒤 Slack 에 시험 메모 1건 → 20~60초 안에 메인 화면에 `[Slack→메인] 1건 도착`. 로그는 둘 중 하나다.
   - `push ok … stages=input_accepted,turn_started … confirm=receipt`: Orca 가 턴 시작을 잡음.
   - `push ok … stages=input_accepted … confirm=screen screen=idle reads=N`: 턴 시작은 못 잡았고 화면으로 확인함(2026-11-10 실측 환경은 이쪽). 메인 화면에서 글이 실제로 제출돼 새 턴이 시작됐는지, 입력 칸에 같은 글이 남지 않았는지 눈으로 함께 본다.
   - `push hold reason=unsubmitted … confirm=still_draft` 또는 `confirm=unknown` 이면 7절 「보류됐을 때」를 따른다. `screen=` 값을 기록해 둔다(판별 규칙 보정 근거).
   - 메인이 작업 중일 때 메모를 1건 더 보내 `screen=idle` 로 끝나는지, 메인이 그 글을 이어서 처리하는지 본다(7절 「줄 서 있는 경우」).
4. `set_main_terminal.sh` 로 형식만 맞는 다른 핸들(예: 닫은 터미널의 핸들)을 기록하고 메모 1건 → Slack 에 '메인 연결 끊김' 1회, 로그 `push hold reason=handle_gone`·`push alert reason=handle_gone result=ok`. 메모를 1건 더 보내도 알림은 늘지 않는다.
5. 진짜 핸들로 되돌림 → 로그 `push link_ok`, 보류분 전달.

## 6. 긴급 스위치

빈 파일 `~/.config/lrm-hubs/.slack-push.off` 를 만들면 전달을 끈다(알림도 나가지 않는다). 파일을 치우면 켜진다. 수집·읽음 반응은 폴러가 그대로 한다.

## 7. 접수 뒤 화면 재확인

Orca 영수증에 `turn_started` 가 없어도 화면으로 제출을 확인해 전달 완료로 본다. 2026-11-10 라이브 메인에서 `--wait-submit 10`·`30` 으로 보낸 글이 실제로 제출돼 새 턴이 시작됐는데도 영수증은 `stages=["input_accepted"]` 뿐이었다(`provider="claude"`, `observation="supported"`). 같은 `--retry-request` 로 다시 물어도 Orca 는 처음 영수증을 그대로 돌려줬다(`mutation.replayed=true`). 그래서 기존 규칙(`turn_started` 필수)으로는 매번 `unsubmitted` 로 막혔다.

### 순서

```
영수증 accepted=true, stages 에 input_accepted, turn_started 없음
        │  (그 밖: unknown·refused·blocked·retry 는 기존 처리)
LRM_PUSH_CONFIRM=0|off ──▶ 기존대로 unsubmitted 보류(confirm=off)
        │
반복: LRM_PUSH_CONFIRM_TICK 초마다(기본 2초, 최소 1초), LRM_PUSH_CONFIRM 초 동안(기본 20초, 최소 2회)
  main.env 핸들 = 보낸 핸들?             아니면 ──▶ unknown_delivery (confirm=changed)
  orca terminal show --terminal <핸들> --json  (읽기만)
    핸들 무효 오류·handle 불일치·끊김·고아·worktreePath·agentIdentity·제목 불일치 ──▶ unknown_delivery (confirm=changed)
    쓰기 불가(not_writable) ──▶ 일시 상태. 이번 읽기는 판별 불가(screen=not_writable)로만 처리하고 계속 읽음
  화면 끝(preview) 분류 ──▶ 제출 | 글 남음 | 판별 불가
    제출이 연속 2회(첫 판정과 1초 이상 간격) ──▶ 전달 완료(confirm=screen): 읽음 표식·전달 기록 갱신(기존 경로)
        │
시간 끝 ──▶ unsubmitted 보류 (마지막 비제출 관찰이 글 남음이면 confirm=still_draft, 그 밖 confirm=unknown)
```

### 판정표

| 화면 끝 | 판정 | 로그 `screen=` |
|---|---|---|
| 입력 줄(`❯`·`>`)이 비어 있고 아래는 테두리·짧은 상태줄뿐 (전송 전 strict 판별의 `idle` 과 같은 규칙) | 제출 | `idle` |
| 입력 줄에 `[Pasted text #N +M lines]` | 글 남음 | `pasted` |
| 입력 줄에 그 밖의 글(우리 글 첫 줄·인용 줄 등), 셸 모드 `!` | 글 남음 | `typed` |
| 작업 중 표식이 보여도 입력 칸에 글이 있음 | 글 남음 | `pasted`·`typed` |
| 입력 칸이 안 보이고 `esc to interrupt` 만 보임(우리 글 끝부분·긴 줄·도구 출력 안의 같은 문구 포함) | 판별 불가. 표식은 작업 중일 뿐 제출 증거가 아님 | `no_box` |
| preview 비어 있음 / 키 없음 | 판별 불가 | `empty` / `no_preview` |
| 권한·선택 메뉴 | 판별 불가 | `dialog` |
| 같은 터미널이 일시적으로 쓰기 불가 | 판별 불가(다른 터미널로 보지 않음) | `not_writable` |
| show 시간 초과·일시 오류 | 판별 불가 | `show_fail` |

- 양성 근거(입력 칸이 보이고 비어 있음)가 있을 때만 제출로 본다. 이 단계의 오판은 메인이 못 본 메시지를 읽음 처리하는 것이라 불확실하면 보류한다.
- 전송 전에 strict 판별로 입력 칸이 비어 있음을 확인했으므로 접수 뒤 칸에 보이는 글은 우리 글로 본다. 글 내용은 비교하지 않는다(로그·메모리에 화면 본문을 두지 않기 위해).
- 한 번 본 화면으로 확정하지 않는다. 붙여 넣기가 화면에 그려지기 직전에는 입력 칸이 잠깐 비어 보일 수 있어서 제출 판정이 연속 2회이고 두 판정 사이가 1초 이상일 때만 확정한다. 중간에 다른 판정이 끼면 처음부터 다시 센다.
- 로그에는 `confirm`·`screen`·`reads`(읽은 횟수)와 영수증 `stages`·`obs` 만 남긴다. 메시지·화면 본문은 남기지 않는다.

### 환경 변수

| 변수 | 기본 | 범위 | 뜻 |
|---|---|---|---|
| `LRM_PUSH_CONFIRM` | 20 | 1~120, `0`·`off` 면 끔 | 재확인 시간(초). 끄면 `turn_started` 필수(이전 동작) |
| `LRM_PUSH_CONFIRM_TICK` | 2 | 1~30 | 재확인 간격(초). 시간이 짧아도 최소 2회는 읽는다. 렌더 지연 방어라 1초 아래로 줄이지 못한다(`LRM_PUSH_TEST_CONFIRM_GAP` 은 시험 전용) |

### 줄 서 있는 경우

메인이 이미 다른 작업 중일 때 Claude Code 는 제출한 글을 대기 입력으로 줄 세우고 입력 칸을 비운다. 작업 중에도 Claude Code 는 입력 칸(빈 `❯` 줄과 테두리)을 그대로 보여 주므로 `screen=idle` 로 전달 완료가 된다. 이 경우 Claude 가 지금 작업을 끝낸 뒤 그 글을 처리하므로 전달 완료로 보는 것이 맞다고 판단했다. 대기 입력 표시가 어떤 모양이든 판정은 입력 칸이 비었는지만 본다. 따라서 대기 표시를 못 알아봐서 글이 사라진 것으로 오판하는 경로는 없다. 대기 표시가 입력 칸 안처럼 보이면 `still_draft` 쪽(보류)으로 기운다.

### 위험

| 위험 | 결과 | 대응 |
|---|---|---|
| Orca 가 `input_accepted` 를 돌려줬지만 실제로는 글이 입력 칸에 그려지지 않음 | 입력 칸이 계속 비어 있어 전달 완료로 오판 | 실측에서 본 적 없음. `input_accepted` 는 PTY 쓰기 완료라 가능성이 낮다고 판단. 의심되면 `LRM_PUSH_CONFIRM=0` |
| 사람이 같은 시각에 입력 칸을 직접 비움 | 전달 완료로 오판 | 드묾. 화면 확인은 20초 안쪽만 함 |
| 실제 화면 모양이 시험의 표식과 다름(입력 줄 기호·자리표시 문구·`esc to interrupt`) | 대부분 `unknown`·`still_draft` 로 보류(fail-closed). 빈 입력 줄을 다른 모양으로 오판하는 경우만 오전달 위험 | 5절 3단계의 라이브 실측으로 `screen=` 값 확인 |
| preview 가 끝 300자 안팎이라 긴 테두리 줄이 입력 칸을 밀어냄 | `no_box` 로 보류 | 보류 로그의 `screen=` 로 확인 후 규칙 보정 |
| 입력 칸이 안 보이는데 `esc to interrupt` 만 보고 제출로 판정(우리 글 끝부분·긴 줄 때문에 칸 판별 불가인 글·도구 출력 안의 같은 문구) | Enter 가 삼켜져 글이 칸에 남았는데도 읽음 처리돼 메인이 못 본 메시지가 유실됨(독립 리뷰가 3가지 화면으로 재현) | 표식 단독 판정을 제거했다. 입력 칸을 못 찾으면 `no_box` 로 보류 |
| 입력 칸이 비어 보이지만 접수된 글을 Claude 가 입력으로 쓰지 않고 버림(양성 근거 없음) | 같은 화면이라 전달 완료로 오판 | 수용한 위험. 우리 글 머리줄·작업 중 표식 같은 양성 근거 가산은 하지 않았다. 5절 실측에서 `screen=`·`reads=` 분포를 본다 |
| 커서 블록 글리프(`❯ █`) 등 실제 빈 입력 줄과 다른 모양 | 글로 보아 `still_draft` 로 보류(안전한 쪽) | 5절 실측에서 `screen=` 확인 |

### 보류됐을 때

`confirm=still_draft`·`confirm=unknown`·`confirm=changed` 모두 자동 재전송하지 않는다(재전송하면 입력 칸에 같은 글이 겹친다). 메인 화면을 보고 `--help` 「자동 재시도 없는 보류」 절차대로 푼다. 입력 칸에 글이 남아 있으면 Enter 로 직접 제출한 뒤 읽음 표식을 올린다.

## 정한 것

| 주제 | 결정 | 근거 | 기각안 |
|---|---|---|---|
| 대상 선택 | `main.env` 핸들 단독 | 제목이 `list`·`show` 에 보이지 않아 제목 필터가 메인을 못 찾음 | 제목 필터 유지: 원인 미해결 상태에서 전달이 계속 막힘. 후보 1개 자동 선택: 메인을 닫으면 다른 claude 터미널이 대상이 됨 |
| 제목 | 키가 있을 때만 대조 | 키 없음을 실패로 보면 지금 환경에서 항상 보류 | 항상 요구: 위와 같음 |
| 알림 시점 | 사유마다 1회, 보내기 전에 기록 | 30초 재확인마다 알림이 쌓이는 것을 막음 | 성공했을 때만 기록: 실패가 계속되면 30초마다 재시도 |
| 갱신 위치 | `set_main_terminal.sh` 수동 실행 | `restart_hub.sh`·`resume_all.sh` 는 메인을 띄우지 않음 | 두 스크립트에 억지로 호출 추가: 메인 핸들을 모름 |
| 턴 시작 미확인 | 접수 뒤 화면을 다시 읽어 입력 칸이 비었으면 전달 완료(사용자 결정, 2026-11-10) | Orca 가 Claude 의 턴 시작을 못 잡아 실제 제출돼도 매번 보류됨 | 자동 재전송: 입력 칸에 글이 겹침. 계속 보류: 전달이 사실상 멈춤 |
| 확정 규칙 | 제출 판정 연속 2회 | 붙여 넣기가 그려지기 전 순간의 빈 입력 칸을 오판하지 않음 | 1회 확정: 위 순간을 오판. 3회 이상: 확인 시간만 늘고 이득 적음 |
| 작업 중 표식 | 제출 근거로 쓰지 않음. 판정은 보이는 빈 입력 칸만 봄 | 표식은 작업 중일 뿐 제출 증거가 아님. 작업 중에도 입력 칸은 보이므로 줄 선 글은 빈 칸으로 판정됨 | 칸이 안 보일 때의 표식 단독 판정: 기각(독립 리뷰가 Enter 삼킴·칸 밀림·도구 출력 문구 3모양에서 오판을 재현해 제거) |
| 확인 중 쓰기 불가 | 판별 불가로만 처리, 계속 읽음 | 같은 터미널의 일시 상태. 터미널이 바뀐 근거도 제출 근거도 아님 | `changed` 로 보류: 일시 상태에 사람 확인을 요구함. 그대로 판정: 근거 없는 화면으로 확정 |
| 확인 간격 | 연속 판정 사이 1초 이상, 간격 하한 1초 | 영수증 관찰이 즉시 반환돼도 렌더 지연을 막음 | 0.05초 하한: 두 읽기가 같은 순간이라 연속 2회가 무의미 |
| 확인 중 터미널 바뀜 | `unknown_delivery`(사람 확인) | 다른 터미널 화면으로 판정하면 근거가 없음 | 계속 확인: 엉뚱한 화면을 제출 근거로 씀 |

뒤집는 조건: `set-pane-title` 값이 `show` 에 안정적으로 보이게 되면 제목을 보조 조건에서 필수 조건으로 올릴 수 있다.

## 미확인

- **라이브 실측 필요(7절)**: 시험의 화면 표식(`❯` 입력 줄, `─` 테두리, `⏵⏵ … (shift+tab to cycle)` 상태줄, `(esc to interrupt)`, `[Pasted text #1 +12 lines]`)은 관찰된 모양을 옮긴 추정이며 실제 preview 로 검증하지 않았다. 특히 ① 접수 직후 preview 에 입력 칸이 보이는지 ② 줄 선 대기 입력이 입력 칸 밖에 보이는지 ③ `--wait-submit` 이 끝난 시점에 이미 입력 칸이 비어 있는지(그러면 `reads=2` 로 끝남)를 5절 3단계에서 확인한다. 양성 근거를 더하지 않은 수용 위험(위험 표)과 커서 블록 글리프는 실측의 `screen=`·`reads=` 분포로 확인한다. 바쁜 메인의 상태줄에 `esc to interrupt` 가 실제로 있는지, preview 가 지연 없이 최신인지도 함께 본다.
- `turn_started` 를 못 잡는 원인(Orca 쪽 관찰기와 Claude Code 화면의 차이)은 조사하지 않았다. Orca 가 고쳐지면 `confirm=receipt` 로 바뀌며 화면 확인은 실행되지 않는다.
- `paneTitle` 이 안 보이는 원인. Orca 번들(`app.asar`)의 터미널 요약 코드는 저장된 pane 제목이 있을 때만 `paneTitle` 을 넣고, 고아 터미널이면 생략한다. `set-pane-title` 이 그 저장소에 남지 않는 경로가 있는 것으로 추정한다(실측 안 함).
- `orca terminal show` 를 `--terminal` 없이 부르면 "활성 터미널"을 고른다. 그것이 부른 터미널 자신인지는 확인하지 않았으므로 3절은 `list` 로 찾는 방법을 적었다.
