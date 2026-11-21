# 워크스페이스 자동 정리기(ws_gc) 런북

쓰임이 끝난 Orca 워크스페이스를 30분마다 점검해 보관 뒤 지운다. `ws_gc.sh`의 판정 규칙과 복구 절차다. 작성일은 2026-11-10, 정정일은 2026-11-21이다.

## 쉬운 말 3줄 요약

- 무엇이 생겼나: PR 이 병합·닫힘이거나 커밋이 전부 서버에 올라가 있고, 미커밋이 없고, 2일 넘게 안 쓴 워크스페이스를 스크립트가 찾아 지운다. 지우기 전에 테스트 결과물은 `~/orca/archive/` 로 복사해 둔다.
- 왜: 오래된 워크스페이스가 디스크 수십 GB 를 차지하고, 사람이 정리를 잊는다(11-10 조사에서 96개 중 10개가 바로 정리 대상이었다).
- 사용자가 할 일: `--dry-run --no-slack`으로 삭제 후보를 확인한다. 설치된 정리기는 30분마다 `--apply`로 돈다(10절).
- 2026-11-19 추가: PR 이 전부 병합된 워크스페이스는 창이 붙어 있어도 정리한다. 30분 넘게 안 쓴 곳을 이번 회차에 표시하고, 다음 회차에 창을 닫은 뒤 보관·삭제한다(2절 "병합된 PR 에 창이 붙은 곳").
- 2026-11-17 추가: PR 이 병합·닫힌 워크스페이스는 2일을 기다리지 않고 다음 회차에 정리한다. 미커밋이 있어도 전부 `~/orca/archive/<폴더>-<날짜시각>/` 에 보관·대조한 뒤 지운다(2절 "PR 닫힘 즉시 정리").
- 2026-11-21 정정: 마무리 목록의 허브는 종료 반영 유예 뒤 활동이 없고 창·하위가 없으면 보관·삭제한다(「마무리된 허브 정리」). 미리보기는 화면 출력만 하며 로그·잠금·Slack을 쓰지 않는다.

## 1. 흐름

```text
 launchd (30분 간격, --apply)
   │
   ▼
 ws_gc.sh ──▶ orca worktree list · terminal list · worker-list   (하나라도 실패하면 중단: 살아 있는 곳을 가릴 수 없음)
   │
   ▼  워크스페이스마다
 제외 → 활동 → 미커밋 → PR/origin → 서브모듈       (먼저 걸리는 이유 하나로 건너뜀, 로그에 이유 기록)
   │ 전부 통과
   ├─ --dry-run(기본): 판정 화면 출력만, 영구 쓰기 없음
   └─ --apply : 결과물 복제·파일 수 대조 → orca worktree rm → worker-release
   │
   ▼
 --apply 만 로그 ~/orca/archive/ws_gc.log 기록
 삭제 또는 새 실패가 있을 때만 Slack progress 전송
```

## 2. 판정 규칙

### 처음부터 대상이 아닌 곳

| 이유 코드 | 뜻 |
|---|---|
| `hub` | 폴더 이름이 `coord-` 로 시작하며 마무리 목록에 없는 허브 |
| (표시 없음) | 메인 체크아웃(`isMainWorktree`). 루트 `~/orca/workspaces` 밖이라 목록에 오르지 않음 |
| `current` | 이 스크립트를 실행 중인 폴더 |
| `children` | 하위 워크스페이스가 아직 남은 곳(워커를 거느린 상위) |
| `live` | 연결된 터미널이 있거나, 쉬는 중이 아닌 워커가 붙은 곳. 쉬는 중(`reclaimable`) 워커의 터미널은 막지 않고 정리 뒤 `worker-release` 로 기록을 닫는다 |
| `protected` | `main`·`master`·`develop`·`dev`·`release/*` 브랜치. 옛 PR 이 병합돼 있어도 지우지 않는다 |
| `not_orca` | Orca 에 등록되지 않은 폴더. 건드리지 않고 개수만 센다 |
| `missing` | 등록부에는 있으나 폴더가 없음 |

### 지우는 조건(전부 충족)

| 번호 | 조건 | 건너뛸 때 코드 |
|---|---|---|
| 1 | 열린 PR 이 없음(`gh pr list --head`). 이름 있는 브랜치가 로컬에 남는 곳은 미병합이어도 후보(공간만 지우고 브랜치·커밋은 유지, DETAIL 에 `미병합(브랜치 유지)`). detached HEAD 는 HEAD 가 origin 에 있어야 함 | `pr_open` 열린 PR 이 하나라도 있음 · `unmerged` detached 이고 origin 에 없는 커밋 있음 · `gh_fail` gh 조회 실패 |
| 2 | 미커밋 변경 0. 아래 부산물은 0 으로 침 | `dirty` |
| 3 | 마지막 활동이 N일 이상 전(기본 2). Orca 활동 시각과 마지막 커밋 시각 중 늦은 쪽 | `idle` |
| 4 | 서브모듈 안에 미커밋·미push 가 없음 | `submodule` |

조건 2의 부산물(미커밋 0 으로 치고 목록을 로그의 `NOISE` 줄에 남김):

- `*.meta` 와 TMP 폰트 아틀라스(`TextMesh Pro/` 아래, `*SDF*.asset`). Unity 재임포트가 만드는 변경이다.
- `Assets/**/Sound/**/*.LICENSE.txt` 가 줄바꿈만 바뀐 경우(`git diff HEAD --ignore-all-space --ignore-cr-at-eol` 가 빔). 내용이 바뀌었으면 진짜 변경이다. 폴더를 지우므로 role-config 의 "되돌린 뒤"에 해당하는 되돌림은 하지 않는다.

브랜치는 지우지 않는다. 미push 커밋이 있어도 PR 이 닫힌 브랜치라면 브랜치 이름에 커밋이 남아 있다. detached 는 커밋이 origin 에 있을 때만 지운다.

### PR 닫힘 즉시 정리(2026-11-17)

PR 이 끝난 워크스페이스는 위 조건 2(미커밋 0)·3(휴지 일수)을 기다리지 않는다. 아래를 전부 만족하면 다음 회차에 정리한다. 하나라도 어긋나면 위 기존 규칙으로 판정한다.

| 조건 | 어긋날 때 |
|---|---|
| "처음부터 대상이 아닌 곳"(허브·메인·현재 폴더·하위 있음·터미널·워커·보호 브랜치)이 아님 | 그 코드로 건너뜀 |
| 이름 있는 브랜치가 로컬에 있음(지운 뒤에도 커밋이 남음) | 기존 규칙 |
| 그 브랜치 PR 이 하나 이상이고 전부 `MERGED`·`CLOSED`(열린 PR 이 하나라도 있으면 아님) | 기존 규칙(열린 PR 이면 결국 `pr_open`) |
| HEAD 가 병합·닫힌 PR 의 마지막 커밋(`headRefOid`)에 포함됨. PR 뒤에 쌓은 커밋이 있으면 새 PR 준비 중일 수 있어 제외 | 기존 규칙 |
| 마지막 활동(Orca 활동·마지막 커밋 중 늦은 쪽)이 10분 넘게 전(`WS_GC_PR_DONE_GRACE_MIN`) | 기존 규칙 |
| 서브모듈 안 미커밋·미push 없음(서브모듈 안은 보관하지 않음) | `submodule` |
| Unity 가 연 곳이 아님(`Temp/UnityLockfile` 을 잡은 프로세스 없음) | `unity_open` |
| 보관할 양이 2048MB 이하(`WS_GC_PR_DONE_MAX_MB`) | `archive_big` |

보관 폴더는 `~/orca/archive/<폴더>-<YYYYMMDD-HHMM>/`(같은 분에 또 있으면 `-2`)이다.

### 병합된 PR 에 창이 붙은 곳(2026-11-19, 수거 방식)

창이 붙은 곳은 `live` 로 항상 건너뛰어서, PR 이 병합된 뒤에도 사람이 창을 닫을 때까지 남았다(11-19 기준 21곳). 메모리 수거처럼 두 회차로 나눠 치운다.

| 회차 | 하는 일 | 로그 |
|---|---|---|
| 표시 | 아래 조건을 전부 만족하면 표시만 한다. 지우지 않는다 | `SKIP live_marked … 표시함` |
| 정리 | 표시한 지 25분(`WS_GC_MARK_MIN`)이 지났고 조건이 그대로면 창을 닫고(`orca terminal close --worktree … --all`) 위 "PR 닫힘 즉시 정리"와 같이 보관·삭제한다 | `TERM_CLOSE`, `DELETE … pr_done` |

| 조건 | 어긋날 때 |
|---|---|
| 일하는 워커 없이 창만 붙어 있음 | `live`(표시도 지움) |
| 그 브랜치 PR 이 전부 `MERGED`(닫히기만 한 PR 은 아님) | `live` |
| 마지막 활동이 30분 넘게 전(`WS_GC_MERGED_LIVE_IDLE_MIN`). 표시 뒤에 창을 다시 쓰면 여기서 걸려 표시가 풀린다 | `live` |
| 위 "PR 닫힘 즉시 정리"의 나머지 조건(HEAD 가 병합된 PR 에 포함, 서브모듈, Unity, 보관 크기) | 그 코드 |

- 표시 기록은 `<archive>/ws_gc.marks`(경로·표시 시각)다. 시험 모드는 표시를 쓰지 않는다.
- 창 닫기가 실패하면 지우지 않는다(`term_close_fail`).
- 허브 폴더(`coord-`), 보호 브랜치, 하위가 남은 곳은 여전히 대상이 아니다.
- 끄려면 `WS_GC_MERGED_LIVE=0`. 그러면 창이 붙은 곳은 예전처럼 항상 건너뛴다.
- 작업 공간이 사라진 작업 카드는 작업판 순찰(`board_sweep.py` 의 `ws_gone`)이 뺀다. PR 이 병합됐으면 닫고, 아니면 할 일로 내려놓는다.

| 파일 | 내용 |
|---|---|
| `tracked.patch` | `git diff --binary HEAD`. 스테이징·미스테이징 추적 변경 전부 |
| `staged.patch` | `git diff --binary --cached`. 스테이징했던 부분 기록 |
| `status.txt` | `git status --porcelain --untracked-files=all` |
| `meta.txt` | 경로·브랜치·HEAD·PR·시각·되살리는 명령 |
| `list.txt` | `files/` 에 넣은 경로 목록 |
| `files/<원래 경로>` | 미추적 파일 전부(부산물 포함)와 캐시가 아닌 gitignore 파일. 캐시(`Library/`·`.dart_tool/` 등 `CFG_CACHE_*`)는 넣지 않음 |

절차(`--apply`):

1. 위 파일을 쓰고 `files/` 로 복제한다.
2. 대조: 목록이 그대로인지, 두 patch 가 지금 변경과 byte 단위로 같은지, 파일 수·바이트 합이 원본과 같은지, 파일마다 내용이 같은지. 하나라도 다르면 `archive_fail` 로 남기고 지우지 않는다.
3. `orca worktree rm`. 미커밋 때문에 거부되면 2번 대조를 한 번 더 해서 보관 뒤 바뀐 것이 없을 때만 `--force`. 바뀌었으면 `rm_fail`(보관본은 남음, 다음 회차에 새로 보관).
4. 허브 색인 한 줄(보관 경로·파일 수)과 쉬는 워커 `worker-release`. 브랜치는 유지한다.

로그: 후보는 `CANDIDATE <저장소>/<폴더> pr_done PR #n MERGED 병합·닫힘 → 활동 대기 생략 … 보관예정 파일 N개·추적 변경 N건(patch …)·합계 …`, 보관은 `ARCHIVE_OK 파일 수·바이트 <수> <바이트> 일치`, 삭제는 `DELETE … 보관=<폴더>`. 요약 줄 끝에 `PR 닫힘 즉시 정리 N개`.

되살리기: `meta.txt` 의 `restore` 줄. 같은 브랜치로 작업 공간을 다시 만들고 `git apply --binary tracked.patch`, `cp -R files/. <작업 공간>/`.

### 삭제 절차(`--apply`)

1. gitignore 결과물 중 `TestResults/`·`docs/out/`·`*.html`·`*.mp4` 를 `~/orca/archive/<저장소>/<폴더>/` 로 `cp -cR`(APFS 복제, 실패하면 일반 복사). 캐시(`Library/` 등)는 보관하지 않는다.
2. 원본·복제본 파일 수 대조. 다르면 `archive_fail` 로 건너뛰고 삭제하지 않는다.
3. `orca worktree rm --worktree path:<경로>`. 서브모듈 때문에 거부되면 4번 조건(서브모듈 안 미커밋·미push 0)이 확인된 곳만 `--force` 로 한 번 더 시도한다. 그래도 실패하면 `rm_fail`.
4. 이 폴더에 붙었던 쉬는 워커마다 `orca orchestration worker-release --dispatch <id>`.

`reset --hard`·`branch -D` 같은 보호 훅 대상 명령과 `git` 파괴 명령은 쓰지 않는다. 지우기는 Orca 의 `worktree rm` 만 쓴다.

## 3. 설정값 — 한 곳

`ws_gc.sh` 맨 위 설정 블록(`CFG_*`)이 유일한 정의 위치이고, 아래는 환경 변수·옵션으로 덮어쓸 수 있다.

| 값 | 기본 | 덮어쓰기 |
|---|---|---|
| 루트 | `~/orca/workspaces` | `--root`·`WS_GC_ROOT` |
| 보관·로그 폴더 | `~/orca/archive` | `--archive-dir`·`WS_GC_ARCHIVE` |
| 로그 파일 | `~/orca/archive/ws_gc.log` | `--log`·`WS_GC_LOG` (2MB 넘으면 `.1` 로 돌림) |
| 최소 휴지 일수 | 2 | `--days`·`WS_GC_MIN_IDLE_DAYS` |
| 로컬 브랜치가 남는 곳의 유예(분) | 60 | `WS_GC_FRESH_GRACE_MIN`. 막 만든 빈 공간 보호용. 이 시간만 지나면 휴지 일수 대신 후보 |
| PR 닫힘 즉시 정리 유예(분) | 10 | `WS_GC_PR_DONE_GRACE_MIN` |
| 허브 마무리 뒤 종료 반영 유예(분) | 10 | `WS_GC_HUB_REOPEN_GRACE_MIN`. 이 시간 뒤 활동·커밋만 재개로 판정 |
| PR 닫힘 즉시 정리 보관 상한(MB) | 2048 | `WS_GC_PR_DONE_MAX_MB`. 넘으면 `archive_big` 로 남김 |
| 부산물·보관 대상 정규식, 보호 브랜치, 허브 접두사 | 블록 안 | 파일 수정 |
| Slack 전송 스크립트 | `~/.config/lrm-hubs/slack_notify.sh` | `WS_GC_NOTIFY`(시험용) |

## 4. 설치

`links.tsv` 에 스크립트·문서(`link`)와 plist(`copy`)가 있다. 병합 뒤 설치기를 돌리고 plist 를 올린다.

```bash
bash install.sh                      # ~/.config/lrm-hubs/ws_gc.sh, ~/Library/LaunchAgents/ai.lrm.wsgc.plist 설치
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/ai.lrm.wsgc.plist   # 처음 한 번(이미 올렸다면 install.sh --reload-launchd)
```

plist 는 30분마다 `--apply --quiet` 로 돈다(10절. 설치·load 는 병합 뒤 메인이 한다). 로그는 `~/orca/archive/ws_gc.log`, launchd 가 잡은 오류 출력은 `~/orca/archive/ws_gc.launchd.log` 에 쌓인다.

## 5. 쓰는 법

```bash
bash ~/.config/lrm-hubs/ws_gc.sh --help          # 옵션·종료 코드
bash ~/.config/lrm-hubs/ws_gc.sh --no-slack      # 시험 점검(삭제 없음), 화면에 판정 출력
bash ~/.config/lrm-hubs/ws_gc.sh --only LRM-318  # 한 곳만
bash ~/.config/lrm-hubs/ws_gc.sh --json --no-slack   # 요약 JSON(stdout), 상세는 stderr
```

종료 코드: 0 정상(건너뜀 포함), 1 삭제 단계 실패 있음, 2 인자 오류, 3 환경 오류(orca·gh·jq 없음, orca 응답 없음).

정정(2026-11-21): 출력 한 줄 형식은 `시각 [DRY|APPLY] 판정 이유 <저장소>/<폴더> — 근거`다. 미리보기는 화면에만 출력한다. `--apply`만 로그 파일에 기록한다. 판정은 `START`·`SKIP`·`CANDIDATE`·`DELETE`·`ARCHIVE`·`NOISE`·`RM_RETRY_FORCE`·`WORKER_RELEASE`·`END`·`SLACK`이다.

정정(2026-11-21): Slack은 `--apply`에서 삭제 또는 지난번과 다른 실패가 있을 때만 전송한다. 후보가 있는 미리보기도 전송하지 않는다. 제목은 `워크스페이스 자동 정리 완료`, 빈 줄 뒤 `• 결과: …` 형식이다(`--kind progress`, 태그 `ws-gc`).

```bash
bash ~/.config/lrm-hubs/ws_gc.sh --dry-run --only LRM-284
```

## 6. 실삭제 전환(첫 1주 시험 뒤, 사용자 승인 필요)

> 2026-11-12 이후 plist 는 처음부터 `--apply` 다(10절). 11-13 사용자 승인 D-CFGAPPLY(Slack 결정 카드 '1. 전부 적용')로 실삭제 유지를 확정했다. 아래는 시험 모드로 되돌렸다가 다시 올릴 때의 절차로 읽는다.

1. 1주 동안 `ws_gc.log` 의 `CANDIDATE` 를 훑어 지우면 안 되는 곳이 없는지 본다. 있으면 그 이유를 규칙으로 고친 뒤 다시 1주.
2. 메인이 사용자 승인을 받는다.
3. 저장소의 `hubs/launchd/ai.lrm.wsgc.plist` 에서 `<string>--dry-run</string>` 을 `<string>--apply</string>` 로 바꾸고 커밋한다.
4. `bash install.sh --reload-launchd` 로 다시 올린다.
5. 첫 실삭제는 사람이 지켜보며 한 번 돌린다: `bash ~/.config/lrm-hubs/ws_gc.sh --apply --only <한 곳>`.

되돌리기(시험으로): 3번을 거꾸로 하고 4번을 다시 한다. 끄기: `launchctl bootout gui/$(id -u)/ai.lrm.wsgc`.

## 7. 지운 뒤 복구

- 브랜치는 그대로이므로 Orca 에서 같은 브랜치로 워크스페이스를 다시 만들면 된다. 단 gitignore 대상(Library 등 캐시)은 다시 만들어진다.
- 테스트 결과물은 `~/orca/archive/<저장소>/<폴더>/` 에서 꺼낸다.
- 기존 규칙으로는 미커밋 변경이 있던 곳을 지우지 않는다. PR 닫힘 즉시 정리로 지운 곳은 `~/orca/archive/<폴더>-<날짜시각>/` 의 patch·파일로 되살린다(2절).

## 8. 알려진 한계

- `git fetch` 를 하지 않는다. origin 정보가 낡았으면 "origin 에 있음"을 놓쳐 안전한 쪽(건너뜀)으로 판정한다. PR 상태는 gh 로 실시간 조회한다.
- 기존 role-config 의 허브 자동 정리 규칙(보관 위치 `~/Documents/90_정리기록/workspace-archive/YYYYMMDD/…`, rsync)과 보관 위치가 다르다. 10절의 결과물 보관도 `~/orca/archive` 를 쓴다. 이 정리기는 `~/orca/archive` 만 쓴다. 허브가 손으로 정리하는 경로는 그대로 두었다.
- 서브모듈 안의 미push 판정은 "어느 원격 브랜치에도 없는 로컬 브랜치 커밋"만 센다. 서브모듈의 stash 는 보지 않는다.
- 보관 대상은 허용 목록(`CFG_OUTPUT_DIRS_RE`·`CFG_OUTPUT_ROOT_RE`)의 결과물뿐이다. 다른 위치의 산출물은 보관 대신 작업 공간을 보존한다. 새 종류가 생기면 목록에 더한다.
- Mac 이 잠든 시각의 회차는 깨어난 뒤 한 번만 실행된다.
- PR 닫힘 즉시 정리는 브랜치마다 `gh pr list` 를 먼저 부른다(이전에는 휴지·미커밋을 통과한 곳만). 11-17 실측 전체 점검 약 1분.
- PR 을 다른 곳에서 rebase·수정해 로컬 HEAD 가 PR 마지막 커밋에 없는 곳은(11-17 실측 2곳) 즉시 정리 대상이 아니다. 기존 규칙으로 남는다.
- PR 없이 결과물만 남은 곳(예: 문서 출력만 있고 브랜치에 커밋·PR 이 없음)은 대상이 아니다.

## 9. 시험

```bash
bash tests/ws_gc/run.sh      # 임시 저장소·가짜 orca·gh·Slack 전송으로 조건별 시험, 실제 워크스페이스는 건드리지 않음
bash tests/ws_gc/mutate.sh   # 결함을 심은 사본에서 시험이 실패하는지 확인
```

## 10. 수명 관리(2026-11-12)

허브 하위 작업 공간이 약 295곳까지 불어난 것이 계기다. 시험 점검 한 번의 보존 291곳 중 미커밋이 97곳이었고, 대부분 결과물 파일이었다.

### 결과물만 남은 곳도 정리(허용 목록 방식)

판정은 확장자가 아니라 **허용 목록**이다. 추적 안 되는(미추적·gitignore) 파일이 허용 목록에 있을 때만 결과물로 보고, 목록 밖은 전부 "미확인"으로 작업 공간을 보존한다. 확신이 없으면 보존한다.

| 구분 | 기준(설정은 `ws_gc.sh` 위쪽 `CFG_*` 한 곳) | 처리 |
|---|---|---|
| 허용 결과물 | 결과물 폴더 `docs/out/`·`evidence/`·`TestResults/`(`CFG_OUTPUT_DIRS_RE`), 작업 공간 **루트**의 `*.html *.md *.png *.jpg *.jpeg *.gif *.mp4 *.txt *.log`(`CFG_OUTPUT_ROOT_RE`, 확장자 대소문자 무시) | 보관 → 색인 → 삭제 |
| 캐시 후보 | gitignore 된 `Library/ Temp/ obj/ Logs/ UserSettings/ .vs/ .idea/ node_modules/ .dart_tool/`, `.flutter-plugins(-dependencies)`, `.claude/settings.local.json`, `.DS_Store`, Flutter 생성물(`android/.gradle/ android/app/build/ build/ ios/Flutter/ephemeral/ ios/Pods/`, `android/local.properties`, `ios/Flutter/Generated.xcconfig`, `ios/Flutter/flutter_export_environment.sh`), `Library/BurstCache/`(`CFG_CACHE_RE`). 폴더째 생성 목록(`CFG_CACHE_GENERATED_RE`)에 드는 곳은 안을 열지 않고 접어 둔 채 캐시로 친다. 그 밖 폴더(`android/app/src/` 등)의 미추적·무시 소스는 그대로 보존한다. **폴더 이름만으로 캐시로 치지 않는다.** 폴더 안에서는 "지워도 되는 것" 목록(`CFG_CACHE_GENERATED_RE`: `Library/PackageCache·Artifacts·Bee` 등 도구가 다시 만드는 하위 경로, `Library` 바로 아래의 확장자 없는 파일·`.asset .json .meta .txt .log`, `obj/Debug·Release`, `node_modules/`, `.dart_tool/`, `Temp/`, `Logs/`, `.vs/`)에 든 파일만 버리고, 목록 밖 파일은 확장자와 상관없이(`.cs`·`.kt`·`.mp4` 모두) 미확인으로 보존한다 | 생성 목록 밖 파일이 없을 때만 보관 없이 삭제 |
| 소스 폴더 | `Assets/ Packages/ ProjectSettings/` 아래(`CFG_SOURCE_DIR_RE`). 허용 목록과 겹쳐도(예 `Assets/TestResults/`) 항상 | 보존 |
| 추적 파일 수정 | 추적 파일이 바뀐 모든 경우. `.meta`·`*SDF*.asset` 부산물 예외보다 항상 우선(예외는 미추적 파일에만 적용) | 보존 |
| 미확인 | 위 어디에도 없는 미추적·gitignore 파일(예 `secret/Foo.cs`, `result.json`, `reports/s.md`, `Foo.CS`) | 보존(`dirty`) |
| 확인된 부산물 | 줄바꿈(`--ignore-cr-at-eol`)·실행 비트만 바뀐 추적 파일, 작업 사본 sha256 이 HEAD 포인터 oid 와 같은 LFS 파일, 미추적 `*.meta`·`*SDF*.asset`, `Library/PackageManager/`. 파일 단위이고 폴더 이름은 보지 않음 | 0 으로 침. 삭제 전 되돌리고, rm 이 미커밋으로 거부하면 재확인 뒤 `--force` |

- 서브모듈: 안에도 상위와 같은 분류를 적용한다(미추적·gitignore 소스, 결과물, 부산물이 하나라도 있으면 `submodule` 로 보존, 미push 커밋도 포함).
- 보관 대조는 파일 수·내용을 비교하되, tar 가 한글 이름을 NFC→NFD 로 풀어도 같은 이름으로 짝지어 본다(`diff -r` 이름 바이트 오탐 방지).
- 본체와 같은 파일 판정은 곳마다 본체 경로를 1회만 구한다(파일마다 git 을 부르면 수만 건에서 분 단위).
- gitignore 파일은 git 이 폴더째 묶어 보여 주므로, 허용·캐시·소스 판정이 서지 않는 폴더는 파일 단위로 풀어 본다.
- 보존 이유는 `KEEP_CODE` 로그와 `--json` 의 `keep_code`(`tracked`·`unverified`·`files`)에 남는다.
- 보관: 결과물을 `~/orca/archive/<저장소>/<폴더>/` 로 복제한 뒤 항목마다 **byte 단위**로 대조한다(파일은 `cmp`, 폴더는 `diff -r`). 복사 실패·내용 불일치·보관본에 남는 다른 파일이 있으면 `archive_fail` 로 지우지 않는다. 삭제 직전에 한 번 더 판정·보관해, 판정 뒤 새로 생긴 결과물도 보관한다.
- 색인: `~/.config/lrm-hubs/hub-inbox/<허브 slug>.tsv` 에 `시각 · 작업 공간 · 보관 경로 · 파일 수` 를 탭으로 한 줄 추가한다(`WS_GC_INBOX`). 허브 slug 는 부모 폴더 `coord-<slug>-hub` 에서 뗀다. 부모가 없으면 `_none.tsv`. 보관한 파일이 있을 때만 쓰고, 삭제가 성공한 뒤에 쓴다.
- 서브모듈·LICENSE 줄바꿈 규칙은 그대로다.
- HEAD 검사: PR 상태와 별개로 현재 HEAD 가 원격 ref 에 있어야 한다. 없으면 `unmerged` 로 보존한다. 단 HEAD 가 병합된 PR 의 마지막 커밋(`headRefOid`)과 같으면 원격 브랜치가 지워진 경우라 허용한다.
- 동시 실행 방지: 전체 점검(`--only` 포함)끼리는 파일 잠금(`~/orca/archive/.ws_gc.flock`)을 쥔 채 자기 자신을 다시 실행해 하나만 돈다. 이미 돌고 있으면 "이미 실행 중" 으로 끝난다. 곳 단위 삭제 안전은 곳마다 따로 잡는 잠금(`~/orca/archive/.ws_gc.locks/<폴더>-<해시>.flock`)이 맡는다: 판정·보관·삭제 한 곳 동안만 쥐고 놓으므로 `--path`(release_ws)·`--parent`(create_worker_ws)는 전체 실행 사이에 대기 없이 끼어들고, 같은 곳을 동시에 처리하면 나중 실행이 기다렸다가 `missing` 으로 건너뛴다(최대 900초, 넘으면 `lock_busy`). 잠금은 프로세스가 죽으면 커널이 풀어, 죽은 pid 판정·잠금 탈취가 없다.

### 반납 = 삭제: `release_ws.sh`

```bash
bash ~/.config/lrm-hubs/release_ws.sh --dispatch <id> [--dry-run] [--json]
```

`worker-release` 로 터미널을 닫은 뒤 `ws_gc.sh --path <그 작업 공간> --days 0 --apply` 로 같은 판정을 한 번 돌린다(판정 복사 없음). 종료 코드: 0 삭제, 1 삭제 실패, 2 인자 오류, 3 환경 오류, 4 남김(코드 변경·열린 PR·미병합 등, 이유 출력), 5 거부(허브 폴더·다른 워커나 터미널이 쓰는 곳·하위 보유). `--dry-run` 은 release 없이 판정만 하며 이 워커 터미널은 쉬는 중으로 친다.

자동 호출: `session_gc.py --apply`(launchd 주기)가 worker_done 으로 끝난 워커를 `worker-release` 한 직후 `release_ws.sh --dispatch <id> --released` 를 부른다. 코디네이터가 부르지 않아도 끝난 작업 공간이 정리된다. 결과는 `session-gc.log` 의 release 줄 뒤에 붙는다.

### 30분 주기와 허브당 10개 상한

- plist: `StartInterval 1800`, `--apply --quiet`. Slack 은 삭제가 있거나 지난번과 다른 실패가 있을 때만 보낸다(같은 실패는 `.ws_gc.lastfail` 로 대조해 반복하지 않는다).
- `create_worker_ws.sh`: 만들기 전에 부모 허브의 하위 수를 센다. 상한(`LRM_HUB_MAX_CHILDREN`, 기본 10) 이상이면 `ws_gc.sh --parent <허브> --apply` 를 1회 돌리고, 그래도 상한 이상이면 stderr 경고와 `hub_report.py add --kind block` 기록을 남긴다. 정리에는 제한 시간(`LRM_HUB_GC_TIMEOUT`, 기본 60초)이 있어, 넘기면 정리기 프로세스 그룹에 TERM 을 보내고 유예(`LRM_HUB_GC_GRACE`, 기본 5초) 뒤에도 남으면 그룹 전체를 KILL 한 뒤 생성을 계속한다. TERM 을 무시하는 자식도 남기지 않는다. 정리 실패·시간 초과도 생성을 막지 않는다.

시험: `bash tests/ws_gc/run.sh`, `bash tests/release_ws/run.sh`, `bash tests/create_worker_ws/run.sh`.

## 마무리된 허브 정리(2026-11-21)

**마무리 목록에 든 허브만 보관 뒤 삭제한다.** `feature_close.sh --yes`가 남긴 `~/.config/lrm-hubs/closed_hubs.txt`를 읽는다. `LRM_CLOSED_HUBS`로 경로를 바꾼다. 목록과 작업 공간 경로는 `pwd -P`로 정규화해 비교한다.

정정(2026-11-21): 한 줄은 `경로<TAB>마무리 시각(epoch 초)`다. 같은 경로를 다시 마무리하면 시각을 갱신하고 중복을 제거한다. 추가·제거는 공용 `lib/closed_hubs.sh`에서 목록 파일 자체의 `flock`을 공유한다. 제거는 판정 때 읽은 시각과 같은 줄만 지워 판정 뒤 시각이 바뀐 기록을 보존한다. 같은 이름 허브가 여러 개면 `feature_close`는 경고하고 허브 단계를 건너뛴다.

```text
목록 허브 → 종료 반영 유예 뒤 활동 없음 → 창 없음 → 하위 0 → 로컬 브랜치 유지
          → 보관 상한 검사 → 복제·대조 → 작업 공간 삭제 → 목록에서 제거
```

| 조건 | 판정 |
|---|---|
| 목록 밖 허브 | `SKIP hub` |
| 경로만 적힌 옛 목록 또는 잘못된 시각 | `CLOSED_HUBS_WARN` 경고와 `SKIP hub`. 허브와 목록 유지 |
| Orca 마지막 활동 또는 마지막 커밋이 마무리 시각 + 유예보다 늦음 | `SKIP hub_reopened`. 허브 보존, `--apply`에서 목록 줄 제거 |
| 연결된 창 또는 활동 중 워커 | `SKIP live`. 쉬는 워커의 창도 닫지 않고 보존 |
| 하위 작업 공간 존재 | `SKIP children`. `parentWorktreeId`와 `childWorktreeIds` 모두 확인 |
| detached HEAD 또는 보호 브랜치 | `SKIP unmerged` 또는 `SKIP protected` |
| 조건 통과 | `CANDIDATE … hub_closed`, 삭제 뒤 `DELETE … hub_closed`. JSON의 `reason`도 `hub_closed` |

**마무리 뒤 활동을 확인하고 PR 조회와 활동 일수 대기는 생략한다.** 다시 연 허브를 보호하기 위해 Orca `lastActivityAt`과 마지막 커밋을 모두 비교한다. 보관은 PR 닫힘 정리의 `archive_all`·`verify_full`을 재사용한다. 서브모듈 변경, 열린 Unity, `WS_GC_PR_DONE_MAX_MB` 초과는 기존과 같이 보존한다. `feature_close --yes`의 목록 등록 뒤 30분 정리 회차가 이 조건을 확인해 허브 폴더를 지운다.

**종료 반영 유예는 기본 10분이다.** `WS_GC_HUB_REOPEN_GRACE_MIN`으로 바꾼다. Orca가 터미널 종료·하위 정리를 늦게 반영하므로, 마무리 시각 + 유예 뒤의 활동·커밋만 재개로 본다. 유예 안 활동은 기존 창·하위 보호 조건을 통과하면 보관·삭제한다.
자동 마무리 재시도도 같은 기준으로 재개된 허브를 제외하므로, `board_sweep.tsv`의 `feature_hub_reopen_grace_min`과 `WS_GC_HUB_REOPEN_GRACE_MIN`을 같은 값으로 설정한다.

**sparse 허브의 미추적 결과물도 보관한다.** `docs/out/` 아래 HTML·증거를 `<archive>/<허브폴더>-<날짜시각>/files/docs/out/`로 복제하고 파일 수·바이트·내용을 대조한다. 추적 변경은 `tracked.patch`와 `staged.patch`에 남긴다. 보관·삭제 실패 시 목록 항목을 유지한다. 브랜치는 삭제하지 않는다.

**기본 미리보기는 영구 파일을 쓰지 않는다.** 로그는 화면에만 출력하고 보관 폴더·로그 파일·잠금·마무리 목록을 만들거나 갱신하지 않는다. Slack 전송도 생략한다. 임시 판정 파일은 실행 종료 때 제거한다. 출력 저장이 필요하면 셸 리다이렉션으로 경로를 지정한다.

복원은 보관본의 `meta.txt`에서 브랜치를 확인한 뒤 수행한다. `<보관폴더>`와 `<허브경로>`는 실제 절대 경로로 바꾼다.

```bash
git worktree add <허브경로> <브랜치>
git -C <허브경로> apply --binary <보관폴더>/tracked.patch
cp -R <보관폴더>/files/. <허브경로>/
```

`staged.patch`는 스테이징 상태를 복구할 때 `git apply --cached --binary`로 적용한다. 다시 작업할 허브는 마무리 목록에 추가하지 않는다. 자동 재삭제를 막기 위해 목록을 비운 상태로 작업한다.

근거: 기준 `27191cb`, 2026-11-21 정정. `hubs/scripts/ws_gc.sh`의 허브 판정과 `tests/ws_gc/run.sh`의 `[HUB]` 시험, `tests/board_sweep/test_feature_close.py`의 동시 갱신 시험이다. 잠금은 [Python fcntl 문서](https://docs.python.org/3/library/fcntl.html)를 따른다. sparse가 추적 파일 범위에 미치는 영향은 [Git sparse-checkout 문서](https://git-scm.com/docs/git-sparse-checkout)를 따른다.

## 참고

- 규칙 출처: `hubs/roles/role-config.txt` 의 "워크스페이스 자동 정리"·"LICENSE.txt 줄바꿈 예외".
- 조사표(2026-11-10): 오래된 Orca 워크스페이스 96개 중 바로 정리 7·보관 뒤 정리 3·허브 확인 4·남김 82.
