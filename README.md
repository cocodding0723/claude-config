# claude-config

개인 Claude Code·Orca 설정 저장소. 프로젝트에 종속되지 않는 스킬·훅·전역 규칙·허브 스크립트·git 훅을 한곳에서 관리한다. 설계 근거는 [구조안 문서](docs/config-repo-structure.html).

## 구조

```
claude-config/
├─ install.sh           링크·병합·복사 + git 설정 (--dry-run·--check·--restore)
├─ links.tsv            원본 → 대상($HOME 기준) → 방식(link|merge|copy|seed)
├─ gitconfig.tsv        git config --global 키 4개
├─ merge/               설정 병합 식 (orca-keep.jq, merge.jq)
├─ claude/              CLAUDE.md, keybindings.json, settings.base.json, hooks/(+lib), meat-proxy/
├─ skills/<이름>/       직접 작성한 스킬 34개 (synced·외부 링크 제외)
├─ git/hooks/           전역 git 훅 9개
├─ hubs/                scripts/ roles/ launchd/ slack-bot-manifest.yml slack-bot.env.example
├─ tests/install/       샌드박스 검증 (run.sh)
└─ docs/                구조안, 라이브 전환 계획서
```

방식:
- `link` 는 대상을 저장소 원본으로 가리키는 링크로 만든다. 스킬은 폴더째가 아니라 스킬마다 링크해 `skills/synced` 같은 외부 항목이 섞이지 않는다.
- `merge` 는 `settings.base.json`(내 설정)에 현재 파일의 Orca 훅·상태줄을 얹어 새 파일로 쓴다. 훅 배열은 순서를 유지해 이어붙인다.
- `copy` 는 `__HOME__` 을 현재 HOME 으로 치환해 복사한다(launchd plist).
- `seed` 는 대상이 없을 때만 복사한다. 계정 값이 든 역할 파일의 자리표시본(`*.example`)이 이 방식이다.

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
- `hubs/roles/role-docs.txt.example`: DM 채널·이름을 자리표시로 바꾼 사본. 실제 `role-docs.txt` 는 로컬 파일로 유지한다.

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

## 병행 브랜치와 겹칠 수 있는 경로

| 브랜치 | 겹치는 경로 | 처리 |
|---|---|---|
| `chore/slack-bot-optimize` | `lrm-hubs/slack_poll.sh`, `lrm-hubs/slack_pending.sh` | 이 저장소의 위치는 `hubs/scripts/`. 병합 시 그쪽 변경분을 `hubs/scripts/` 로 옮기고 `lrm-hubs/` 사본은 지운다. git 은 경로가 달라 충돌 대신 중복으로 보인다. |
| 같은 브랜치 | `tests/run.sh`, `tests/lib.sh` | 이 브랜치는 `tests/install/` 아래만 써서 겹치지 않는다. |
| `chore/docs-hub-consolidation` | `skills/doc-kit/`(신규), 통합 대상 스킬의 삭제·이름 변경 | 이 브랜치가 `skills/explain-diff`·`tech-record`·`create-pr`·`doc-coauthoring`·`structure-change-doc` 사본을 이미 갖고 있어, 먼저 병합된 쪽 기준으로 삭제·이름 변경 충돌이 난다. `links.tsv` 의 해당 줄도 같이 고쳐야 한다. |
| 위와 동일 | `docs/out/`, `.gitignore`, `README.md` | `docs/out/` 은 허브 전용 브랜치에서만 추적한다. `.gitignore` 의 `HANDOFF.md`·`decisions.tsv` 줄은 양쪽이 같은 취지라 합치면 된다. |

병합·리베이스는 하지 않았다.
