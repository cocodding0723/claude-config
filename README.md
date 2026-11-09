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
├─ skills/<이름>/       직접 작성한 스킬 35개 (synced·외부 링크 제외). `doc-kit` 은 문서 규칙 통합본
├─ git/hooks/           전역 git 훅 9개
├─ hubs/                scripts/ roles/ launchd/ slack-bot-manifest.yml slack-bot.env.example
├─ values.env.example   로컬 값 양식(실명·채널 ID). 실제 값은 저장소 밖
├─ tests/install/       샌드박스 검증 (run.sh)
├─ tests/doc-kit/       doc-kit 검증 (run.sh·sample.sh·rename.sh, fixtures/, check_unique.sh)
├─ tests/mutate.sh      변이 검증(결함을 심어 위 검증이 잡는지 확인)
├─ migrate/             옛 문서 스킬 이름 → doc-kit 치환 도구(rename_skills.sh, 대상·규칙 tsv)
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

doc-kit 은 따로 검증한다. 모두 임시 폴더에서만 실행하고 실제 `~/.config` 는 읽지 않는다.

```sh
tests/doc-kit/run.sh      # 설정 읽기(우선순위·파이썬 3.9)·check.py·고유값 0건·전역 규칙·새 허브 스크립트
tests/doc-kit/sample.sh   # 샘플 문서 전달: 프로젝트 지정 시 채널, 미지정 시 기본값·경로만 보고, 제목 형식·직접 전달 금지
tests/doc-kit/rename.sh   # 이름 치환 도구: dry-run 무변경, 백업 후 치환, 반복 실행, 되돌리기
tests/doc-kit/backup.sh   # 반영 순서 2번 백업 블록: 링크가 실제 내용으로 복사돼 7번 삭제 뒤에도 복원되는지(임시 HOME)
```

`sample.sh` 는 LLM 을 돌리지 않는다. 보고 메시지 문구와 설정 읽기까지만 대조하며, 메인에 업로드를 넘기는 동작은 문구 확인에 그친다. `tests/install/run.sh` 는 `doc-kit` 스킬·역할 파일 링크 설치와 `--check` 의 끊긴 링크 검출도 확인한다.

미추적 `.claude/`(로컬 하네스 설정)에 절대 경로가 있으면 `tests/install/run.sh` 의 하드코딩 경로 검사 1건이 실패한다. 저장소 문제가 아니므로 그 폴더를 치우고 다시 돌린다.

```sh
tests/mutate.sh   # 설치기 변이 17개(병합 식·복원·STATE_DIR·render·라이브 우선 등)와 doc-kit 변이 21개(고유값 혼입·설정 우선순위·치환 백업 생략·링크째 백업 등)가 모두 검출되는지 확인(약 10분)
ONLY="D1 D13" tests/mutate.sh   # 일부만. M=설치기, D=doc-kit
```

## 병행 브랜치와 겹칠 수 있는 경로

2026-11-06 기준으로 두 브랜치를 읽기만 해 확인한 내용이다.

| 브랜치 | 겹치는 경로 | 처리 |
|---|---|---|
| `chore/slack-bot-optimize` | `lrm-hubs/slack_poll.sh`, `lrm-hubs/slack_pending.sh`, `lrm-hubs/slack_cmd.sh` (3개 수정·사본) | 이 저장소의 위치는 `hubs/scripts/`. git 은 경로가 달라 충돌 대신 같은 파일의 중복으로 보인다. 병합 시 그쪽 변경분을 `hubs/scripts/` 로 옮기고 `lrm-hubs/` 사본은 지운다. |
| 위와 동일 | `tests/run.sh`, `tests/lib.sh`, `tests/mutate.py`, `tests/test_*.sh`, `tests/stub_slack.py` | 이 브랜치의 테스트는 `tests/install/` 와 `tests/mutate.sh` 만 쓴다. 파일 이름이 겹치지 않는다. |
| `chore/docs-hub-consolidation` | `docs/docs-hub-consolidation.html` 1개(설계 문서). 겹치는 경로 없음 | 이 문서가 설계한 `skills/doc-kit/`·새 허브 역할 파일·치환 도구는 `chore/doc-kit` 에서 구현했다. 통합 대상 스킬 5개(`explain-diff`·`tech-record`·`structure-change-doc`·`work-report`·`doc-coauthoring`)의 삭제는 이 브랜치에서 하지 않았다. 라이브 검증 뒤 별도 커밋으로 `links.tsv` 줄과 함께 지운다. |
| 위와 동일(예정) | `.gitignore`, `README.md`, `docs/out/` | `docs/out/` 은 허브 전용 브랜치에서만 추적한다. `.gitignore` 의 `HANDOFF.md`·`decisions.tsv` 줄은 같은 취지라 합치면 된다. |
| `chore/jira-comment-rule` | `claude/CLAUDE.md`, `hubs/roles/role-{arch,config,docs.txt.tmpl,server}.txt`, `skills/orca-hub-create/references/common-rules.txt.tmpl` (6개 파일을 양쪽이 수정). 그 브랜치만 바꾼 파일: `claude/settings.base.json`, 역할 파일 7개, `skills/jira-issue/SKILL.md` | 이 브랜치는 Jira 절·역할 파일 끝줄을 건드리지 않았다. 아래 «Jira 브랜치와 줄 범위» 표 기준으로 수정 줄이 서로 떨어져 있어 줄 단위 충돌은 없을 것으로 보지만 병합 시험은 하지 않았다. 병합 때 `claude/CLAUDE.md` 와 역할 파일을 눈으로 한 번 확인한다. |

### Jira 브랜치와 줄 범위

기준은 공통 조상 `6313f37`. 줄 번호는 조상 파일 기준이다(`git diff -U0 6313f37 <브랜치> -- <파일>`). 2026-11-09 읽기 전용으로 확인했다.

| 파일 | 이 브랜치가 고친 줄 | Jira 브랜치가 고친 줄 |
|---|---|---|
| `claude/CLAUDE.md` | 7, 47, 51~54(이름 치환), 80(이름 치환), 83 뒤에 5줄 추가(쉬운 말 절) | 59 |
| `hubs/roles/role-arch.txt` | 1 | 5, 7 뒤에 5줄 추가 |
| `hubs/roles/role-config.txt` | 5~6 | 15 뒤에 6줄 추가 |
| `hubs/roles/role-docs.txt.tmpl` | 2 | 8, 10 뒤에 6줄 추가 |
| `hubs/roles/role-server.txt` | 4 | 10, 12 뒤에 6줄 추가 |
| `skills/orca-hub-create/references/common-rules.txt.tmpl` | 2 | 8 |
| `skills/jira-issue/*` | 없음 | `SKILL.md` 15, 113, 115 |
| `hubs/roles/role-doc-kit.txt`, `handoff-template.md` | 새 파일 | 없음 |

가장 가까운 쌍은 `role-arch.txt`(1 대 5)와 `role-docs.txt.tmpl`(2 대 8)이며 사이에 안 고친 줄이 있다. 같은 줄을 둘 다 고친 곳은 없다. `claude/CLAUDE.md` 의 Jira 절(59)은 이 브랜치의 «문서 작성»(47~54)과 «쉬운 말»(84~) 사이다.

## 병합 순서와 push 전 체크

**병합 순서.** `chore/slack-bot-optimize` 의 `lrm-hubs/` 변경(`slack_poll.sh`·`slack_pending.sh`·`slack_cmd.sh`)이 이 저장소의 `hubs/scripts/` 로 먼저 옮겨져야 한다. 그렇지 않으면 링크 전환이 라이브의 최신 스크립트를 이 브랜치 시점의 구버전으로 되돌린다. 이 브랜치의 3개 파일은 2026-11-06 라이브(`chore/slack-bot-optimize` 의 같은 파일과 바이트 동일, 최신 커밋 `0593345`) 기준으로 갱신해 두었다. 그 브랜치가 더 바뀌면 다시 어긋나므로, 병합 후 `./install.sh --diff-live` 와 `./install.sh --check` 로 라이브와 같은지 확인한다.

**라이브 우선 점검.** `./install.sh --diff-live` 는 link·copy·render 항목의 저장소 원본과 라이브 대상을 내용으로 비교해, 라이브가 더 최신인 파일을 `LIVE-NEWER`(폴더 항목의 라이브 전용 파일은 `LIVE-ONLY`)로 알린다. 설치와 `--dry-run` 도 같은 점검을 먼저 하고 하나라도 있으면 변경 없이 중단한다(`--allow-live-diff` 로 우회). 경로 치환처럼 의도된 차이는 `live-diff.tsv` 의 치환 규칙으로만 흡수하며, 목록 밖 파일은 바이트가 같아야 한다.

**push 전 체크.** 실명·DM 채널 ID 가 아래 커밋의 파일 내용(blob)에 남아 있다. 현재 트리는 깨끗하지만 이력에는 있으므로, push·PR 전에 사용자 승인을 받아 `f24dcd4` 부터 `2f0b312` 까지의 15개 커밋(`f24dcd4 b09897f b2700fe 3e9bf21 b5fc44d a786c18 1defce4 a60fc5e fa0d2a8 f7fa24d 61bf8c9 cb94a02 3b9be12 af8818f 2f0b312`)을 템플릿 상태로 재작성해야 한다. 재작성 뒤 `tests/install/run.sh` 의 실명·ID 패턴 검사와 `git log -G` 로 이력을 다시 확인한다.

## doc-kit 라이브 반영 순서

이 브랜치는 라이브 파일을 하나도 바꾸지 않았다. 아래는 `main` 병합 뒤 `~/orca/claude-config` 체크아웃에서 사용자 승인을 받고 실행하는 순서다. 구조안의 전환(`docs/live-cutover.md`)이 끝나 `~/.claude/skills` 가 링크 상태일 때 시작한다.

### 계획서와 달라진 판단 3건

| 항목 | 계획서·초기 구현 | 지금 | 이유와 영향 |
|---|---|---|---|
| TOML 한글 섹션 | `[project.이름]` | `[project."이름"]` | TOML 의 따옴표 없는 키는 영문·숫자·`-`·`_` 만 쓸 수 있다. 한글 이름을 그대로 쓰면 문법 오류로 종료 코드 2 가 난다. 값 파일은 `skills/doc-kit/projects.example.toml` 을 복사해 시작하면 된다. |
| `check.py` 고유명사 검사 | 옛 `structure-change-doc` 의 `check.py`: 독자와 무관하게 항상 검사, 티켓 접두는 코드에 고정 | 설정 `audience` 가 `기획자` 일 때만 검사 | 개발자 독자는 클래스·함수 이름을 쓰는 것이 맞다. 티켓 키 검사도 설정에 `ticket_pattern` 이 있고 프로젝트가 잡혔을 때만 돈다. 프로젝트를 지정하지 않으면 두 검사 모두 생략되어 기본값으로 통과한다. |
| `live-diff.tsv` 의 `restart_hub` 규칙 | 라이브의 고정 경로를 `HUB_ROOT` 형태로 바꿔 비교 | 규칙 제거 | 저장소의 `restart_hub.sh` 에 `ROLE_FILE`·`COMMON_FILE` 변수가 더해져 경로 치환만으로는 라이브와 같아지지 않는다. 그래서 `--diff-live` 가 이 파일을 `LIVE-NEWER` 로 알리고, 설치는 `--allow-live-diff` 없이 중단한다. 라이브가 앞선 것이 아니라 저장소가 앞선 차이다. |

`--allow-live-diff` 가 필요한 경우는 **라이브가 아직 저장소 링크가 아닐 때**(첫 설치와 함께 반영할 때)다. 라이브가 이미 저장소를 가리키는 링크이고 `main` 을 받았다면 내용이 같아 `--diff-live` 에 아무것도 나오지 않고 플래그도 필요 없다. 플래그를 쓰기 전에 아래를 확인한다.

- `--diff-live` 가 알리는 줄은 `LIVE-NEWER .config/lrm-hubs/restart_hub.sh` 와 이름 치환 대상 13파일(`migrate/rename-targets.tsv` 의 `repo` 줄) 중 라이브에 대응 파일이 있는 것뿐이어야 한다. 그 밖의 줄은 라이브가 앞선 변경이므로 먼저 저장소로 옮긴다.
- 치환 대상은 차이가 이름 치환뿐인지 본다. 예: `perl migrate/rename_apply.pl migrate/rename-rules.tsv < ~/.claude/skills/create-pr/SKILL.md | diff - skills/create-pr/SKILL.md` 출력이 없어야 한다. `claude/CLAUDE.md` 는 `live-diff.tsv` 의 경로 치환(절대 홈 경로 → `~/`)도 겹치므로 둘 다 적용해 비교한다. render 로 만든 역할·스킬 파일은 값이 채워진 상태이므로 diff 가 치환한 줄뿐인지 눈으로 본다.
- 라이브 `restart_hub.sh` 는 이전 저장소 사본(`git show 6313f37:hubs/scripts/restart_hub.sh`)에 고정 경로만 다른 것인지 눈으로 확인한다.

### 순서

1. **사전 점검(읽기만).** `git status --short` 가 비어 있고 아래가 모두 통과해야 한다.
   ```sh
   tests/install/run.sh && tests/doc-kit/run.sh && tests/doc-kit/sample.sh && tests/doc-kit/rename.sh && tests/doc-kit/backup.sh
   ./install.sh --diff-live      # 위 «판단 3건» 아래 확인 항목대로 해석. 링크 상태면 출력 없음
   ```
2. **백업.** 되돌릴 수 있게 옛 스킬 5개와 프로젝트 메모리를 한 곳에 복사한다. 옛 스킬 4개는 저장소를 가리키는 링크이므로 `-L` 로 링크가 가리키는 **실제 내용**을 복사한다. 링크째 복사하면 7번에서 저장소 폴더를 지울 때 백업이 끊긴 링크가 되어 되돌릴 수 없다(`tests/doc-kit/backup.sh` 가 이 블록을 그대로 실행해 확인한다).
   ```sh
   B=~/.claude-config-backup/pre-doc-kit-$(date +%Y%m%d-%H%M%S)
   mkdir -p "$B"/skills "$B"/memory
   cp -RpL ~/.claude/skills/explain-diff ~/.claude/skills/tech-record ~/.claude/skills/structure-change-doc ~/.claude/skills/work-report ~/.claude/skills/doc-coauthoring "$B"/skills/
   cp -RpL ~/.claude/projects/-Users-$(id -un)-orca-LoveRevolutionMaker/memory/. "$B"/memory/
   echo "$B" > ~/.claude-config-backup/LAST-PRE-DOC-KIT
   find "$B"/skills -type l   # 출력이 없어야 한다(링크가 남아 있으면 복사가 잘못된 것)
   ```
3. **값 파일 만들기(없을 때만).** `~/.config/doc-kit/projects.toml` 은 설치기가 만들지도 지우지도 않는다. 값은 화면·로그에 출력하지 않는다.
   ```sh
   mkdir -p ~/.config/doc-kit
   cp skills/doc-kit/projects.example.toml ~/.config/doc-kit/projects.toml
   chmod 600 ~/.config/doc-kit/projects.toml   # 편집기로 자리표시를 실제 값으로 바꾼다
   ```
4. **설치 재실행.** `doc-kit` 스킬·`role-doc-kit.txt`·`handoff-template.md`·`create_doc_hub.sh` 링크가 생기고, `restart_hub.sh`·이름 치환된 역할 파일·스킬 사본이 저장소 내용으로 바뀐다. `--allow-live-diff` 는 라이브 우선 중단 보호를 끄므로 **먼저 플래그 없이** 실행하고, 아래 조건일 때만 붙인다.
   ```sh
   ./install.sh --diff-live    # 링크 상태 확인. 출력이 없으면 라이브가 저장소와 같다(이미 링크). 플래그 없이 진행
   ./install.sh --dry-run      # 플래그 없이 바뀔 항목 확인. 중단 없이 끝나면 아래 설치도 플래그 없이
   ./install.sh                # 출력의 '백업:' 경로를 기록
   ./install.sh --check        # 점검 통과: 문제 없음
   ```
   `--diff-live` 가 `restart_hub.sh` 와 치환 대상 중 라이브가 아직 링크가 아닌 사본의 줄**만** 내고, 위 확인 항목 3개를 마쳤을 때에만 `--dry-run` 과 설치를 `--allow-live-diff` 를 붙여 다시 실행한다. 그 밖의 줄이 있으면 플래그를 쓰지 않고 라이브 변경을 먼저 저장소로 옮긴다.
5. **프로젝트 메모리 이름 치환.** 저장소 13파일은 4번 설치로 이미 라이브에 반영됐다. 남은 것은 저장소 밖의 메모리 12파일이다.
   ```sh
   migrate/rename_skills.sh --dry-run    # 바뀔 줄을 파일:줄로만 출력. REVIEW·MISSING 이 0 이어야 함
   migrate/rename_skills.sh --apply      # 백업 후 치환. 출력의 '백업:' 경로를 기록
   ```
   저장소 13파일은 `--dry-run` 결과가 바뀔 파일 0 이어야 정상이다.
6. **시험.** 메인이 없는 새 터미널에서 샘플 문서 1건을 만들어 check.py 통과, 프로젝트 지정 시 채널, 미지정 시 기본값·경로만 보고, 제목 형식을 확인한다. 하나라도 실패하면 7번으로 가지 않는다.
7. **옛 스킬 5개 삭제(별도 커밋·사용자 승인).** 먼저 저장소에서 `skills/` 폴더 5개와 `links.tsv` 의 해당 줄을 지운다. 그다음 라이브를 지운다. 라이브의 모양이 둘로 갈린다. 지우기 전에 2번 백업이 끝났는지 확인한다.
   - `explain-diff`·`tech-record`·`work-report`·`doc-coauthoring` 은 저장소 폴더를 가리키는 링크 하나다. `rm 링크` 로 지운다.
   - `structure-change-doc` 은 링크가 아니라 **실제 폴더**다. 안에 저장소 파일을 가리키는 파일 단위 링크 7개(`scripts/` 4개, `templates/` 3개)와 설치기가 렌더한 `SKILL.md`, 옛 작업이 남긴 라이브 전용 파일(`__pycache__`, `*.bak-*`)이 있다. `rm 폴더` 는 `-r` 없이 실패하고, 폴더를 남기면 저장소 폴더를 지운 뒤 링크가 끊겨 되돌리기의 `cp -Rp` 가 실패한다. 그래서 안의 링크를 먼저 지우고, 링크가 없는 것을 확인한 뒤에 폴더를 지운다.
   ```sh
   rm ~/.claude/skills/explain-diff
   rm ~/.claude/skills/tech-record
   rm ~/.claude/skills/work-report
   rm ~/.claude/skills/doc-coauthoring
   rm ~/.claude/skills/structure-change-doc/scripts/check.py
   rm ~/.claude/skills/structure-change-doc/scripts/image_diff.py
   rm ~/.claude/skills/structure-change-doc/scripts/puml_diff.py
   rm ~/.claude/skills/structure-change-doc/scripts/screen_flow.sh
   rm ~/.claude/skills/structure-change-doc/templates/after.puml
   rm ~/.claude/skills/structure-change-doc/templates/before.puml
   rm ~/.claude/skills/structure-change-doc/templates/doc.html
   find ~/.claude/skills/structure-change-doc -type l   # 출력이 없어야 한다(남은 링크가 있으면 멈추고 확인)
   rm -r ~/.claude/skills/structure-change-doc
   ```
   링크 7개를 먼저 지웠으므로 `rm -r` 이 저장소 파일에 닿지 않는다. 라이브 전용 파일은 2번 백업(`cp -RpL`)에 들어 있다. 지운 뒤 `./install.sh --check` 와 새 스킬로 문서 1건 재생성을 확인한다(`tests/doc-kit/backup.sh` 가 이 블록을 그대로 실행해 확인한다).

### 되돌리기

| 대상 | 방법 |
|---|---|
| 설치(링크·렌더) | `./install.sh --restore <4번의 백업 폴더>` |
| 메모리 이름 치환 | `migrate/rename_skills.sh --revert <5번의 백업 폴더>`. 치환 뒤 직접 고친 파일은 건너뛰고 알린다. |
| 옛 스킬 5개 | 7번 전이면 그대로. 지운 뒤에는 `git revert` 또는 2번 백업(`"$B"/skills`, 링크가 아니라 실제 내용)에서 `cp -Rp "$B"/skills/<이름> ~/.claude/skills/`. 7번을 중간에 멈춰 `~/.claude/skills/structure-change-doc` 폴더가 남아 있으면 끊긴 링크 때문에 `cp` 가 실패하므로, 7번의 `rm` 줄과 `rm -r` 을 먼저 끝낸 뒤 복원한다. 복원본은 링크가 아니라 실제 파일이다. |
| 값 파일 | 설치기가 건드리지 않는다. 지우려면 직접 지운다. |

## 복원의 한계

`--restore` 는 백업 원본을 보존한 채 복사해서 되돌리므로 여러 번 실행해도 안전하다(백업 원본이 없으면 아무것도 지우지 않고 중단). 설치가 새로 만든 폴더(`.config/git/hooks`, `Library/LaunchAgents`, 스킬 하위 폴더 등)는 manifest 에 기록해 두었다가 복원 때 비어 있을 때만 제거한다. 설치 뒤 사용자가 그 안에 파일을 더했다면 폴더는 남긴다.

병합·리베이스는 하지 않았다.
