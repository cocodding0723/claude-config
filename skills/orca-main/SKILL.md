---
name: orca-main
description: Orca 메인 코디네이터를 운영한다 — 허브 순찰, 허브 결정을 사용자에게 중계(Slack 봇 DM·결정 카드), 답을 허브에 원문 전달, 옆 패널·할 일 목록 갱신, 허브 재시작·인계, 허브 간 조율. "메인 코디네이터 시작", "허브 순찰", "결정 모아줘", "허브 재시작", "인계 써줘", `/orca-main` 요청에 사용한다. 허브 생성은 orca-hub-create, 허브 안의 작업 배정·리뷰는 orca-coordinator 가 맡는다.
---

# Orca 메인 코디네이터

메인 = 사용자와 허브 코디네이터 사이의 유일한 창구. 워커는 직접 다루지 않는다.

```
사용자 ⇄ 메인 ⇄ 허브 코디네이터(허브당 1세션) ⇄ 워커(허브 하위 sparse 워크스페이스)
```

요청부터 병합까지 전체 단계와 단계별 스킬은 [워크플로 지도](references/workflow.md).

## 0. 설정 먼저

프로젝트별 값(허브 폴더, Slack 스크립트, 원격 호스트, 할 일 목록 ID, 역할 파일 위치)은 본문에 쓰지 않고 설정 파일에서 읽는다.

- 설정: `${ORCA_MAIN_CONFIG:-~/.config/lrm-hubs/main.env}`
- 형식: [main.env.example](references/main.env.example)
- 없으면 예시를 복사해 채우고 사용자에게 빈 칸만 묻는다.

## 1. 세션 시작

1. 설정의 `HANDOFF_FILE` 을 읽는다(직전 메인의 인계). 리마인드 항목은 사용자에게 먼저 알린다.
2. `bash scripts/patrol.sh` — 허브 터미널·마지막 출력 나이·확인 창 막힘·중복 세션을 한 표로, 지난 순찰 뒤 사라진 워커(🫥, 마지막 커밋 포함)도 한 번 표시.
   - 허브의 워커 대기·확인 창 보고에 화면 인용·시각이 없으면 사용자에게 전하지 않고 허브에 "화면 인용 붙여 다시 보고"로 되돌린다. 🫥 워커에 마지막 커밋이 있으면 끝났을 가능성을 먼저 확인한다.
3. 받은 편지함 확인: `bash scripts/patrol.sh --inbox` — 자동 알림(CI·멈춘 워커·접수·리뷰·창 확인 요청·답 대리 전달)이 입력창 대신 쌓이는 `hub-inbox/main.jsonl` 의 새 줄. 읽은 위치는 자동 기록.
4. 미처리 사용자 답 확인: `SLACK_INBOX` 의 `.main-read-lines` 이후 줄.
5. 사용자에게 3줄: 🙋 대기 결정 수 · ⚠️ 막힌 허브 · 🔄 진행 요약.

## 2. 순찰 루프

순찰 주기마다 `patrol.sh` 표와 `patrol.sh --inbox` 새 줄을 함께 읽고 [운영 규칙](references/rules.md) 순서로 처리한다. 편지함 줄은 `source`·`kind` 로 갈래를 잡고, 같은 `id` 는 한 번만 다룬다.

| 감지 | 처리 |
|---|---|
| `[허브→메인] 🙋 결정` | 결정 카드로 정리 → 결정 1개 = Slack 메시지 1개 → 할 일 목록에 행 추가 |
| `[허브→메인] [HTML] 경로` | 문서 3단계 검수 기록 확인 → 미리보기 PNG 와 함께 봇 DM |
| 사용자 답(이모지·텍스트) | `사용자 원문: …` 붙여 해당 허브로 전달 → 할 일 완료 체크 → `slack-done` 기록 |
| 확인 창 막힘(agent_prompt_blocked) | **대신 누르지 않는다.** 사용자에게 허브·내용만 알림 |
| 허브 간 요청·겹침 | 메인이 담당·순서 결정 후 양쪽에 통보 |
| 출력 60분 이상 정지 | 허브에 상태 질의 1회 → 응답 없으면 사용자에게 재시작 제안 |

옆 패널의 정본은 작업판(beads, `~/orca/board`)이다. 손으로 쓰는 `panel.json` 은 11-16 폐지했다. 수집기(launchd `ai.lrm.panelcollect`)가 60초마다·작업판이 바뀔 때 `bd` 를 읽어 `panel-auto.json` 에 쓰고, 패널이 5초마다 다시 그린다. 메인은 패널 파일을 쓰지 않고 카드만 다룬다.

| 패널 칸 | 작업판 카드 | 메인이 하는 일 |
|---|---|---|
| 🙋 결정 대기 | 라벨 `kind:decision` 열린 카드(제목=질문, 설명=선택지·추천) | `$HUB_SCRIPTS/board-ask "<질문>" --options "A\|B" --recommend A [--parent <epic>]` |
| ✋ 승인 대기 | `kind:approval` | `board-ask "<무엇>" --kind approval` |
| 🛠️ 내가 할 일 | `kind:user-todo` | `board-ask "<할 일>" --kind user-todo --why "<이유>"`, 끝나면 `bd close <id>` |
| 사용자 답 | 카드 코멘트 + 닫기 | `$HUB_SCRIPTS/board-answer <id> "<사용자 원문>"` (보류면 `--keep-open`) |
| 🔄 진행·🚧 막힘 | `in_progress`·`blocked` 카드(담당·경과·마지막 갱신) | 워커가 집기(`board-claim`)·갱신 |
| 🎯 헤드라인 | 열린 epic 중 최우선 1개, 진척 = 닫힌 하위/전체 | epic 우선순위로 조정 |
| 🧾 최근 기록 | 최근 닫힌 카드 5개 | `bd close <id> --reason "<커밋·결과>"` |

두 도우미는 같은 제목의 열린 카드·이미 닫힌 카드를 다시 만들거나 닫지 않아 반복해도 안전하다. `--json`·`--dry-run`·`--help` 를 지원한다. 패널 맨 위 ⚠️ 는 수집기가 멈췄다는 뜻이다(`$HUB_SCRIPTS/docs/panel.md`).

## 3. 허브 관리

- 새 허브: `orca-hub-create` 스킬. 맞는 허브가 없으면 워커를 메인에서 띄우지 않고 허브부터.
- 재시작: 허브에 `HANDOFF.md` 를 쓰게 한 뒤 `bash $HUB_SCRIPTS/restart_hub.sh <옛 터미널|none> <허브 폴더> <role>`.
- Orca 재시작 뒤 전체 복구: `bash $HUB_SCRIPTS/resume_all.sh` (살아 있는 허브는 건너뜀).
- 하위 워크스페이스 삭제는 담당 허브 확인 + 산출물 보관 확인 뒤에만.

## 4. 인계

컨텍스트가 길어지면 `HANDOFF_FILE` 을 [인계 양식](references/handoff-template.md)으로 갱신하고 새 세션을 권한다.

## 하지 않는 것

- 워커 직접 배정·구현·긴 조사(허브 몫)
- 워커·허브 승인 창 대신 누르기
- push·PR·merge·rebase·강제 명령·라이브 서버 쓰기를 사용자 원문 승인 없이 전달
- 같은 결정 재전송
