# 전체 워크플로 지도 (2026-11-09 실측, 11-17 계획당 새 세션으로 개정)

한 작업이 사용자 요청에서 병합까지 지나는 단계와, 단계마다 쓰는 스킬·스크립트.

## 단계

| # | 단계 | 누가 | 도구 |
|---|---|---|---|
| 1 | 요청 접수(새 메인 세션, 작업판만 읽음) → 맞는 epic 에 원문 코멘트 | 메인 | 이 스킬, `bd list` |
| 2 | 맞는 기능 없음 → 기능 열기(epic·통합 브랜치·기능 코디네이터) | 메인 | `feature_open.sh` |
| 3 | 계획서(`plan.md` 양식, `plan_check.py` 통과) 컨펌 1회 → 메인이 `plan-approvals/<plan-id>.json` 기록 → 메인 세션 종료 | 메인·기능 코디네이터 | `jira-issue` |
| 4 | 워커 워크스페이스 + 호스트 선택 | 허브 | `create_worker_ws.sh`, `pick_host.sh` |
| 5 | 구조 영향 → 구조안 승인 | 기능 코디네이터 → 작업판 결정 카드 → 사용자 | `doc-kit` + `check.py`, `board-ask` |
| 6 | 구현 | 워커 | `orca-coordinator` 배정, 모델 라우팅(common-rules) |
| 7 | 코드 정리 | 워커 | `pretty-code`, `csharp-readable-layout`, `comment-lint` |
| 8 | 독립 리뷰(다른 모델) | 허브 | `code-review`, `blast-radius` |
| 9 | 테스트 | 허브 / QA 허브 | Roslyn 컴파일 → QA 통합 워크트리 EditMode+녹화 1회, `unity-run --name "<허브>:<작업>" -- <명령>` |
| 10 | 기능 단위 로컬 커밋 | 워커 | `review-before-commit` 기준, 무인 판단은 `decision-log` |
| 11 | 완료 문서·영상 | 문서 허브 | `doc-kit`, 비주얼은 `ux-flow-board` + 전후 비교 |
| 12 | 완료 기록(epic 코멘트·`board-done --epic`, 증거 경로) + Jira 댓글 | 기능 코디네이터 | `jira-issue` 댓글 규칙 |
| 13 | 사용자 보고 | 기능 코디네이터(작업판·Slack 기능 스레드) | `hub_report.py`, 패널 |
| 14 | 사용자 검수 → fixup 반복 | 허브 | — |
| 15 | draft PR (계획서에 적힌 것만, push 는 무승인, 머지 전 `board-merge-check`) | 허브 | `create-pr`, `pr-review-game` |
| 16 | 병합 확인 → 정리 | 허브 | `worktree-gc`, 허브 자동 정리 규칙, `orphan_check.sh` |

## 상시 장치

- Slack 수신: launchd `ai.lrm.slackpoll`(폴러) + 켜져 있으면 `ai.lrm.slacksocket`(Socket Mode, 설치만 하고 기본은 꺼짐) → `slack-inbox.jsonl`
- 봇 명령 즉답: `slack_cmd.sh`
- 옆 패널: mod `lrm-board` 가 `panel-auto.json`(launchd `ai.lrm.panelcollect` 가 60초마다·작업판 변경 때 씀)을 그림. 결정·할 일 정본은 작업판(`bd`), 승인 성격은 결정 카드의 `gate:approval` 배지. 런북 `$HUB_SCRIPTS/docs/panel.md`
- 메인 대상 자동 알림: `main-route`=board 면 작업판 카드 코멘트(`board_notify.py`). 메인 입력창은 사용자만
- Slack 기능 스레드 피드백: `feedback_route.py` 가 epic 코멘트·기능 상자로 직접
- 따라잡기: `catch-up` / 현황: `work-brief`
- 반복 실수 구조화: `make-mistakes-impossible`, 문서·현실 대조: `repo-reality-check`

## 사고 후 복구

| 상황 | 처리 |
|---|---|
| Orca 재시작 | `resume_all.sh` → `patrol.sh` 로 중복 확인 → 중복은 사용자에게 닫기 요청 |
| 허브 맥락 과다 | 허브가 `HANDOFF.md` → `restart_hub.sh` |
| 메인 맥락 과다 | 없음 — 계획마다 새 세션이고 상태는 작업판에 있음 |
| 1회성 정리·강제 push | `HUB_SCRIPTS` 에 날짜 붙은 사용자 실행용 스크립트로 만들어 건넴 |
