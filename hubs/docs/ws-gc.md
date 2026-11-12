# 워크스페이스 자동 정리기(ws_gc) 런북

쓰임이 끝난 Orca 워크스페이스를 하루 3번 점검해 지우는 `ws_gc.sh` 의 판정 규칙, 설치, 실삭제 전환, 되돌리기를 적는다. 작성일은 2026-11-10 이다.

## 쉬운 말 3줄 요약

- 무엇이 생겼나: PR 이 병합·닫힘이거나 커밋이 전부 서버에 올라가 있고, 미커밋이 없고, 2일 넘게 안 쓴 워크스페이스를 스크립트가 찾아 지운다. 지우기 전에 테스트 결과물은 `~/orca/archive/` 로 복사해 둔다.
- 왜: 오래된 워크스페이스가 디스크 수십 GB 를 차지하고, 사람이 정리를 잊는다(11-10 조사에서 96개 중 10개가 바로 정리 대상이었다).
- 사용자가 할 일: 지금은 시험 모드(지우지 않고 목록만 기록)로 돈다. 1주 뒤 로그를 보고 괜찮으면 메인에게 실삭제 전환을 승인한다(6절).

## 1. 흐름

```text
 launchd (하루 3회, 10:07·15:07·22:07)
   │
   ▼
 ws_gc.sh ──▶ orca worktree list · terminal list · worker-list   (하나라도 실패하면 중단: 살아 있는 곳을 가릴 수 없음)
   │
   ▼  워크스페이스마다
 제외 → 활동 → 미커밋 → PR/origin → 서브모듈       (먼저 걸리는 이유 하나로 건너뜀, 로그에 이유 기록)
   │ 전부 통과
   ├─ --dry-run(기본): CANDIDATE 로그만
   └─ --apply : 결과물 복제·파일 수 대조 → orca worktree rm → worker-release
   │
   ▼
 로그 ~/orca/archive/ws_gc.log + Slack progress 1줄(후보·삭제가 있을 때만)
```

## 2. 판정 규칙

### 처음부터 대상이 아닌 곳

| 이유 코드 | 뜻 |
|---|---|
| `hub` | 폴더 이름이 `coord-` 로 시작하는 허브 |
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
| 1 | 브랜치 PR 이 모두 MERGED·CLOSED(`gh pr list --head`). PR 이 없으면 HEAD 가 origin 에 전부 있어야 함(detached 포함) | `pr_open` 열린 PR 이 하나라도 있음 · `unmerged` PR 없고 origin 에 없는 커밋 있음 · `gh_fail` gh 조회 실패 |
| 2 | 미커밋 변경 0. 아래 부산물은 0 으로 침 | `dirty` |
| 3 | 마지막 활동이 N일 이상 전(기본 2). Orca 활동 시각과 마지막 커밋 시각 중 늦은 쪽 | `idle` |
| 4 | 서브모듈 안에 미커밋·미push 가 없음 | `submodule` |

조건 2의 부산물(미커밋 0 으로 치고 목록을 로그의 `NOISE` 줄에 남김):

- `*.meta` 와 TMP 폰트 아틀라스(`TextMesh Pro/` 아래, `*SDF*.asset`). Unity 재임포트가 만드는 변경이다.
- `Assets/**/Sound/**/*.LICENSE.txt` 가 줄바꿈만 바뀐 경우(`git diff HEAD --ignore-all-space --ignore-cr-at-eol` 가 빔). 내용이 바뀌었으면 진짜 변경이다. 폴더를 지우므로 role-config 의 "되돌린 뒤"에 해당하는 되돌림은 하지 않는다.

브랜치는 지우지 않는다. 미push 커밋이 있어도 PR 이 닫힌 브랜치라면 브랜치 이름에 커밋이 남아 있다. detached 는 커밋이 origin 에 있을 때만 지운다.

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

로그 한 줄 형식: `시각 [DRY|APPLY] 판정 이유 <저장소>/<폴더> — 근거`. 판정은 `START`·`SKIP`·`CANDIDATE`·`DELETE`·`ARCHIVE`·`NOISE`·`RM_RETRY_FORCE`·`WORKER_RELEASE`·`END`·`SLACK`.

Slack 요약은 양식 A 를 따른다(`--kind progress`, 태그 `ws-gc`). 예: 첫 줄 `워크스페이스 정리 시험 점검`, 빈 줄, `• 결과: 삭제 후보 8개·예상 용량 4.4GB(시험, 삭제 없음), 건너뜀 236곳(미커밋 50·최근활동 82 …)`. 실전송 확인은 메인이 한 줄로 한다.

```bash
bash ~/.config/lrm-hubs/ws_gc.sh --dry-run --only LRM-284
```

## 6. 실삭제 전환(첫 1주 시험 뒤, 사용자 승인 필요)

> 2026-11-12 이후 plist 는 처음부터 `--apply` 다(10절). 아래는 시험 모드로 되돌렸다가 다시 올릴 때의 절차로 읽는다.

1. 1주 동안 `ws_gc.log` 의 `CANDIDATE` 를 훑어 지우면 안 되는 곳이 없는지 본다. 있으면 그 이유를 규칙으로 고친 뒤 다시 1주.
2. 메인이 사용자 승인을 받는다.
3. 저장소의 `hubs/launchd/ai.lrm.wsgc.plist` 에서 `<string>--dry-run</string>` 을 `<string>--apply</string>` 로 바꾸고 커밋한다.
4. `bash install.sh --reload-launchd` 로 다시 올린다.
5. 첫 실삭제는 사람이 지켜보며 한 번 돌린다: `bash ~/.config/lrm-hubs/ws_gc.sh --apply --only <한 곳>`.

되돌리기(시험으로): 3번을 거꾸로 하고 4번을 다시 한다. 끄기: `launchctl bootout gui/$(id -u)/ai.lrm.wsgc`.

## 7. 지운 뒤 복구

- 브랜치는 그대로이므로 Orca 에서 같은 브랜치로 워크스페이스를 다시 만들면 된다. 단 gitignore 대상(Library 등 캐시)은 다시 만들어진다.
- 테스트 결과물은 `~/orca/archive/<저장소>/<폴더>/` 에서 꺼낸다.
- 미커밋 변경이 있던 곳은 애초에 지우지 않으므로 복구 대상이 없다.

## 8. 알려진 한계

- `git fetch` 를 하지 않는다. origin 정보가 낡았으면 "origin 에 있음"을 놓쳐 안전한 쪽(건너뜀)으로 판정한다. PR 상태는 gh 로 실시간 조회한다.
- 기존 role-config 의 허브 자동 정리 규칙(보관 위치 `~/Documents/90_정리기록/workspace-archive/YYYYMMDD/…`, rsync)과 보관 위치가 다르다. 10절의 결과물 보관도 `~/orca/archive` 를 쓴다. 이 정리기는 `~/orca/archive` 만 쓴다. 허브가 손으로 정리하는 경로는 그대로 두었다.
- 서브모듈 안의 미push 판정은 "어느 원격 브랜치에도 없는 로컬 브랜치 커밋"만 센다. 서브모듈의 stash 는 보지 않는다.
- 보관 대상은 허용 목록(`CFG_OUTPUT_DIRS_RE`·`CFG_OUTPUT_ROOT_RE`)의 결과물뿐이다. 다른 위치의 산출물은 보관 대신 작업 공간을 보존한다. 새 종류가 생기면 목록에 더한다.
- Mac 이 잠든 시각의 회차는 깨어난 뒤 한 번만 실행된다.

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
| 캐시 | gitignore 된 `Library/ Temp/ obj/ Logs/ UserSettings/ .vs/ .idea/ node_modules/ .dart_tool/`, `.flutter-plugins(-dependencies)`, `.claude/settings.local.json`, `.DS_Store`(`CFG_CACHE_RE`) | 보관 없이 삭제 |
| 소스 폴더 | `Assets/ Packages/ ProjectSettings/` 아래(`CFG_SOURCE_DIR_RE`). 허용 목록과 겹쳐도(예 `Assets/TestResults/`) 항상 | 보존 |
| 추적 파일 수정 | 추적 파일이 바뀐 모든 경우. 폴더 이름만으로 부산물로 치지 않음 | 보존 |
| 미확인 | 위 어디에도 없는 미추적·gitignore 파일(예 `secret/Foo.cs`, `result.json`, `reports/s.md`, `Foo.CS`) | 보존(`dirty`) |
| 확인된 부산물 | 줄바꿈만 바뀐 사운드 `LICENSE.txt`, `*.meta`, `*SDF*.asset`(TMP 아틀라스) 파일. 규칙은 파일 단위이고 `TextMesh Pro/` 폴더 이름은 보지 않음 | 0 으로 침 |

- gitignore 파일은 git 이 폴더째 묶어 보여 주므로, 허용·캐시·소스 판정이 서지 않는 폴더는 파일 단위로 풀어 본다.
- 보존 이유는 `KEEP_CODE` 로그와 `--json` 의 `keep_code`(`tracked`·`unverified`·`files`)에 남는다.
- 보관: 결과물을 `~/orca/archive/<저장소>/<폴더>/` 로 복제한 뒤 항목마다 **byte 단위**로 대조한다(파일은 `cmp`, 폴더는 `diff -r`). 복사 실패·내용 불일치·보관본에 남는 다른 파일이 있으면 `archive_fail` 로 지우지 않는다. 삭제 직전에 한 번 더 판정·보관해, 판정 뒤 새로 생긴 결과물도 보관한다.
- 색인: `~/.config/lrm-hubs/hub-inbox/<허브 slug>.tsv` 에 `시각 · 작업 공간 · 보관 경로 · 파일 수` 를 탭으로 한 줄 추가한다(`WS_GC_INBOX`). 허브 slug 는 부모 폴더 `coord-<slug>-hub` 에서 뗀다. 부모가 없으면 `_none.tsv`. 보관한 파일이 있을 때만 쓰고, 삭제가 성공한 뒤에 쓴다.
- 서브모듈·LICENSE 줄바꿈 규칙은 그대로다.
- HEAD 검사: PR 상태와 별개로 현재 HEAD 가 원격 ref 에 있어야 한다. 없으면 `unmerged` 로 보존한다. 단 HEAD 가 병합된 PR 의 마지막 커밋(`headRefOid`)과 같으면 원격 브랜치가 지워진 경우라 허용한다.
- 동시 실행 방지: 파일 잠금(`~/orca/archive/.ws_gc.flock`)을 쥔 채 자기 자신을 다시 실행한다. 프로세스가 죽으면 커널이 잠금을 풀어, 죽은 pid 판정·잠금 탈취가 없다. 이미 돌고 있으면 "이미 실행 중" 으로 끝난다.

### 반납 = 삭제: `release_ws.sh`

```bash
bash ~/.config/lrm-hubs/release_ws.sh --dispatch <id> [--dry-run] [--json]
```

`worker-release` 로 터미널을 닫은 뒤 `ws_gc.sh --path <그 작업 공간> --days 0 --apply` 로 같은 판정을 한 번 돌린다(판정 복사 없음). 종료 코드: 0 삭제, 1 삭제 실패, 2 인자 오류, 3 환경 오류, 4 남김(코드 변경·열린 PR·미병합 등, 이유 출력), 5 거부(허브 폴더·다른 워커나 터미널이 쓰는 곳·하위 보유). `--dry-run` 은 release 없이 판정만 하며 이 워커 터미널은 쉬는 중으로 친다.

### 30분 주기와 허브당 10개 상한

- plist: `StartInterval 1800`, `--apply --quiet`. Slack 은 삭제가 있거나 지난번과 다른 실패가 있을 때만 보낸다(같은 실패는 `.ws_gc.lastfail` 로 대조해 반복하지 않는다).
- `create_worker_ws.sh`: 만들기 전에 부모 허브의 하위 수를 센다. 상한(`LRM_HUB_MAX_CHILDREN`, 기본 10) 이상이면 `ws_gc.sh --parent <허브> --apply` 를 1회 돌리고, 그래도 상한 이상이면 stderr 경고와 `hub_report.py add --kind block` 기록을 남긴다. 정리에는 제한 시간(`LRM_HUB_GC_TIMEOUT`, 기본 60초)이 있어, 넘기면 정리기와 자식 프로세스를 끊고 생성을 계속한다. 정리 실패·시간 초과도 생성을 막지 않는다.

시험: `bash tests/ws_gc/run.sh`, `bash tests/release_ws/run.sh`, `bash tests/create_worker_ws/run.sh`.

## 참고

- 규칙 출처: `hubs/roles/role-config.txt` 의 "워크스페이스 자동 정리"·"LICENSE.txt 줄바꿈 예외".
- 조사표(2026-11-10): 오래된 Orca 워크스페이스 96개 중 바로 정리 7·보관 뒤 정리 3·허브 확인 4·남김 82.
