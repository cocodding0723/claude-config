---
name: work-brief
description: 내 Jira 티켓(LRM)과 로컬 워크트리·PR 상태를 한 번에 대조해 진행 브리핑을 낸다. "브리핑", "지라 티켓 얼마나 됐어", "작업 현황", "워크스페이스 점검", `/work-brief` 요청에 사용한다. 읽기 전용이며 정리·삭제는 worktree-gc 가 맡는다.
---

# work-brief

티켓 ↔ 워크트리 ↔ PR 을 티켓 번호로 맞물려 **어긋난 곳**을 먼저 보여 준다. 아무것도 고치지 않는다.

## 인자

- 없음: 내 담당, 최근 14일
- `7d`·`30d`: 완료 티켓 조회 기간 변경
- `jira`: 워크트리 스캔 생략(티켓만)
- `ws`: Jira 생략(워크트리만)

## 1. 수집 (두 작업을 한 응답에서 병렬 호출)

**Jira** — `mcp__atlassian__searchJiraIssuesUsingJql` (ToolSearch 로 로드)
- cloudId: `a59b16fd-c171-4342-97ca-1f4ae9841192` (조회 생략)
- jql: `project = LRM AND assignee = currentUser() AND (statusCategory != Done OR updated >= -14d) ORDER BY status, updated DESC`
- fields: `["summary","status","updated","priority","parent"]`, maxResults 100

**워크트리** — `bash ~/.claude/skills/work-brief/scripts/scan.sh <메인 저장소 경로>`
- 메인 저장소에서 실행. `origin/dev` fetch 후 비교하므로 10초 정도 걸린다.
- 출력 필드: `tickets`(브랜치·폴더명의 LRM-n), `idleDays`, `modified`(추적 파일 변경),
  `untracked`(.meta·폰트 아틀라스 제외 새 파일), `ahead`/`behind`(origin/dev 대비),
  `prs`(같은 브랜치 PR), `detached`, `orphanDirs`(git 이 모르는 폴더),
  `prsByTicket`(PR 제목·브랜치의 LRM-n 별 PR. 워크트리 없는 티켓의 PR 은 여기서 찾는다)

## 2. 대조 규칙 → "어긋남" 절

| 신호 | 조건 | 제안 |
|---|---|---|
| 완료 처리 후보 | 티켓 `확인 대기`/`진행 중` + prsByTicket 이 MERGED 뿐 | Jira 완료 전환 (사용자가 직접 또는 승인 후) |
| 정리 후보 | 티켓 완료 + 워크트리 남음, 또는 PR MERGED + 새 커밋 없음 | `/worktree-gc` |
| PR 없음 | 티켓 `확인 대기` 인데 prsByTicket 에 PR 없음 | PR 생성 또는 상태 되돌림 |
| 멈춤 | 티켓 `진행 중` + 워크트리 없음, 또는 idleDays ≥ 7 | 상태 확인 |
| 상태 과다 | 같은 접두 묶음(`[친구들]` 등)에서 진행 중 ≥ 3 | 실제 진척 확인 |
| 크게 뒤처짐 | behind ≥ 300 + ahead > 0 | 리베이스 전 충돌 주의 |
| 미커밋 방치 | modified > 0 + idleDays ≥ 7 | 커밋 또는 폐기 결정 |
| 티켓 없음 | tickets 비어 있음 | 티켓 연결 또는 정리 |
| 고아·detached | orphanDirs, detached=true | 폴더 확인 |
| 우선순위 | High 가 `해야 할 일` | 표시만 |

같은 워크트리에 신호가 여럿이면 한 줄로 합친다. 한 묶음은 5줄까지 보여 주고 나머지는 개수로 적는다.

## 3. 출력 틀 (터미널, 한국어, 평서 `~다`)

```
**기준: <날짜>, 내 LRM 담당.** 티켓 열림 N(확인 대기 a·진행 중 b·해야 할 일 c) · 최근 완료 d · 워크트리 w · OPEN PR p

## 1. 어긋남        ← 2절 규칙 결과. 없으면 "없음" 한 줄
## 2. 확인 대기     ← 티켓 + PR 번호
## 3. 진행 중       ← 접두 묶음별 진척 ("P1~P5 완료, P6 남음")
## 4. 해야 할 일    ← 묶음별 개수 + 눈에 띄는 것만

다음: <2분 안에 할 수 있는 행동 하나>
```

- 에픽 진척은 하위 티켓 접두 번호(P1·F3·C2·N4)로 계산한다.
- 숫자는 수집 결과로만 쓴다. 추정하지 않는다.
- 스캔이나 Jira 가 실패하면 그 절에 원인을 적고 나머지는 그대로 낸다.

## 하지 않는 것

- 워크트리 삭제·커밋·Jira 상태 변경. 제안만 한다.
- 문서 파일 생성. 공유용이 필요하면 `doc-kit` 으로 넘긴다.
