# 옆 패널(lrm-board) 런북

옆 패널의 자동 칸을 채우는 수집기, 메인이 쓰는 손 칸, 둘을 합쳐 그리는 mod 의 구성·켜기·끄기·되돌리기를 적는다. 작성일은 2026-11-10 이다.

## 쉬운 말 3줄 요약

- 11-23 개정(단계 띠): 🔄 AI 진행 칸의 기능 줄 앞에 작업 단계 띠(`■■■◆□□□ 구현 · 제목 …`)가 붙고, 계획 승인·검수·결정 카드를 기다리는 기능은 `?` 와 설명으로 사용자 차례임을 보인다. `/board-detail` 은 기능마다 전체 단계 이름 띠와 다음 차례를 보인다. 단계는 기능 카드 label `stage:<키>`(`flow:v1` 기능만)에서 수집기가 `board/lib/board_flow.py` 를 읽어 낸다. 단계가 없는 옛 기능 줄은 그대로다(7-5절).
- 11-16 개정: 손으로 쓰던 `panel.json` 을 없앴다. 결정·할 일·헤드라인·최근 기록은 작업판(beads, `~/orca/board`) 카드에서 나온다. 메인은 `board-ask`·`board-answer` 로 카드만 만들고 닫는다(6절).
- 11-17 3차(워커 줄): 🔄 AI 진행 칸 아래에 워커를 한 줄씩 보인다. 지금 도는 워커(`▶ 작업 공간 · 허브 · 모델`, 경과)를 먼저, 60분 안에 끝난 워커(✅ 성공·❌ 실패·⏹ 중단, 끝난 시각)를 다음에 둔다. 합쳐 6줄(`PANEL_CONFIG.workerRows`), 넘치면 `외 N개`, 전체는 `/board-detail`. 워커 세션이 끝나면 Orca 가 터미널을 닫아 사이드바에서 사라지므로 이 줄이 유일한 흔적이다. 원료는 `worker-list`(도는지·끝났는지)·`worktree list`(표시 이름·상위 허브)·`worker-show --full`(시작·종료 시각·모델, 회차당 12회 상한).
- 11-17 2차 개정(4칸): 패널은 사용자에게 필요한 것만 그린다. 위에서부터 🚨 문제(있을 때만) → 🙋 답할 것 → 🔧 직접 할 일 → 🔄 AI 진행(기능마다 한 줄). 맨 위 한 줄에 칸별 개수·수집 시각(3분 넘게 낡으면 빨강). 허브 표·PR 수·쉬는 자원·최근 기록·원료 상태는 `/board-detail` 로 본다(수집기 데이터는 그대로). 칸 순서·임계 시간(멈춤 의심 15분·끝난 기능 표시 60분·접기 8줄)·알림 상한(시간당 5, 같은 항목 30분)은 `board.ts` 의 `PANEL_CONFIG` 한 곳에서 바꾼다. 데스크톱 알림은 🙋·🚨 새 항목만. AI 상태는 세션 훅(`claude/hooks/ai-state.sh`)이 쓰는 `ai-state/<세션>.json` 을 먼저 보고 화면 글자 판별은 보조다. PR·CI 감시기의 `gh-state.json` 은 읽는 자리만 있고 스키마가 정해지면 🔧·🚨 가 그쪽을 읽는다(지금은 기존 gh 원료).
- 11-17 개정: 승인 칸·Slack 칸을 없애고 🙋 결정 대기 한 칸으로 합쳤다. 승인 성격 결정은 카드 label `gate:approval` 이고 패널에서 `✋ 승인` 배지로만 구분한다. Slack 봇을 다시 켜면 Slack 결정 카드의 원천은 작업판 결정 카드다(7-1절).
- 무엇이 바뀌었나: 허브 상태·결정 대기·최근 기록·PR 수·쉬는 자원(터미널 없는 워크스페이스·디스크 여유)은 이제 60초마다 스크립트가 자동으로 채운다. 패널 맨 위에 데이터 나이가 늘 보이고, 3분 넘게 안 채워지면 ⚠️ 가 붙는다. 작업판(beads, `~/orca/board`) 카드 수와 진행 중 카드도 보인다.
- 왜: 메인이 손으로 쓰던 패널이 하루 가까이 낡은 채 남은 일이 있었다. 기계가 셀 수 있는 칸은 기계가 채우고, 사람 판단이 필요한 칸(승인할 것·할 일)만 메인이 쓴다.
- 사용자가 할 일: 병합 뒤 설치기를 한 번 돌리고 수집기를 켜는 명령 한 줄을 실행한 다음, 메인 세션을 새로 시작한다(아래 「라이브 적용 절차」).

## 1. 구성

```text
 원료(읽기만)                              수집기                         패널 데이터                 화면
 ─────────────────────────────            ─────────────────────         ──────────────────         ──────────────
 orca terminal list ──(분류만)──┐
 slack-sent/inbox/done.jsonl ───┤          panel_collect.sh --once       panel-auto.json            lrm-board mod
 slack-push.log(결과 코드) ─────┼────────▶ (launchd 90초, 잠금,  ──────▶ (수집기만 씀,     ──┐      (15초 폴링,
 orca worker-list·task-list ────┤           원자 교체)                    원료별 ok·stale)    ├────▶  두 파일 합쳐
 gh pr list(5분마다, 선택) ─────┤
 orca worktree list·df·du ───────┤                                                             │       그림, ⚠️ 판정)
 bd list --all (작업판) ──────────┘   + launchd WatchPaths: .beads/last-touched·issues.jsonl 바뀌면 즉시 회차
 메인·워커 ──board-ask·board-answer·bd──▶ ~/orca/board (작업판 = 결정·할 일·진행의 정본)
```

파일을 둘로 나눈 이유: 쓰는 사람이 둘(수집기·메인)인 파일을 각자 읽고-고치고-쓰면, 늦게 쓴 쪽이 먼저 쓴 쪽의 갱신을 지운다. 한 파일 안에서 키만 나누면 이 유실을 막을 수 없다(잠금을 공유하지 않는 한). 파일을 나누면 각 파일의 쓰는 사람이 하나뿐이라 유실이 구조적으로 없고, 한쪽이 깨져도 다른 쪽 표시는 그대로다.

## 2. 파일·키 — 누가 쓰는가

| 파일 | 키 | 쓰는 사람 | 비고 |
|---|---|---|---|
| `~/.config/lrm-hubs/panel.json` | — | (폐지) | 11-16 부터 mod 가 읽지 않는다. 남아 있어도 무해 |
| `~/.config/lrm-hubs/panel-auto.json` | `version`·`updatedAt`(epoch 초)·`updatedAtText` | 수집기 | 메인은 쓰지 않는다 |
| 〃 | `sources.hubs` → `rows[]`(`hub`·`name`·`agent`·`state`·`quietMin`·`connected`·`dup`)·`counts`·`truncated` | 수집기 | `state`: busy 작업 중 · approval 승인창 · idle 대기 · unknown 확인불가. `truncated` 는 Orca 가 터미널 목록을 잘랐다는 뜻 |
| 〃 | `sources.pending` → `count`·`items[]`(`title` 40자·`ageMin`·`id`·`state`) | 수집기 | 제목은 우리가 보낸 결정 카드 제목(사용자 원문 아님). `id`(결정 번호)·`state`(`hold` 보류·`conflict` 충돌)는 번호 카드만, 옛 카드는 null |
| 〃 | `sources.board` → `counts`·`doing[]`·`more`·`blocked[]`·`decisions[]`·`todos[]`·`headline`(`id`·`title`·`done`·`total`·`epics`)·`recent[]`(`title`·`at`·`reason`) | 수집기 | `bd list --all --json`. 카드는 `id`·`title`·`p`·`who`·`createdAt`·`beatAt`(마지막 갱신)·`startedAt`·`comments`, 사람 손 카드만 `desc`(600자)·`gate`(label `gate:approval` 또는 옛 `kind:approval` 이면 `approval`). 라벨 `kind:decision`·`kind:user-todo`(옛 `kind:approval` 은 결정으로 읽음) 카드는 진행 줄에 넣지 않음 |
| 〃 | `sources.board.value.features[]` 의 `stage`·`next`(11-23) | 수집기 | `stage`: `key`·`name`·`idx`(0부터)·`total`·`names[]` 전체 단계 이름. 이름·순서는 `board/lib/board_flow.py` `STAGES` 에서 읽는다(수집기·패널에 다시 적지 않음). `flow:v1` 이 없거나 `stage:` label 이 없거나 board_flow 를 못 읽으면 `null`. `next`: `who`(`user`·`ai`)·`why`. 사용자 차례는 검수 단계, 설계 단계에서 `board_flow.claim_block` 이 계획 승인 없음으로 판정한 경우, 열린 결정 카드가 있는 경우다 |
| 〃 | `sources.push` → `lines[]`(`at`·`event`·`reason`) | 수집기 | `slack-push.log` 끝 5줄의 결과·사유 코드만 |
| 〃 | `sources.workers` → `live`·`recent[]`(`title` 40자·`status`·`at`) | 수집기 | 최근 종료 워커(완료 보고) |
| 〃 | `sources.agents` → `running[]`(`dispatch`·`name` 작업 공간 표시 이름 40자·`hub`·`agent`·`model`·`startedAt`)·`runningMore`·`finished[]`(같은 칸 + `status`·`finishedAt`)·`windowMin`(60)·`complete` | 수집기 | 🔄 AI 진행 칸 워커 줄(11-17) |
| 〃 | `sources.gh` → `repos[]`·`open`·`ciFail` 또는 `off:true` | 수집기 | `PANEL_GH_REPOS` 가 있을 때만 |
| 〃 | `sources.idle` → `terminals`·`workers`(상태별 건수·`leftover`·`items` 20개·`seen`·`total`·`complete`·`terminalStates`)·`workspaces`(`total`·`noTerminal`·`items` 20개·`more`·`truncated`)·`disk`(`freeGB`·`usedPct`·`minGB`·`warn`)·`wsSize`(`gb`·`at`·`err`) | 수집기 | 부분마다 따로 실패. `err` 는 `부분:종류` 목록 |
| 〃 | 원료마다 `ok`·`err`·`at`·`tried`·`stale` | 수집기 | 실패하면 `ok:false`·`err`(종류만)·`stale:true` 이고 `value`·`at` 은 마지막 성공 값 그대로 |
| `~/.config/lrm-hubs/panel.env` | `PANEL_GH_REPOS`·`PANEL_GH_INTERVAL`·`PANEL_HUB_ROOT`·`PANEL_RECENT`·`PANEL_DISK_MIN_GB`·`PANEL_WS_DIR`·`PANEL_DU_INTERVAL`·`PANEL_DU_TIMEOUT` | 사용자(선택) | 시크릿 아님. 양식 `panel.env.example`. 환경 변수가 우선 |
| `panel-collect.log`·`.panel-gh.stamp`·`.panel-du.stamp`·`.panel-collect.lock` | — | 수집기 | 로그는 원료별 결과 코드만. 1MB 넘으면 `.1` 로 돌림 |

수집기가 저장하지 않는 것: 터미널 화면(preview) 본문, Slack 메시지 본문, 토큰, gh 출력 원문, 터미널 핸들. 수집기는 `slack-bot.env`·`main.env` 를 읽지 않고 Slack API 를 부르지 않는다.

## 3. 판정 규칙

### 허브 식별

`patrol.sh` 와 같다. 작업 폴더 이름이 `coord-*` 이고 에이전트가 붙은 터미널(`agentIdentity` 가 비어 있지 않음). `PANEL_HUB_ROOT` 를 주면 그 아래 폴더만 본다. 같은 폴더의 창이 둘 이상이면 `dup:true`(👥중복). 표시 이름은 앞의 `coord-`·뒤의 `-hub` 를 뗀 것이다.

### 허브 상태 분류(위에서부터 먼저 맞는 것)

| 순서 | 근거 | 결과 |
|---|---|---|
| 0 | 끊김·고아 터미널, 빈 화면 | 확인불가 |
| 1 | 화면 끝 8줄에 권한·선택 대화상자(`slack_push.py` 의 판별식: `do you want`·`(y/n)`·`❯ 1.` 등) | 승인창 |
| 2 | `esc to interrupt`, 또는 회전 기호(✻·✢ 등) + 영어 동사(한두 단어) + `…` 로 시작하는 줄(예 `✻ Thinking…`). 글머리 `· 설치 정리 중…` 같은 줄은 아니다 | 작업 중 |
| 3 | 화면 끝에 빈 입력 줄(또는 쓰던 글) | 대기 |
| 4 | `patrol.sh` 의 막힘 키워드(`do you want`·`allow`·`승인`·`(y/n)`·`❯ 1.`) | 승인창 |
| 5 | 출력이 2분 넘게 없음 | 대기 |
| 6 | 그 밖 | 확인불가 |

4번은 오탐이 있다. `allow`·`승인` 은 평범한 보고 글에도 나온다. 1~3번으로 판별되지 않을 때만 쓰는 이유다.

### 결정 대기

`slack_pending.sh` 와 같은 판정이다. 대상은 보낸 메시지 중 최상위(`thread_ts` 없음)이고 제목에 `결정` 이 들었거나 번호 카드(`slack_notify.sh --options`, 기록에 `form_kind: decide` 와 `options`)인 것이다.

| 카드 | 닫히는 조건 | 같은 제목 |
|---|---|---|
| 옛 결정 카드 | 반응(`slack-inbox.jsonl` 의 `reaction` → `parent_ts`)이 하나라도 있거나 `slack-done.txt` 기록 | 한 건 |
| 번호 카드 | 폴러가 해석한 답(`kind: decide`·같은 `decision_id`·`pick`·`approve`)이 하나로 모임, 또는 `slack-done.txt` 기록. 보류(❌ `hold`)·충돌(답이 둘 이상·`conflict`)은 대기로 남기고 `state` 에 적는다. 해석되지 않은 반응은 답이 아니다 | 메시지마다 따로 |

결정 번호가 형식(`D-` 와 영숫자 1~16자)에 맞지 않거나 없는 번호 카드 기록은 옛 결정 카드로 본다. 반응 하나로 닫히고, 대기 목록에는 번호 없이 제목만 싣는다(`id` null). `slack_pending.sh` 와 같다.

번호 카드가 없는 기록(main 형식)에서는 옛 규칙과 결과가 같다. `slack_pending.sh` 는 Slack 에 글을 보내고 토큰을 읽어 부를 수 없으므로 같은 식을 수집기에 두고, `tests/panel/run.sh` 가 원본 줄을 대조한다. main 형식과 번호 카드 형식(이모지 카드 브랜치) 두 원본을 모두 인정하고, 어느 쪽도 아니면 시험이 실패한다. 두 형식의 기록을 각각 넣은 시험도 있다(`fixtures.py`·`fixtures_cards.py`).

### 오래됨

| 대상 | 기준 시각 | 경고 |
|---|---|---|
| 자동 칸 | 파일 안 `writtenAt`(쓴 순간, 없으면 `updatedAt`). 없거나 지금보다 2분 넘게 미래면 파일 수정 시각 | 맨 위에 늘 "데이터 N초 전". 3분 초과·파일 없음·못 읽음 → 빨간 ⚠️ |
| 메인 칸 | 파일 수정 시각 | 30분 초과 → 노란 글씨 "메인 칸이 N분 전 갱신됨" |

자동 칸에 파일 안 epoch 를 쓰는 이유: 수집기가 기록하는 순간의 시계라 데이터가 언제 모였는지를 그대로 말한다. 수정 시각은 복사·백업 복원·`touch` 로도 새로워져 낡은 데이터를 새것처럼 보이게 할 수 있다. 메인 칸에 수정 시각을 쓰는 이유: 메인이 글자로 적는 `updatedAt` 은 실제와 42분 어긋난 전례가 있다.

### 쉬는 자원(😴 칸)

쌓이면 디스크·메모리를 먹는 자원을 숫자로 보여 준다. **목록과 숫자만 만들고 정리(삭제·종료)는 하지 않는다.** 수집기 코드에 삭제·종료 명령이 없다는 것을 시험이 세 겹으로 고정한다: 문자열(`rm`·`worktree rm`·`terminal close`·`worker-stop`·`branch -D` 등), 구문 트리(`os.remove`·`os.kill`·`unlink` 같은 호출은 정해진 자리에만, `tests/panel/no_delete_check.py`), 시험 중 실제로 부른 orca·git·gh·du·df 명령이 읽기 명령뿐인지. 수집기가 지우는 것은 자기 임시 파일·잠금 폴더뿐이고, 끝내는 것은 시간 상한을 넘긴 자기 자식(du) 묶음뿐이다.

| 항목 | 원료 | 뜻 |
|---|---|---|
| 터미널 없는 워크스페이스 | `orca worktree list --limit 1000` 와 `orca terminal list --limit 1000` 의 `worktreeId` 대조(메인 체크아웃 제외) | 열린 터미널이 0개인 워크스페이스 수. 오래 쉰 것부터 20개를 이름·`~` 경로·브랜치·쉰 일수·병합 여부와 함께 싣고 나머지는 "외 N개". 두 목록 중 하나라도 잘리면 `truncated:true`·"목록 잘림" |
| 병합 여부 | `git -C <경로> merge-base --is-ancestor HEAD <baseRef>` | 기준 ref 가 없거나 git 이 답하지 못하면 모름(null). 목록 20개에만 확인 |
| 종료 뒤 남은 터미널 | `orca orchestration worker-list --full --limit 100`, `nextCursor` 로 끝 쪽까지 | 끝난(`succeeded`·`failed`·`stopped`·`abandoned`) 워커 중 터미널이 아직 `retained`·`active` 등인 것. dispatch id·작업 제목·상태만. Orca 는 한 번에 최신 100건만 주므로(실측 2034건) 쪽을 넘겨 센다. 쪽 수 60·40초 상한에 걸리면 거기까지 세고 `complete:false`, 패널에 "N건 중 M건만 셈". 중간 쪽이 실패하면 숫자를 싣지 않고 원료 실패로 둔다. Orca 가 주는 터미널 상태별 총계(`counts`)는 `terminalStates` 에 그대로 싣는다 |
| 디스크 여유 | `df -k ~` | 홈 볼륨 여유 GB·사용률. `PANEL_DISK_MIN_GB`(기본 50) 미만이면 빨간 글씨 |
| 워크스페이스 폴더 크기 | `du -sk <PANEL_WS_DIR>` | 느려서 30분마다만 잰다(간격 표식은 재기 전에 갱신). `PANEL_DU_TIMEOUT`(기본 120초)을 넘기면 du 와 그 자식까지 끝내고 직전 값을 둔 채 `err:timeout` 과 "측정 시간 초과" 를 표시한다. 원료 전체를 실패로 보지는 않는다. 로그에는 잰 회차에만 `du=timeout` |
| 터미널 수 | `orca terminal list` | 전체·에이전트 붙은 수. 프로세스 메모리는 재지 않는다 |

정리는 사람이 한다. 예: 각 허브가 자기 하위 워크스페이스를 정리하고, 규칙은 "병합됨 + 터미널 없음 + `.claude` 보관 뒤 삭제"를 따른다(`worktree-gc` 스킬).

### gh 간격

수집 주기(90초)마다 gh 를 부르면 하루 약 1,000회 GitHub API 를 쓴다. `PANEL_GH_INTERVAL`(기본 300초) 안에서는 부르지 않고 직전 값을 그대로 둔다. 간격은 `.panel-gh.stamp` 수정 시각으로 재며, 실패한 시도도 간격을 지킨다(실패 때 연속 재시도 방지). 저장소당 PR 은 100건까지만 받는다. 100건이 꽉 차면 `capped:true` 이고 패널은 `열린 PR 100+` 처럼 하한으로 보인다.

## 4. 켜기·끄기·되돌리기

| 할 일 | 명령 |
|---|---|
| 켜기 | `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/ai.lrm.panelcollect.plist` |
| 상태 보기 | `launchctl print gui/$(id -u)/ai.lrm.panelcollect \| grep -E 'state\|last exit'` · `tail -3 ~/.config/lrm-hubs/panel-collect.log` |
| 한 번 돌려 보기(파일 안 씀) | `bash ~/.config/lrm-hubs/panel_collect.sh --print` |
| 끄기 | `launchctl bootout gui/$(id -u)/ai.lrm.panelcollect` (끄면 3분 뒤 패널에 ⚠️ 가 뜬다) |
| 로그인 때도 안 켜지게 | 끈 뒤 `~/Library/LaunchAgents/ai.lrm.panelcollect.plist` 를 다른 폴더로 옮긴다 |
| PR 수 끄기 | `panel.env` 의 `PANEL_GH_REPOS=` 를 비운다 |
| 전체 되돌리기 | 수집기를 끄고 `./install.sh --restore ~/.claude-config-backup/<시각>`. 그 설치가 새로 만든 mod·plist·링크가 빠진다. `panel-auto.json` 은 남아도 되며, 옛 mod 는 읽지 않는다 |

## 5. 라이브 적용 절차(병합 뒤, 코디네이터가 한다)

1. 메인 체크아웃 `~/orca/claude-config` 에서 `./install.sh --dry-run` 으로 바뀔 항목을 본다. 새로 생기는 것: `~/.config/lrm-hubs/panel_collect.{sh,py}`·`panel.env.example`·`docs/panel.md` 링크, `~/Library/LaunchAgents/ai.lrm.panelcollect.plist`, `~/.claude/skills/lrm-board/` 5개 파일.
2. `./install.sh` 를 돌린다. 끝에 `launchd 재적재 필요: … ai.lrm.panelcollect` 줄이 나온다(설치기는 적재하지 않는다).
3. `bash ~/.config/lrm-hubs/panel_collect.sh --print | head -40` 으로 한 번 확인한다. `hubs`·`pending`·`push`·`workers` 의 `ok` 가 `true` 면 된다.
4. 수집기를 켠다: `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/ai.lrm.panelcollect.plist`. 60초 안에 `~/.config/lrm-hubs/panel-auto.json` 이 생긴다.
5. PR 수가 필요하면 `cp ~/.config/lrm-hubs/panel.env.example ~/.config/lrm-hubs/panel.env` 뒤 `PANEL_GH_REPOS=` 를 채운다. 다음 수집부터 반영된다.
6. 메인 세션을 새로 시작한다. 지금 메인 세션은 세션 전용 mod 폴더(dev-mods)에서 옛 `lrm-board` 를 읽고 있어, 그대로 두면 같은 이름의 mod 가 둘이 된다. 새 세션은 `~/.claude/skills/lrm-board` 만 읽는다. 새 세션에서 `/lrm-board` 를 열어 맨 위에 ⚠️ 가 없는지 본다.
7. 옛 사본 `~/.config/lrm-hubs/mods/lrm-board` 는 더 쓰지 않는다. 헷갈리면 다른 이름으로 옮겨 둔다(설치기는 지우지 않는다).

### 안 보일 때

새 세션에서 `/lrm-board` 명령이 없으면 Claude Code 가 `~/.claude/skills/lrm-board` 를 플러그인으로 읽지 않은 것이다. 먼저 `claude plugin validate ~/.claude/skills/lrm-board` 로 형식을 본다. 형식이 맞는데도 안 읽히면 사용자 설정 `~/.claude/settings.json` 의 `env` 에 아래 한 줄을 넣는 방법이 있다(사용자 승인 뒤, 설정 파일은 사람이 고친다).

```json
"CLAUDE_CODE_PLUGIN_DIRS": "~/.claude/skills/lrm-board"
```

이 변수는 `--plugin-dir` 과 같게 그 폴더를 매 세션 읽게 한다. 이 경우 저장소 `claude/settings.base.json` 에도 같은 줄을 넣어야 다음 설치 때 사라지지 않는다. 지금 `settings.base.json` 에는 `env` 블록이 없으므로 맨 위 단계에 `"env": { … }` 블록을 새로 만든다.

## 6. 메인 코디네이터가 할 일

- 패널 파일(`panel.json`·`panel-auto.json`)은 쓰지 않는다. 카드만 다룬다.
- 결정 올리기: `~/.config/lrm-hubs/board-ask "<질문>" --options "A => 결과|B => 결과" --recommend A --runnable no --background "<배경>" --terms "<용어>" --doc <설명서.html> [--why "<이유>"] [--parent <epic>]` → `kind:decision` 카드. `--runnable`(돌려 보면 알 수 있나)은 필수다. `yes` 면 카드를 만들지 않고 종료 5 — 동작·배치·시간처럼 실행하면 아는 사실은 시제품·실측으로 정하고 근거를 작업 카드 코멘트에 남긴다. 승인 성격은 `--gate approval`(옛 `--kind approval` 도 받아 바꿈), 사용자 할 일은 `--kind user-todo`. 결정 카드는 배경·용어·추천·선택지별 결과·HTML 설명서(`--doc`)가 모두 필수이고 하나라도 없으면 종료 2 다. 설명서는 배경(상세) 절·도식(도형 3개 이상 SVG)·선택지별 결과·추천 이유를 갖추고, 한 줄에 사실 하나만 쓴다(` / `·가운뎃점 나열 금지). 검사는 `board_cards.doc_gaps` 한 곳이고 Slack 전송 직전에 같은 검사를 다시 한다. 여러 카드를 한 파일에 담은 표준 형식(구역 `div.bg`·카드 절 `data-check=dcard`·선택지 `data-check=opt`·`why-rec`)은 `--doc 경로#카드id` 로 그 카드 절만 검사한다. 통과하는 뼈대는 `tests/board_cards/fixtures/standard.html`.
- 사용자 답 받기: `~/.config/lrm-hubs/board-answer <id> "<사용자 원문>"` → 원문 코멘트 + 닫기(보류면 `--keep-open`). 같은 답을 두 번 넣어도 이미 닫힌 카드는 그대로다.
- 헤드라인은 열린 epic 중 우선순위가 가장 높은(같으면 오래된) 것이다. 바꾸려면 epic 우선순위를 고친다.
- 진행 줄의 "신호"는 카드의 마지막 갱신 시각이다. 30분 넘으면 ⚠ 가 붙는다. 워커는 진행 중 카드를 `bd update`(메모·상태)로 갱신한다.
- 패널 맨 위 ⚠️ 는 수집기가 멈췄다는 뜻이다. `tail ~/.config/lrm-hubs/panel-collect.log` 와 `launchctl print gui/$(id -u)/ai.lrm.panelcollect` 를 본다.

## 7. 알려진 한계

- 화면 분류는 Orca 가 주는 화면 끝 300자만 본다. 위쪽에 있는 질문은 못 보고, `allow`·`승인` 키워드는 평범한 글에도 걸린다. 확인불가가 나오면 허브 화면을 직접 본다.
- 확인불가가 많은 원인은 분류 규칙보다 원료다. 2026-11-10 실측(허브 창 16개, 화면 글 없이 표식만 셈): 끝 300자 안에 입력 칸 줄이 보인 창은 3개뿐이라 대기 판정은 대부분 "2분 넘게 출력 없음" 에 기댄다. 확인불가 6개 중 4개는 방금 출력이 있었고(상태줄 갱신 등) 작업 표식도 없는 창, 2개는 끊긴 창이었다. 머리 숫자 "작업 중 0" 은 실제와 다를 수 있다.
- 결정 대기는 Slack 에 나간 카드(`결정` 제목 또는 번호 카드)만 센다. 허브 화면에만 있는 결정은 못 센다. 판정식은 `slack_pending.sh` 와 두 벌이라, 그쪽 규칙이 또 바뀌면 시험이 실패하고 수집기를 같이 고쳐야 한다.
- 최근 종료 작업 시각은 Orca 의 `releaseCompletedAt`(터미널 정리 시각, UTC 로 읽음)이다. 작업 완료 시각과 몇 분 차이가 날 수 있다.
- 워크스페이스 목록은 Orca 가 관리하는 것만 센다. Orca 밖에서 만든 git worktree·폴더는 빠진다. 활동 시각(`lastActivityAt`)이 0 이면 만든 시각을 쓰고, 둘 다 없으면 목록 뒤로 보낸다.
- 작업 제목은 `task-list --run <Run>` 로 얻는다. 최근 기록의 Run 이 셋보다 많으면 나머지는 제목 없이 상태만 나온다.
- 잠금 회수에 좁은 경쟁이 있다. 두 실행이 동시에 오래된 잠금을 보면 둘 다 돌 수 있다. 결과 파일은 원자 교체라 깨지지 않고, 늦게 쓴 쪽 값이 남는다. 실행 중에는 잠금 시각을 60초마다 갱신해(최대 30분) 느린 회차가 회수되지 않게 한다. 끝날 때는 잠금 폴더가 얻을 때와 같은 폴더(inode)일 때만 지운다. 회수된 뒤 다른 실행이 만든 잠금은 남기고 로그에 `lock lost` 를 쓴다. 대조와 삭제 사이의 아주 짧은 경쟁은 남는다.
- worker-list 는 회차마다 전 쪽을 받는다. 2026-11-10 실측 2054건 = 21쪽, `--print` 1회 4.2~4.7초(90초 주기의 약 5%). 잠금이 겹침을 막고 쪽 60·40초 상한이 있어 간격을 두지 않았다. 1회가 10초를 넘기면 gh 처럼 간격(예 5분)을 두고, 사이 회차는 첫 쪽 `counts` 와 직전 값을 쓰는 안을 본다.
- 원료 하나에서 예상 밖 오류(JSON 모양이 다름 등)가 나면 그 원료만 `err: internal:<예외 이름>` 으로 실패하고 다른 원료는 그대로 갱신된다.
- 실제 launchd 적재와 패널 화면 렌더는 이 브랜치에서 확인하지 않았다(라이브 적용 금지). 검증은 샌드박스 시험과 `claude plugin validate` 까지다.
- mod 타입 검사는 `npx -p typescript tsc -p <배포 사본>` 으로 한다(전역 `tsc` 없음).
- `bd comments add` 는 `last-touched` 를 고치지 않는다. 코멘트만 단 변화는 다음 60초 회차에 반영된다. `bd show` 도 `last-touched` 를 고쳐 회차가 더 돌 수 있으나 launchd 10초 간격 제한이 있다.
- Slack 번호 카드(`sources.pending`)·Slack→메인 전달(`sources.push`)은 수집만 하고 화면에 그리지 않는다. 봇을 다시 켜도 결정은 작업판 카드가 원천이라 결정 대기 칸 하나면 된다.

## 7-1. Slack 봇 재가동 때 작업판 연결 (11-17)

봇은 꺼 둔 채 연결 코드만 넣었다. 켤 때 순서:

1. 소켓 수신기·폴러를 켠다(설치 안내 `install-orca-main.md`).
2. `touch ~/.config/lrm-hubs/.board-slack.on` — 이 파일이 있을 때만 `board-ask` 가 새 결정 카드를 Slack 번호 카드로 바로 보낸다(`board_slack.py push`). 이미 있는 카드는 `board_slack.py push <id>` 로 하나씩 보낸다.
3. 보낸 카드에는 label `slack:<결정 번호>`(카드 id 로 만든 `D-B…`)가 붙는다. 같은 카드를 다시 보내지 않는다.
4. 버튼 답·직접 입력은 소켓 수신기가 결정 번호로 카드를 찾아 원문 코멘트 + 닫기(`board-answer` 와 같은 처리). 직접 입력이 질문이면 코멘트만 남기고 카드를 열어 둔다. 허브·메인 받은 편지함으로는 보내지 않는다. 전달 기록 `answer-delivery.jsonl` 의 state 는 `board`.
5. 끄기: `.board-slack.on` 을 지운다. 작업판 카드가 아닌 옛 결정 번호(`D-RDY…` 등)는 지금처럼 허브·메인 경로를 탄다.

필수 칸(배경·용어·선택지 결과·추천)이 없는 카드는 보내지 않고 원인·조치를 낸다. 이모지 반응·스레드 글 답은 아직 작업판으로 옮기지 않았다(버튼 답만).

## 7-2. 메인 입력은 사용자만 — 자동 알림은 작업판 카드로 (11-17)

- `~/.config/lrm-hubs/main-route`(저장소 `hubs/main-route`, 값 `board`)가 있으면 허브 보고·CI·리뷰·Slack 답·멈춤 같은 메인 대상 자동 알림이 `hub-inbox/main.jsonl` 대신 작업판 카드 코멘트가 된다(`board_notify.py`, 호출처는 그대로 `main_inbox.py`).
- 카드 찾기: 결정 번호 → `slack:<번호>` 카드, PR 번호 → `pr:<n>` 카드, 본문의 카드 id → 그 카드, 못 찾으면 종류별 모음 카드 `📥 자동 알림: <종류>`(label `kind:alert`).
- 작업판 쓰기가 실패하면 `main.jsonl` 로 떨어진다. 끄려면 `main-route` 를 지운다(링크만 지우면 다음 `install.sh` 가 되살리므로 `links.tsv` 줄도 뺀다).
- 허브 보고는 맡은 카드에 `bd comments add`, 카드가 없으면 `board_notify.py post`. 메인 입력창 터미널 전송은 훅이 막는다.
- Slack 사용자 메시지(최상위 DM·스레드 답글·반응)도 같은 경로다. `slack_push` 가 메인 터미널에 치지 않고 메시지마다 카드 코멘트(결정 답은 그 결정 카드, 그 밖은 `📥 자동 알림: slack`)를 남기고 데스크톱 알림을 묶음당 1회 띄운 뒤 읽음 표식을 올린다(Slack 확인 답장은 `📋 작업판에 기록했어요`).

## 7-3. Slack 기능 스레드 피드백 → 기능 카드 (11-17, 봇 꺼짐)

- 기능 코디네이터가 첫 Slack 보고 ts 로 `feedback_route.py open-thread --epic <epic> --ts <ts>` 를 실행하면 epic 에 `slack-thread:<ts>` 가 붙는다.
- 폴러 회차마다 `feedback_route.py --once` 가 그 스레드의 사용자 답글을 원문 그대로 epic 코멘트와 `hub-inbox/feat-<slug>.jsonl` 로 보낸다. 메인 전달(`slack_push`)에서는 빠진다.
- 같은 스레드가 epic 여럿에 걸렸거나 닫힌 epic 만 있으면 메인 카드 `📥 기능 판별 필요`(label `feedback:triage`)에 모은다.

## 7-4. 에픽 하위 작업 펼치기 토글 (11-17)

🔄 AI 진행 칸의 에픽 줄 제목 끝에 닫힌/전체 `(3/6)` 이 한 번 붙고, 바로 아래 한 줄에 `▾ ▰▰▰▱▱` 처럼 펼침 표시와 진행률 막대가 나온다(꼬리에 두면 좁은 패널에서 제목 칸이 줄어 줄이 꺾여 아래로 내림). 하위 카드가 없는 에픽은 막대·표식 없이 기존 한 줄이다. 펼친 에픽은 그 아래에 하위 작업 목록이 나온다. 한 줄은 `○ 열림 · ◐ 진행 중 · ✕ 막힘 · ✓ 닫힘` 표시, 제목, 담당, (그 아래 카드가 있으면 `하위 N`), 마지막 갱신이다. 깊이는 에픽 → 하위 카드 1단이다. 닫힌 에픽은 목록 없이 한 줄만 보인다.

| 명령 | 동작 |
|---|---|
| `/board-epics on` | 전체 펼침. 에픽별 예외를 지운다 |
| `/board-epics off` | 전체 접힘. 에픽별 예외를 지운다 |
| `/board-epics` | 전체를 반전한다 |
| `/board-epics <에픽 id>` | 그 에픽만 반전한다. id 는 끝·앞 일부(`q5o`)도 되며 둘 이상 맞으면 거절한다 |

- 입력은 슬래시 명령이다. 이 mod 는 키·클릭 입력을 받지 않는다.
- 기본값은 전체 펼침이다. 상태는 `~/.config/lrm-hubs/panel-view.json`(`{"all": true, "epics": {"<id>": false}}`)에 저장돼 패널을 다시 열어도 유지된다. 파일이 없거나 깨지면 기본값으로 읽는다. 경로는 `register.tsx` 의 `VIEW_FILE` 한 곳이다. 다른 세션에서 바꾼 값도 5초 안에 반영된다.
- 펼친 에픽 하나가 그리는 줄은 `board.ts` 의 `PANEL_CONFIG.epicKidsMax`(5)까지이고 넘치면 `외 N개` 한 줄이다. 하위 제목은 `kidTitleMax`(16자)에서 …로 자른다. 패널 폭은 렌더에서 알 수 없어 폭에 맞춘 상한 대신 고정 상한을 쓴다. 펼친 에픽이 있으면 AI 진행 칸 머리 줄에 `접기: /board-epics off` 안내가 보인다. 막대 칸 수는 `barCells`. 수집기가 싣는 하위 카드 상한은 `panel_collect.py` 의 `EPIC_KIDS_MAX`(30)다.
- 원료는 `sources.board.value.features[].kids[]`(`id`·`title`·`st`(todo·doing·blocked·done)·`who`·`at`·`subs`)이다. 기존 `bd list --all --json` 한 번의 결과에서 만들며 카드마다 `bd` 를 부르지 않는다.

### 하위 카드 둘째 줄: 갱신·증거 (11-17, lrm-fxm)

진행 중(`◐`) 하위 카드 아래에 한 줄이 더 붙는다. 담당 이름은 첫 줄에 있어 되풀이하지 않는다.

| 표시 | 뜻 |
|---|---|
| `갱신 N분` | 마지막 heartbeat 이후 경과. heartbeat 가 없으면 카드 마지막 변경 시각으로 대신한다 |
| `갱신 N분 ⚠` | 30분 넘게 갱신이 없다(줄 색도 경고). 기준은 `board.ts` 의 `PANEL_CONFIG.beatStaleMin` 한 곳이다 |
| `증거 ✓` | 증거 커밋(`board-run --card` 의 metadata 또는 코멘트의 `evidence:… commit=` 줄)이 작업 브랜치 끝과 같다 |
| `증거 없음` | metadata 에도 코멘트에도 증거가 없다 |
| `증거 낡음` | 증거 커밋이 브랜치 끝과 다르다(증거 뒤에 커밋이 생김). 다시 돌려야 한다 |
| `증거 ?` | 증거는 있으나 비교할 수 없다: 코멘트 줄에 `commit=` 이 없거나, 브랜치 끝을 모르거나, 코멘트를 읽지 못했다(`bd export` 실패) |

- 열림·막힘·닫힘 카드에는 둘째 줄이 없다. 폭 40·60·100 에서 한 줄이다(`tests/panel/board.test.mjs`). 담당 이름은 `kidWhoMax`(12자)에서 자른다.
- 원료는 `features[].kids[]` 의 `beatAt`(epoch 초)·`ev`(`none`·`ok`·`stale`·`unk`)다. 기존 `bd list --all --json` 한 번에서 만들며 카드마다 `bd` 를 부르지 않는다. 브랜치 끝은 같은 저장소·브랜치당 `git rev-parse` 한 번이다.
- 진행 줄의 `beatAt` 도 heartbeat 와 마지막 변경 중 늦은 쪽이다.

**heartbeat 남기기**: `board-beat <카드> <작업자> ["한 줄 메모"]`

- 갱신 시각은 beads 임대의 `heartbeat_at`, 메모는 카드 metadata `beat_note`(한 줄·80자)에 남는다. 코멘트는 늘지 않는다.
- 담당 작업자만 갱신할 수 있다(아니면 종료 13). 반복 실행해도 안전하다. `--help` 에 종료 코드가 있다.
- `board-claim`(다시 집기 포함)과 `board-run <종류> --card <카드> --actor <작업자> -- <명령>` 이 자동으로 부른다. `board-run` 은 명령이 성공하고 작업 트리가 HEAD 와 같을 때만 증거 커밋(`evidence_<종류>`)도 남긴다.
- 단계가 바뀔 때와 30분 넘게 길어질 때 손으로 한 번씩 부른다.

**코멘트 증거도 읽는다.** 지금까지처럼 `board-run` 이 출력한 줄을 코멘트로 달아도 된다. 수집기가 진행 중 카드의 notes·코멘트를 `bd export` 한 번으로 모아(카드마다 `bd` 를 부르지 않는다) 종류별 마지막 `evidence:` 줄을 읽는다. 해석은 `board-done` 과 같은 `board/lib/board_evidence.py` 를 쓴다. `commit=` 이 없는 줄은 비교할 수 없어 `증거 ?` 로 보인다.

## 7-5. 기능 줄 단계 띠와 다음 차례 (11-23)

- 줄 모양: `■■■◆□□□ 구현 · 제목 · 지금 하는 일 (1/3)`. `■` 지난 단계, `◆` 지금 단계, `□` 남은 단계다. 칸 수와 이름은 수집기 값(`stage.total`·`stage.name`)이고 패널 코드에는 단계 이름이 없다. 글자는 `board.ts` `PANEL_CONFIG.band` 한 곳이다.
- 사용자 차례(`next.who` 가 `user`)인 단계 있는 기능은 기존 `?` 표시를 쓰고 설명(`계획 승인 대기`·`검수 통과 답 대기`·`결정 카드 N건 답 대기`)이 지금 하는 일 자리에 나온다. 검수 대기 기능은 카드가 닫혀 있어도 `doneShowMin` 이 지나도록 계속 보인다. 새 칸은 없다.
- `/board-detail` 기능 항목 아래에 `단계: 접수 · … · [구현] · …`(지금 단계 대괄호)와 `다음 차례: 내 차례|AI — 설명` 이 붙는다.
- 단계가 없는 기능(옛 방식)의 줄은 이전과 같은 글자다. 패널은 읽기만 하며 단계를 옮기는 것은 `board-stage` 다.
- 시험: `tests/panel/board_beads.sh`(임시 작업판의 단계·다음 차례·board_flow 못 읽음), `tests/panel/board.test.mjs`(줄·상세 글자). 수집기는 `PANEL_BOARD_LIB` 로 board_flow 위치를 바꿀 수 있다(시험용).

## 8. 후속 제안

- Slack 할 일 목록(리스트) 읽기: 봇 토큰 스코프(`lists:read` 등) 확인이 먼저다. 수집기는 지금 토큰을 읽지 않으므로, 읽게 하려면 토큰을 다루는 별도 단계(폴러 쪽)에 붙이는 편이 맞다.
- 프로세스별 메모리(에이전트 세션 RSS 합계)를 쉬는 자원 칸에 더하기. `ps` 로 잴 수 있으나 이번 범위 밖이다.
- 확인불가 줄이기: 확인불가 허브만 화면을 더 길게 읽어(읽기 전용 조회) 입력 칸을 찾거나, 끝난 턴 요약 줄(`✻ Worked for …`)을 대기 근거로 쓰는 안. 오판 위험이 있어 이번 범위 밖이다.
- 허브별 `decisions.tsv` 의 오늘 줄 수를 자동 칸에 더하면 `catch-up` 없이도 "사람 없이 내린 결정" 수를 볼 수 있다.

## 참고

- 수집기: `hubs/scripts/panel_collect.sh`(진입)·`panel_collect.py`(본체), 설정 양식 `hubs/panel.env.example`
- launchd: `hubs/launchd/ai.lrm.panelcollect.plist`
- mod: `hubs/mods/lrm-board/hooks/register.tsx`(화면)·`board.ts`(판정 함수)·`types/index.d.ts`
- 시험: `bash tests/panel/run.sh`, 변이 `bash tests/panel/mutate.sh`, node 시험 `node --test tests/panel/board.test.mjs`
- 조사 사실표: `~/Documents/90_정리기록/2026-11-10-소통개선조사/facts-panel.md`

## 허브 창 질문 경고 (2026-11-13)

허브 화면 끝에 '이 창에서 확인·답·승인' 류 문구가 보이면 수집기가 허브 행에 `askHere: true` 를 남기고 로그(`panel-collect.log`)에 `WARN ask-here=<허브>` 한 줄을 쓴다. 결정은 허브 창이 아니라 `hub_report.py decide` Slack 카드로 가야 사용자가 본다. 화면 내용은 저장하지 않고 허브별 참·거짓만 남긴다. 화면 끝 300자 안팎만 보므로 놓칠 수 있다.

## 2026-11-16 갱신 지연 수정

- 증상: 주기 90초인데 실측 간격 2분 4초~2분 10초, 한 회차 약 30초(`--print` 는 7초).
- 원인: plist `ProcessType=Background` — macOS 가 CPU·I/O 를 낮추고 타이머를 묶어 늦춤.
- 조치: `ProcessType=Standard`, `StartInterval=60`. 파일에 `writtenAt` 을 더해 나이를 쓴 순간 기준으로 잼.
- 작업판 원료 `board`: `bd list --json --limit 0`(cwd `~/orca/board`, `PANEL_BOARD_DIR` 로 변경). 진행 중·열림·막힘 수와 진행 중 카드 8개까지(제목·담당).
