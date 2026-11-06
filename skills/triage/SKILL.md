---
name: triage
description: "현재 저장소의 Crashlytics 크래시와 GitHub 이슈를 수집해 우선순위대로 골라 dev 기반 워크트리(feature/fix-*, feature/*)에서 수정·테스트하고 검수 대기 상태로 남긴다. '트리아지', '이슈 처리해줘', '크래시 잡아줘' 요청에 사용한다. 커밋·push·PR은 하지 않는다."
argument-hint: "[최대 워크트리 수=1] [--source crashlytics|github|all] [--dry-run]"
---

# 트리아지 (수동 호출)

`~/.orca-triage/triage-prompt.md`를 읽고 그대로 따른다. 아래 항목만 그 문서를 덮어쓴다.

## 인자: $ARGUMENTS
- 첫 숫자 = **최대 워크트리 수 N** (기본 1). 이번 호출에서 새로 만드는 워크트리 상한이자 처리 이슈 수 상한.
- `--source crashlytics|github|all` (기본 all): 수집 출처 제한.
- `--dry-run`: 수집·분류·우선순위 표까지만 만들고 워크트리는 만들지 않는다.

## 문서와 다른 점
1. **워크스페이스**: 자동화용 triage 워크트리가 아니라 현재 디렉터리의 저장소를 기준으로 한다. `git rev-parse --show-toplevel`이 실패하면 중단. 현재 워크트리 파일은 수정하지 않는다 (수정은 워커가 새 워크트리에서만).
2. **처리 건수**: 문서의 "1위 1건"을 "상위 N건"으로 바꾼다. 단, 이미 검수 대기 중인 워크트리(`orca worktree list`에 브랜치 `feature/fix-*`·`feature/*`로 남아 있고 코멘트가 "검수 대기"인 것)를 포함해 **총 워크트리 수가 N을 넘지 않게** 한다. 이미 N개면 새로 만들지 않고 그 사실을 보고한다.
3. **오케스트레이션**: Run은 하나(`run-create` 1회), 이슈마다 Task/Dispatch 하나. N건을 **한 웨이브로 병렬** 디스패치하되 `worker-start` 사이를 90초 띄운다 (`fvm flutter` 동시 초기화로 SDK 캐시가 깨진 전례). 워커 스펙에 "다른 워커의 워크트리는 읽기만" 을 추가한다. `check --wait`로 worker_done을 하나씩 처리(재검증 → ack → release)하고 `reclaimable`이 빌 때까지 정리한다.
4. **시간 예산**: 웨이브 전체 60분. 초과하면 남은 워커는 `worker-stop` 후 failed로 기록.
5. **원장·리포트 경로**: 문서와 같다 (`~/.orca-triage/<repo>/`). source 필드에 `manual`을 추가로 기록한다.
6. **보고**: 마지막에 모든 건을 한 번에 사용자에게 보여준다. 건마다 리포트 항목(문서 7절) + "커밋 여부는 사용자 결정" 한 줄. 이 스킬은 `review-before-commit` 승인 흐름의 앞 단계일 뿐, 커밋 승인을 묻지 않는다.

## 종료 조건
- N건 처리 완료, 후보 소진, 또는 총 워크트리 상한 도달 중 먼저 오는 것.
- 후보가 0건이면 분류 표(PR 진행 중 / 해결됨·닫기 권장 / 해결 후보 / 수동 필요 / 제외)만 보여주고 끝낸다.
