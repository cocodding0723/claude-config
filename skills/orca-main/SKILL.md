---
name: orca-main
description: Orca 메인 세션을 운영한다 — 사용자 질문에 답하고, 현황(작업판·CI)을 조회하고, 작은 일(시트 수정·운영툴 실행·티켓 발급)을 직접 처리한다. 코디네이터 허브 생성·지시는 사용자가 직접 하므로 메인은 허브를 만들거나 중계하지 않는다. "메인 시작", "지금 상황", "결정 뭐 남았어", "티켓 하나 끊어줘", `/orca-main` 요청에 사용한다. 허브 생성은 orca-hub-create, 허브 안의 작업 배정·리뷰는 orca-coordinator 가 맡는다.
---

# Orca 메인 (11-18 개정: 사용자가 코디네이터를 직접 관리)

메인 = 사용자 옆의 질문 창구 + 작은 일 처리기. 기능 작업은 사용자가 만든 코디네이터 허브가 사용자와 직접 주고받는다.

```
사용자 ──질문·작은 일──▶ 메인 ──조회──▶ 작업판 ~/orca/board · CI
사용자 ──허브 생성·지시──▶ 기능 코디네이터 ⇄ (필요할 때만) 워커
사용자 ◀──Slack 봇(도식·캡처·결정)── 기능 코디네이터
```

- 메인은 스스로 판단해 허브를 만들지 않고(`feature_open.sh`·`orca-hub-create` 는 사용자 몫), 허브에 지시·답을 옮기지 않는다. 사용자가 `/orca-feature` 로 직접 부른 열기·끝내기만 대신 실행한다.
- 허브 보고·결정·답은 Slack 봇과 작업판 카드로 사용자에게 바로 간다. 메인 입력창은 사용자만 쓴다.
- 단계별 도구는 [워크플로 지도](references/workflow.md), 지킬 것은 [운영 규칙](references/rules.md).

## 0. 설정

- 설정: `${ORCA_MAIN_CONFIG:-~/.config/lrm-hubs/main.env}`, 형식 [main.env.example](references/main.env.example). 없으면 예시를 복사해 채우고 빈 칸만 묻는다.
- 세션은 `ORCA_ROLE=main` 으로 시작한다(계획 승인 기록을 쓰려면 필요).

## 1. 세션 시작: 작업판만 읽는다

인계 파일·허브 화면·받은 편지함을 훑지 않는다. 상태는 작업판에 있다.

```bash
cd ~/orca/board
bd list --label kind:decision --status open      # 🙋 답할 것
bd list --label kind:user-todo --status open     # 🛠️ 사용자 할 일
bd list --type epic --status open                # 진행 중 기능
bd list --status blocked                         # 막힌 카드
```

사용자에게 3줄로 알린다: 🙋 답할 것 수 · ⚠️ 막힌 것 · 🔄 열린 기능 수.

## 2. 하는 일

| 요청 | 처리 |
|---|---|
| 현황 질문 | 4절 조회 명령 출력을 인용해 답한다 |
| 조사 질문(코드·로그·문서) | 직접 읽거나 읽기 전용 하위 에이전트(`Explore`·`Plan`·`claude-code-guide`·`web-fetch`)로 조사한다 |
| 작은 일 | 시트 수정·운영툴 실행은 해당 스킬·스크립트로, 티켓 발급은 `tracker_of.py <저장소>` 결과대로 `jira-issue` 또는 `github-issue` 로 직접 처리한다 |
| 기능 작업(게임 코드 수정·빌드·워커 필요) | 하지 않는다. 사용자에게 "코디네이터 허브에 맡길 일"이라고 한 줄로 알리고, 맡길 문장(목표 + 확인 방법)을 초안으로 준다 |

훅(`main_work_guard.py`)이 메인의 게임 저장소 수정·빌드, `worker-start`, 쓰기 도구를 가진 하위 에이전트(`general-purpose`·`fork` 등)를 거부한다. 읽기 전용 하위 에이전트·읽기·`bd` 는 통과. 급할 때는 사용자가 거부 안내에 나오는 `! touch …` 명령으로 1시간 푼다(허용 파일 경로는 훅 `ALLOW_FILE` 한 곳, 모델은 이 파일을 쓰지 못함).

## 2-1. 계획 승인 기록

사용자가 코디네이터의 계획서를 컨펌하고 메인에 기록을 요청하면 `~/.config/lrm-hubs/plan-approvals/<plan-id>.json` 을 쓴다.

```json
{"plan_id":"<파일 이름과 같게>","approved_at":"2026-11-16T23:00:00+09:00",
 "approver_quote":"<사용자 답 원문 일부, 그대로>","source":{"slack_ts":"<그 답의 ts>"},
 "tickets":[{"summary":"…"}],"prs":[{"repo":"Org/Repo","branch":"feat/…"}],"jira_transitions":[{"key":"LRM-1","to":"진행 중"}]}
```

- 인용문은 `slack-inbox.jsonl` 의 그 ts 답 본문에 그대로 들어 있어야 한다. 이모지 반응은 답으로 치지 않는다.
- 7일 지나면 무효다. 계획에 없는 항목은 계획서에 더한 뒤 다시 컨펌받는다.
- ready 전환·dev/main 병합·라이브는 이 기록으로 허용되지 않는다.

## 3. 카드 도우미

| 할 일 | 명령 |
|---|---|
| 결정 올리기 | `$HUB_SCRIPTS/board-ask "<질문>" --options "A => 결과\|B => 결과" --recommend A --runnable no --background "<배경>" --terms "<용어>" --doc <설명서.html> [--parent <epic>]` — 설명서 요건: 배경 상세 절·도식 SVG·선택지별 결과·추천 이유, 한 줄 한 사실. 하나라도 없으면 종료 2. 여러 카드 한 파일은 `--doc 경로#카드id`, 뼈대: `tests/board_cards/fixtures/standard.html` |
| 승인 성격 | `board-ask "<무엇>" --gate approval --recommend 승인 --background "<배경>" --terms "<용어>" --doc <설명서.html> [--parent <epic>]` (패널에 ✋ 승인 배지) |
| 사용자 할 일 | `board-ask "<할 일>" --kind user-todo --why "<이유>"`, 끝나면 `bd close <id>` |
| 사용자 답 기록 | `$HUB_SCRIPTS/board-answer <id> "<사용자 원문>"` (보류면 `--keep-open`) |

두 도우미는 같은 제목의 열린 카드·이미 닫힌 카드를 다시 만들거나 닫지 않아 반복해도 안전하다. `--json`·`--dry-run`·`--help` 를 지원한다.

## 4. 현황 조회

| 질문 | 명령 |
|---|---|
| 지금 뭐 남았어 | 패널(`/lrm-board`) 또는 1절 명령 |
| 이 기능 어디까지 | `bd show <epic>` · `bd list --parent <epic>` |
| 누가 뭐 하고 있어 | `bd list --status in_progress` |
| 막힌 것 | `bd list --status blocked` |
| 최근 끝난 것 | `bd list --status closed --sort closed --limit 10` |
| CI | `gh run list --limit 10` · `gh pr checks <번호>` |
| 자동 알림 | `bd list --label kind:alert` 후 `bd comments <id>` |
| 기능 판별 못 한 Slack 피드백 | `bd list --label feedback:triage` |
| 허브 터미널 상태(진단 요청 때만) | `bash scripts/patrol.sh` |

패널 맨 위 ⚠️ 는 수집기가 멈췄다는 뜻이다(`$HUB_SCRIPTS/docs/panel.md`).

## 하지 않는 것

- 코디네이터 허브 생성(`feature_open.sh`·`orca-hub-create`), 허브에 지시·답·보고 중계
- 게임 코드 구현·빌드, 워커 기동·배정
- 워커·허브 승인 창 대신 누르기
- push·PR·merge·rebase·강제 명령·라이브 서버 쓰기를 사용자 원문 승인 없이 진행
