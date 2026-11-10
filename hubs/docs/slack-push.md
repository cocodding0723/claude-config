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
terminal send --retry-request <고정 UUID> ──▶ 전달
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

화면 판별·전송 재시도·`unknown_delivery`·`unsubmitted` 는 바뀌지 않았다(`--help`).

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
3. `.slack-push.off` 가 있으면 치운 뒤 Slack 에 시험 메모 1건 → 20~60초 안에 메인 화면에 `[Slack→메인] 1건 도착`, 로그 `push ok … stages=input_accepted,turn_started`.
4. `set_main_terminal.sh` 로 형식만 맞는 다른 핸들(예: 닫은 터미널의 핸들)을 기록하고 메모 1건 → Slack 에 '메인 연결 끊김' 1회, 로그 `push hold reason=handle_gone`·`push alert reason=handle_gone result=ok`. 메모를 1건 더 보내도 알림은 늘지 않는다.
5. 진짜 핸들로 되돌림 → 로그 `push link_ok`, 보류분 전달.

## 6. 긴급 스위치

빈 파일 `~/.config/lrm-hubs/.slack-push.off` 를 만들면 전달을 끈다(알림도 나가지 않는다). 파일을 치우면 켜진다. 수집·읽음 반응은 폴러가 그대로 한다.

## 정한 것

| 주제 | 결정 | 근거 | 기각안 |
|---|---|---|---|
| 대상 선택 | `main.env` 핸들 단독 | 제목이 `list`·`show` 에 보이지 않아 제목 필터가 메인을 못 찾음 | 제목 필터 유지: 원인 미해결 상태에서 전달이 계속 막힘. 후보 1개 자동 선택: 메인을 닫으면 다른 claude 터미널이 대상이 됨 |
| 제목 | 키가 있을 때만 대조 | 키 없음을 실패로 보면 지금 환경에서 항상 보류 | 항상 요구: 위와 같음 |
| 알림 시점 | 사유마다 1회, 보내기 전에 기록 | 30초 재확인마다 알림이 쌓이는 것을 막음 | 성공했을 때만 기록: 실패가 계속되면 30초마다 재시도 |
| 갱신 위치 | `set_main_terminal.sh` 수동 실행 | `restart_hub.sh`·`resume_all.sh` 는 메인을 띄우지 않음 | 두 스크립트에 억지로 호출 추가: 메인 핸들을 모름 |

뒤집는 조건: `set-pane-title` 값이 `show` 에 안정적으로 보이게 되면 제목을 보조 조건에서 필수 조건으로 올릴 수 있다.

## 미확인

- `paneTitle` 이 안 보이는 원인. Orca 번들(`app.asar`)의 터미널 요약 코드는 저장된 pane 제목이 있을 때만 `paneTitle` 을 넣고, 고아 터미널이면 생략한다. `set-pane-title` 이 그 저장소에 남지 않는 경로가 있는 것으로 추정한다(실측 안 함).
- `orca terminal show` 를 `--terminal` 없이 부르면 "활성 터미널"을 고른다. 그것이 부른 터미널 자신인지는 확인하지 않았으므로 3절은 `list` 로 찾는 방법을 적었다.
