# claude-config

개인 Claude Code·Orca 설정 저장소. 프로젝트에 종속되지 않는 스킬·훅·전역 규칙·허브 스크립트·git 훅을 한곳에서 관리한다. 설계 근거는 [구조안 문서](docs/config-repo-structure.html).

## 구조

```
claude-config/
├─ install.sh           링크·병합·복사 + git 설정 (--dry-run·--check·--restore)
├─ links.tsv            원본 → 대상($HOME 기준) → 방식(link|merge|copy|render)
├─ gitconfig.tsv        git config --global 키 4개
├─ merge/               설정 병합 식 (orca-keep.jq, merge.jq)
├─ claude/              CLAUDE.md, keybindings.json, settings.base.json, hooks/(+lib), meat-proxy/
├─ skills/<이름>/       직접 작성한 스킬 34개 (synced·외부 링크 제외)
├─ git/hooks/           전역 git 훅 9개
├─ hubs/                scripts/ roles/ launchd/ slack-bot-manifest.yml slack-bot.env.example
├─ values.env.example   로컬 값 양식(실명·채널 ID). 실제 값은 저장소 밖
├─ tests/install/       샌드박스 검증 (run.sh)
├─ tests/mutate.sh      변이 검증(결함을 심어 run.sh 가 잡는지 확인)
└─ docs/                구조안, 라이브 전환 계획서
```

방식:
- `link` 는 대상을 저장소 원본으로 가리키는 링크로 만든다. 스킬은 폴더째가 아니라 스킬마다 링크해 `skills/synced` 같은 외부 항목이 섞이지 않는다.
- `merge` 는 `settings.base.json`(내 설정)에 현재 파일의 Orca 훅·상태줄을 얹어 새 파일로 쓴다. 훅 배열은 순서를 유지해 이어붙인다.
- `copy` 는 `__HOME__` 을 현재 HOME 으로 치환해 복사한다(launchd plist).
- `render` 는 `*.tmpl` 의 `__KEY__` 자리표시를 로컬 값 파일로 채워 실제 파일로 복사한다. 실명·DM 채널 ID 가 든 파일 5개(역할 3개, 스킬 파일 2개)가 이 방식이다. 스킬 2개(`orca-hub-create`, `structure-change-doc`)는 폴더째가 아니라 파일마다 링크한다.

## 설치·동기화

```sh
git clone git@github.com:cocodding0723/claude-config.git ~/orca/claude-config   # 새 맥만
cd ~/orca/claude-config
./install.sh --dry-run      # 바뀔 항목 확인
./install.sh                # 설치. 덮어쓸 파일은 ~/.claude-config-backup/<시각>/ 으로 이동
./install.sh --check        # 끊어진 링크·어긋난 설정 점검
```

갱신은 `git pull` 뒤 `./install.sh`. 같은 상태면 아무것도 바꾸지 않는다(반복 실행 안전). 설정을 바꿨으면 `claude/settings.base.json` 도 같이 고친다.

되돌리기: `./install.sh --restore ~/.claude-config-backup/<시각>`. 그 설치가 만든 링크·병합 결과를 지우고 백업 원본을 제자리로 되돌린다.

launchd plist 가 바뀌면 재적재 명령을 안내한다. 실행까지 맡기려면 `--reload-launchd` 를 붙인다.

## 저장소에 두지 않는 것

비밀(`*.env`, 토큰, 자격 증명), 이 맥의 기록(`state`·`cache`·`logs`·`history`·`projects`·`sessions`), 백업(`*.bak*`), 캐시(`__pycache__`), 외부 도구 관리분(`skills/synced`, `plugins`), 허브 실행 상태(slack-inbox·sent·poll-state), 문서 허브 작업물(`HANDOFF.md`·`decisions.tsv`)은 `.gitignore` 로 막는다. 로컬 값 두 가지는 자리표시본으로만 둔다.

- `hubs/slack-bot.env.example`: 값 없는 양식. 실제 값은 `~/.config/lrm-hubs/slack-bot.env`(권한 600)에만 둔다.
- `values.env.example`: 값 없는 양식. 실제 값은 `~/.config/claude-config/values.env`(권한 600, `*.env` 라 미추적)에 둔다. 키는 `OWNER_NAME`·`PERSON_FULL`·`PERSON_SHORT`·`DM_CHANNEL`. 값 파일이 없거나 키가 비면 `install.sh` 는 아무것도 바꾸기 전에 중단한다.

### 실명·채널 ID 처리 방식과 이유

저장소(커밋)에는 `__PERSON_FULL__` 같은 자리표시만 두고, 설치기가 값 파일로 채워 복사한다(render). 대안이던 seed(대상이 있으면 건드리지 않음)는 새 맥에서 자리표시가 그대로 남아 허브가 이름·채널을 모르고, 기존 파일이 저장소와 어긋나도 점검이 안 된다. render 는 ① 라이브 파일이 항상 값이 채워진 상태로 재생성되고 ② `--check` 가 값·템플릿 변경을 DRIFT 로 알리고 ③ 값 파일만 있으면 새 맥도 같은 결과가 나온다. 비용은 값 파일을 먼저 만들어야 한다는 점이며, 없으면 중단하므로 라이브 파일이 자리표시로 바뀌는 사고는 없다. 실제 파일 내용은 라이브 파일과 같게 나오도록 확인했다(바이트 비교).

**render 결과는 링크가 아니라 생성된 파일이다.** `.tmpl` 이나 값 파일을 고쳐도 `./install.sh` 를 다시 실행하기 전에는 라이브에 반영되지 않는다(`settings.json` 병합 결과도 같다). 반영 여부는 `./install.sh --check` 의 `DRIFT` 로 확인한다.

## 하드코딩 경로 치환

| 위치 | 방식 |
|---|---|
| `claude/CLAUDE.md` | `~` 경로 |
| `claude/settings.base.json` | `__HOME__` → 병합 때 HOME |
| `hubs/launchd/*.plist` | `__HOME__` → 복사 때 HOME |
| `hubs/scripts/restart_hub.sh` | `${HUB_ROOT:-$HOME/orca/workspaces/LoveRevolutionMaker}` |
| `skills/orca-hub-create/scripts/create_hub.sh`, `create_worker_ws.sh` | `$HOME` |

`claude/hooks/lib/common.sh` 의 `STATE_DIR` 은 링크로 설치해도 state 가 저장소로 옮겨 가지 않도록 `${CLAUDE_HOOKS_STATE:-$HOME/.claude/hooks/state}` 로 고정했다.

## 검증

```sh
tests/install/run.sh                                       # 임시 HOME 샌드박스에서만 실행
SETTINGS_SAMPLE=<settings.json 사본> tests/install/run.sh  # 실제 설정 사본으로 병합 검증 추가
```

dry-run 무변경 → 설치 → 재설치 멱등 → 설정 병합(훅 보존·순서·바이트 동일) → state 위치 → git 훅 동작 → 백업 불가 시 중단 → 복원 → 비밀 패턴 0건을 확인한다. shellcheck 가 설치돼 있으면 함께 돌린다.

```sh
tests/mutate.sh   # 병합 식·복원·STATE_DIR·render 등에 결함 12개를 심어 모두 검출되는지 확인(수 분)
```

## 병행 브랜치와 겹칠 수 있는 경로

2026-11-06 기준으로 두 브랜치를 읽기만 해 확인한 내용이다.

| 브랜치 | 겹치는 경로 | 처리 |
|---|---|---|
| `chore/slack-bot-optimize` | `lrm-hubs/slack_poll.sh`, `lrm-hubs/slack_pending.sh`, `lrm-hubs/slack_cmd.sh` (3개 수정·사본) | 이 저장소의 위치는 `hubs/scripts/`. git 은 경로가 달라 충돌 대신 같은 파일의 중복으로 보인다. 병합 시 그쪽 변경분을 `hubs/scripts/` 로 옮기고 `lrm-hubs/` 사본은 지운다. |
| 위와 동일 | `tests/run.sh`, `tests/lib.sh`, `tests/mutate.py`, `tests/test_*.sh`, `tests/stub_slack.py` | 이 브랜치의 테스트는 `tests/install/` 와 `tests/mutate.sh` 만 쓴다. 파일 이름이 겹치지 않는다. |
| `chore/docs-hub-consolidation` | 현재는 `docs/docs-hub-consolidation.html` 1개뿐이라 겹치는 경로 없음 | 그 문서가 설계하는 `skills/doc-kit/`·`docs/out/` 이동은 아직 그 브랜치에 구현돼 있지 않다. 구현되면 통합 대상 스킬(`explain-diff`·`tech-record`·`create-pr`·`doc-coauthoring`·`structure-change-doc`)의 삭제·이름 변경이 이 저장소의 스킬 사본·`links.tsv` 와 충돌한다. |
| 위와 동일(예정) | `.gitignore`, `README.md`, `docs/out/` | `docs/out/` 은 허브 전용 브랜치에서만 추적한다. `.gitignore` 의 `HANDOFF.md`·`decisions.tsv` 줄은 같은 취지라 합치면 된다. |

## 병합 순서와 push 전 체크

**병합 순서.** `chore/slack-bot-optimize` 의 `lrm-hubs/` 변경(`slack_poll.sh`·`slack_pending.sh`·`slack_cmd.sh`)이 이 저장소의 `hubs/scripts/` 로 먼저 옮겨져야 한다. 그렇지 않으면 링크 전환이 라이브의 최신 스크립트를 이 브랜치 시점의 구버전으로 되돌린다. 이 브랜치의 3개 파일은 2026-11-06 라이브(`chore/slack-bot-optimize` 의 같은 파일과 바이트 동일, 최신 커밋 `0593345`) 기준으로 갱신해 두었다. 그 브랜치가 더 바뀌면 다시 어긋나므로, 병합 후 `./install.sh --diff-live` 와 `./install.sh --check` 로 라이브와 같은지 확인한다.

**라이브 우선 점검.** `./install.sh --diff-live` 는 link·copy·render 항목의 저장소 원본과 라이브 대상을 내용으로 비교해, 라이브가 더 최신인 파일을 `LIVE-NEWER`(폴더 항목의 라이브 전용 파일은 `LIVE-ONLY`)로 알린다. 설치와 `--dry-run` 도 같은 점검을 먼저 하고 하나라도 있으면 변경 없이 중단한다(`--allow-live-diff` 로 우회). 경로 치환처럼 의도된 차이는 `live-diff.tsv` 의 치환 규칙으로만 흡수하며, 목록 밖 파일은 바이트가 같아야 한다.

**push 전 체크.** 실명·DM 채널 ID 가 아래 커밋의 파일 내용(blob)에 남아 있다. 현재 트리는 깨끗하지만 이력에는 있으므로, push·PR 전에 사용자 승인을 받아 `f24dcd4` 부터 `2f0b312` 까지의 15개 커밋(`f24dcd4 b09897f b2700fe 3e9bf21 b5fc44d a786c18 1defce4 a60fc5e fa0d2a8 f7fa24d 61bf8c9 cb94a02 3b9be12 af8818f 2f0b312`)을 템플릿 상태로 재작성해야 한다. 재작성 뒤 `tests/install/run.sh` 의 실명·ID 패턴 검사와 `git log -G` 로 이력을 다시 확인한다.

## 복원의 한계

`--restore` 는 백업 원본을 보존한 채 복사해서 되돌리므로 여러 번 실행해도 안전하다(백업 원본이 없으면 아무것도 지우지 않고 중단). 설치가 새로 만든 폴더(`.config/git/hooks`, `Library/LaunchAgents`, 스킬 하위 폴더 등)는 manifest 에 기록해 두었다가 복원 때 비어 있을 때만 제거한다. 설치 뒤 사용자가 그 안에 파일을 더했다면 폴더는 남긴다.

병합·리베이스는 하지 않았다.
