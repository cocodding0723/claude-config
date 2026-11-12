# 전체 워크플로 지도 (2026-11-09 실측)

한 작업이 사용자 요청에서 병합까지 지나는 단계와, 단계마다 쓰는 스킬·스크립트.

## 단계

| # | 단계 | 누가 | 도구 |
|---|---|---|---|
| 1 | 요청 접수·담당 허브 결정 | 메인 | 이 스킬, `patrol.sh` |
| 2 | 맞는 허브 없음 → 허브 생성 | 메인 | `orca-hub-create` (`create_hub.sh`) |
| 3 | Jira 선발급(게임 작업) / `[개인]` 이름(개인 업무) | 허브 | `jira-issue` |
| 4 | 워커 워크스페이스 + 호스트 선택 | 허브 | `create_worker_ws.sh`, `pick_host.sh` |
| 5 | 구조 영향 → 구조안 승인 | 허브→메인→사용자 | `doc-kit` + `check.py`, 결정 카드 |
| 6 | 구현 | 워커 | `orca-coordinator` 배정, 모델 라우팅(common-rules) |
| 7 | 코드 정리 | 워커 | `pretty-code`, `csharp-readable-layout`, `comment-lint` |
| 8 | 독립 리뷰(다른 모델) | 허브 | `code-review`, `blast-radius` |
| 9 | 테스트 | 허브 / QA 허브 | Roslyn 컴파일 → QA 통합 워크트리 EditMode+녹화 1회, `unity_slot.sh` |
| 10 | 기능 단위 로컬 커밋 | 워커 | `review-before-commit` 기준, 무인 판단은 `decision-log` |
| 11 | 완료 문서·영상 | 문서 허브 | `doc-kit`, 비주얼은 `ux-flow-board` + 전후 비교 |
| 12 | 완료 기록(`hub_report.py add --kind done`, 증거 경로) + Jira 댓글 | 허브 | `jira-issue` 댓글 규칙 |
| 13 | 사용자 보고 | 메인 | `slack_notify.sh` + `html_preview.sh`, `slack_pending.sh` |
| 14 | 사용자 검수 → fixup 반복 | 허브 | — |
| 15 | push·PR (명시 승인 뒤) | 허브 | `create-pr`, `pr-review-game` |
| 16 | 병합 확인 → 정리 | 허브 | `worktree-gc`, 허브 자동 정리 규칙, `orphan_check.sh` |

## 상시 장치

- Slack 수신: launchd `ai.lrm.slackpoll`(폴러) + 켜져 있으면 `ai.lrm.slacksocket`(Socket Mode, 설치만 하고 기본은 꺼짐) → `slack-inbox.jsonl`
- 봇 명령 즉답: `slack_cmd.sh`
- 옆 패널: mod `lrm-board` 가 `panel.json`(메인 손 칸)과 `panel-auto.json`(launchd `ai.lrm.panelcollect` 가 90초마다 씀)을 합쳐 그림. 런북 `$HUB_SCRIPTS/docs/panel.md`
- 따라잡기: `catch-up` / 현황: `work-brief`
- 반복 실수 구조화: `make-mistakes-impossible`, 문서·현실 대조: `repo-reality-check`

## 사고 후 복구

| 상황 | 처리 |
|---|---|
| Orca 재시작 | `resume_all.sh` → `patrol.sh` 로 중복 확인 → 중복은 사용자에게 닫기 요청 |
| 허브 맥락 과다 | 허브가 `HANDOFF.md` → `restart_hub.sh` |
| 메인 맥락 과다 | `HANDOFF_FILE` 갱신 → 새 메인 세션 |
| 1회성 정리·강제 push | `HUB_SCRIPTS` 에 날짜 붙은 사용자 실행용 스크립트로 만들어 건넴 |
