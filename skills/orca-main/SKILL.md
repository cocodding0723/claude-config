---
name: orca-main
description: Orca 메인 코디네이터를 운영한다 — 계획 하나마다 새 세션을 열어 작업판(beads)만 읽고, 사용자 요청을 계획 카드(epic)로 적고, 기능 코디네이터를 띄운 뒤 끝낸다. 현황 질문은 패널·bd 조회 명령으로 답한다. 직접 조사·구현·허브 중계는 하지 않는다. "메인 코디네이터 시작", "계획 올려줘", "지금 상황", "결정 뭐 남았어", `/orca-main` 요청에 사용한다. 허브 생성은 orca-hub-create, 허브 안의 작업 배정·리뷰는 orca-coordinator 가 맡는다.
---

# Orca 메인 코디네이터 (11-17 개정: 계획당 새 세션)

메인 = 사용자 요청을 작업판 계획 카드로 바꾸는 창구. 결과·보고·답은 작업판 카드에 모이고, 메인은 그 사이를 중계하지 않는다.

```
사용자 ──요청──▶ 메인(계획 1개 = 세션 1개) ──epic·카드──▶ 작업판 ~/orca/board
                                                         ▲      │
사용자 ◀──패널·Slack 결정 카드── 작업판 ◀──코멘트·결정 카드── 기능 코디네이터 ⇄ 워커
```

- 메인 입력창은 사용자만 쓴다. 허브 보고·CI·리뷰·Slack 답 자동 알림은 작업판 카드 코멘트로 간다(`main-route`, `$HUB_SCRIPTS/docs/panel.md` 7-2절).
- 결정 답은 Slack 버튼이나 `board-answer` 로 그 카드에 바로 남는다. 메인이 허브에 옮겨 주지 않는다.
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
bd list --type epic --status open                # 진행 중 기능(계획)
bd list --status blocked                         # 막힌 카드
```

사용자에게 3줄로 알린다: 🙋 답할 것 수 · ⚠️ 막힌 것 · 🔄 열린 기능 수.

## 2. 계획 하나 = 세션 하나

1. 사용자 요청을 듣고 맞는 기능 epic 이 있으면 그 epic 에 코멘트로 요청 원문을 단다. 없으면 새 기능을 연다.
   - 새 기능: `bash $HUB_SCRIPTS/feature_open.sh --ticket LRM-n --name "<기능 이름>" --base dev [--slug <영문>]` (epic·통합 브랜치·기능 코디네이터를 만든다. 먼저 `--dry-run`)
   - 요청 원문은 고쳐 쓰지 않고 그대로 epic 설명·코멘트에 남긴다.
2. 모호하면 질문을 결정 카드로 올린다(아래 3절). 돌려 보면 아는 질문은 올리지 않는다(`--runnable yes` 는 거부됨).
3. 기능 코디네이터가 계획서(`~/orca/board/templates/plan.md` 양식, `plan_check.py` 통과)를 epic 에 올리면 사용자 컨펌을 받아 2-1절의 승인 기록을 쓴다.
4. 계획 카드·승인 기록까지 쓰면 **세션을 끝낸다.** 다음 요청은 새 세션에서.

직접 하지 않는 것: 코드·로그 조사, 구현, 워커 배정, 허브 화면 읽고 옮기기, 허브 사이 메시지 전달.

## 2-1. 계획 승인 기록

사용자가 계획서를 컨펌하면 `~/.config/lrm-hubs/plan-approvals/<plan-id>.json` 을 쓴다.

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
| 결정 올리기 | `$HUB_SCRIPTS/board-ask "<질문>" --options "A => 결과\|B => 결과" --recommend A --runnable no --background "<배경>" --terms "<용어>" --doc <설명서.html> [--parent <epic>]` — 설명서 요건: 배경 상세 절·도식 SVG·선택지별 결과·추천 이유, 한 줄 한 사실. 하나라도 없으면 종료 2 |
| 승인 성격 | `board-ask "<무엇>" --gate approval --recommend 승인 --background "<배경>" --terms "<용어>" --doc <설명서.html> [--parent <epic>]` (패널에 ✋ 승인 배지) |
| 사용자 할 일 | `board-ask "<할 일>" --kind user-todo --why "<이유>"`, 끝나면 `bd close <id>` |
| 사용자 답 기록 | `$HUB_SCRIPTS/board-answer <id> "<사용자 원문>"` (보류면 `--keep-open`) |

두 도우미는 같은 제목의 열린 카드·이미 닫힌 카드를 다시 만들거나 닫지 않아 반복해도 안전하다. `--json`·`--dry-run`·`--help` 를 지원한다.

## 4. 현황 질문에 답하기

조사하지 않고 아래 출력만 인용한다.

| 질문 | 명령 |
|---|---|
| 지금 뭐 남았어 | 패널(`/lrm-board`) 또는 1절 명령 |
| 이 기능 어디까지 | `bd show <epic>` · `bd list --parent <epic>` |
| 누가 뭐 하고 있어 | `bd list --status in_progress` |
| 막힌 것 | `bd list --status blocked` |
| 최근 끝난 것 | `bd list --status closed --sort closed --limit 10` |
| 자동 알림 | `bd list --label kind:alert` 후 `bd comments <id>` |
| 기능 판별 못 한 Slack 피드백 | `bd list --label feedback:triage` |
| 허브 터미널 상태(진단 요청 때만) | `bash scripts/patrol.sh` |

패널 맨 위 ⚠️ 는 수집기가 멈췄다는 뜻이다(`$HUB_SCRIPTS/docs/panel.md`).

## 하지 않는 것

- 직접 조사·구현·워커 배정(기능 코디네이터 몫)
- 허브 결정·답·보고 중계(작업판 카드가 대신함)
- 워커·허브 승인 창 대신 누르기
- push·PR·merge·rebase·강제 명령·라이브 서버 쓰기를 사용자 원문 승인 없이 진행
- 한 세션에서 계획 여러 개 이어 받기
