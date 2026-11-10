# 옆 패널(lrm-board) 런북

옆 패널의 자동 칸을 채우는 수집기, 메인이 쓰는 손 칸, 둘을 합쳐 그리는 mod 의 구성·켜기·끄기·되돌리기를 적는다. 작성일은 2026-11-10 이다.

## 쉬운 말 3줄 요약

- 무엇이 바뀌었나: 허브 상태·결정 대기·최근 기록·PR 수·쉬는 자원(터미널 없는 워크스페이스·디스크 여유)은 이제 90초마다 스크립트가 자동으로 채우고, 10분 넘게 안 채워지면 패널 맨 위에 ⚠️ 가 뜬다.
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
 orca worktree list·df·du ───────┘                                                             │       그림, ⚠️ 판정)
                                                                         panel.json         ──┘
 메인 코디네이터(손으로) ──────────────────────────────────────────────▶ (메인만 씀)
```

파일을 둘로 나눈 이유: 쓰는 사람이 둘(수집기·메인)인 파일을 각자 읽고-고치고-쓰면, 늦게 쓴 쪽이 먼저 쓴 쪽의 갱신을 지운다. 한 파일 안에서 키만 나누면 이 유실을 막을 수 없다(잠금을 공유하지 않는 한). 파일을 나누면 각 파일의 쓰는 사람이 하나뿐이라 유실이 구조적으로 없고, 한쪽이 깨져도 다른 쪽 표시는 그대로다.

## 2. 파일·키 — 누가 쓰는가

| 파일 | 키 | 쓰는 사람 | 비고 |
|---|---|---|---|
| `~/.config/lrm-hubs/panel.json` | `headline` | 메인 | 한 줄 요약(선택) |
| 〃 | `approvals[]` (`p`·`title`·`why`·`how`·`detail`·`links`) | 메인 | 🙋 내가 승인할 것. `why` 가 결정 추천 이유 |
| 〃 | `todos[]` (같은 모양) | 메인 | 🛠️ 내가 할 일 |
| 〃 | `history[]` (`at`·`hub`·`text`·`ref`) | 메인 | 📜 진행 기록 |
| 〃 | `hubs[]` | 메인(옛 방식) | 자동 칸이 없을 때만 표시. 새로 쓰지 않아도 된다 |
| 〃 | `updatedAt` | 메인(옛 방식) | 더는 화면에 쓰지 않는다. 나이는 파일 수정 시각으로 계산 |
| `~/.config/lrm-hubs/panel-auto.json` | `version`·`updatedAt`(epoch 초)·`updatedAtText` | 수집기 | 메인은 쓰지 않는다 |
| 〃 | `sources.hubs` → `rows[]`(`hub`·`name`·`agent`·`state`·`quietMin`·`connected`·`dup`)·`counts` | 수집기 | `state`: busy 작업 중 · approval 승인창 · idle 대기 · unknown 확인불가 |
| 〃 | `sources.pending` → `count`·`items[]`(`title` 40자·`ageMin`) | 수집기 | 제목은 우리가 보낸 결정 카드 제목(사용자 원문 아님) |
| 〃 | `sources.push` → `lines[]`(`at`·`event`·`reason`) | 수집기 | `slack-push.log` 끝 5줄의 결과·사유 코드만 |
| 〃 | `sources.workers` → `live`·`recent[]`(`title` 40자·`status`·`at`) | 수집기 | 최근 종료 워커(완료 보고) |
| 〃 | `sources.gh` → `repos[]`·`open`·`ciFail` 또는 `off:true` | 수집기 | `PANEL_GH_REPOS` 가 있을 때만 |
| 〃 | `sources.idle` → `terminals`·`workers`(상태별 건수·`leftover`·`items` 20개)·`workspaces`(`total`·`noTerminal`·`items` 20개·`more`)·`disk`(`freeGB`·`usedPct`·`minGB`·`warn`)·`wsSize` | 수집기 | 부분마다 따로 실패. `err` 는 `부분:종류` 목록 |
| 〃 | 원료마다 `ok`·`err`·`at`·`tried`·`stale` | 수집기 | 실패하면 `ok:false`·`err`(종류만)·`stale:true` 이고 `value`·`at` 은 마지막 성공 값 그대로 |
| `~/.config/lrm-hubs/panel.env` | `PANEL_GH_REPOS`·`PANEL_GH_INTERVAL`·`PANEL_HUB_ROOT`·`PANEL_RECENT`·`PANEL_DISK_MIN_GB`·`PANEL_WS_DIR`·`PANEL_DU_INTERVAL` | 사용자(선택) | 시크릿 아님. 양식 `panel.env.example`. 환경 변수가 우선 |
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
| 2 | `esc to interrupt`, 또는 회전 기호(✻·✢ 등)로 시작하고 `…` 가 있는 줄 | 작업 중 |
| 3 | 화면 끝에 빈 입력 줄(또는 쓰던 글) | 대기 |
| 4 | `patrol.sh` 의 막힘 키워드(`do you want`·`allow`·`승인`·`(y/n)`·`❯ 1.`) | 승인창 |
| 5 | 출력이 2분 넘게 없음 | 대기 |
| 6 | 그 밖 | 확인불가 |

4번은 오탐이 있다. `allow`·`승인` 은 평범한 보고 글에도 나온다. 1~3번으로 판별되지 않을 때만 쓰는 이유다.

### 결정 대기

`slack_pending.sh` 와 같은 판정이다. 보낸 메시지 중 최상위(`thread_ts` 없음)이고 제목에 `결정` 이 든 것에서, 반응(`slack-inbox.jsonl` 의 `reaction` → `parent_ts`)이나 `slack-done.txt` 기록이 있는 것을 뺀다. 같은 제목은 한 건이다. `slack_pending.sh` 는 Slack 에 글을 보내고 토큰을 읽어 부를 수 없으므로 같은 식을 수집기에 두고, `tests/panel/run.sh` 가 두 식이 같은지 원본 줄을 대조한다(원본이 바뀌면 시험 실패).

### 오래됨

| 대상 | 기준 시각 | 경고 |
|---|---|---|
| 자동 칸 | 파일 안 `updatedAt`(epoch). 없거나 지금보다 2분 넘게 미래면 파일 수정 시각 | 10분 초과·파일 없음·못 읽음 → 맨 위 빨간 ⚠️ "자동 수집이 N분째 멈춤" |
| 메인 칸 | 파일 수정 시각 | 30분 초과 → 노란 글씨 "메인 칸이 N분 전 갱신됨" |

자동 칸에 파일 안 epoch 를 쓰는 이유: 수집기가 기록하는 순간의 시계라 데이터가 언제 모였는지를 그대로 말한다. 수정 시각은 복사·백업 복원·`touch` 로도 새로워져 낡은 데이터를 새것처럼 보이게 할 수 있다. 메인 칸에 수정 시각을 쓰는 이유: 메인이 글자로 적는 `updatedAt` 은 실제와 42분 어긋난 전례가 있다.

### 쉬는 자원(😴 칸)

쌓이면 디스크·메모리를 먹는 자원을 숫자로 보여 준다. **목록과 숫자만 만들고 정리(삭제·종료)는 하지 않는다.** 수집기 코드에 삭제·종료 명령이 없다는 것을 시험이 고정한다(`rm`·`worktree rm`·`terminal close`·`worker-stop` 등). 수집기가 지우는 것은 자기 임시 파일·잠금 폴더뿐이다.

| 항목 | 원료 | 뜻 |
|---|---|---|
| 터미널 없는 워크스페이스 | `orca worktree list --limit 1000` 와 `orca terminal list` 의 `worktreeId` 대조(메인 체크아웃 제외) | 열린 터미널이 0개인 워크스페이스 수. 오래 쉰 것부터 20개를 이름·`~` 경로·브랜치·쉰 일수·병합 여부와 함께 싣고 나머지는 "외 N개" |
| 병합 여부 | `git -C <경로> merge-base --is-ancestor HEAD <baseRef>` | 기준 ref 가 없거나 git 이 답하지 못하면 모름(null). 목록 20개에만 확인 |
| 종료 뒤 남은 터미널 | `orca orchestration worker-list --full` | 끝났거나(`succeeded`·`failed`·`stopped`) 실패했는데 터미널이 아직 `retained`·`active` 등인 워커. dispatch id·작업 제목·상태만 |
| 디스크 여유 | `df -k ~` | 홈 볼륨 여유 GB·사용률. `PANEL_DISK_MIN_GB`(기본 50) 미만이면 빨간 글씨 |
| 워크스페이스 폴더 크기 | `du -sk <PANEL_WS_DIR>` | 느려서 30분마다만 잰다. 실패하면 직전 값을 두고 경고하지 않는다 |
| 터미널 수 | `orca terminal list` | 전체·에이전트 붙은 수. 프로세스 메모리는 재지 않는다 |

정리는 사람이 한다. 예: 각 허브가 자기 하위 워크스페이스를 정리하고, 규칙은 "병합됨 + 터미널 없음 + `.claude` 보관 뒤 삭제"를 따른다(`worktree-gc` 스킬).

### gh 간격

수집 주기(90초)마다 gh 를 부르면 하루 약 1,000회 GitHub API 를 쓴다. `PANEL_GH_INTERVAL`(기본 300초) 안에서는 부르지 않고 직전 값을 그대로 둔다. 간격은 `.panel-gh.stamp` 수정 시각으로 재며, 실패한 시도도 간격을 지킨다(실패 때 연속 재시도 방지).

## 4. 켜기·끄기·되돌리기

| 할 일 | 명령 |
|---|---|
| 켜기 | `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/ai.lrm.panelcollect.plist` |
| 상태 보기 | `launchctl print gui/$(id -u)/ai.lrm.panelcollect \| grep -E 'state\|last exit'` · `tail -3 ~/.config/lrm-hubs/panel-collect.log` |
| 한 번 돌려 보기(파일 안 씀) | `bash ~/.config/lrm-hubs/panel_collect.sh --print` |
| 끄기 | `launchctl bootout gui/$(id -u)/ai.lrm.panelcollect` (끄면 10분 뒤 패널에 ⚠️ 가 뜬다) |
| 로그인 때도 안 켜지게 | 끈 뒤 `~/Library/LaunchAgents/ai.lrm.panelcollect.plist` 를 다른 폴더로 옮긴다 |
| PR 수 끄기 | `panel.env` 의 `PANEL_GH_REPOS=` 를 비운다 |
| 전체 되돌리기 | 수집기를 끄고 `./install.sh --restore ~/.claude-config-backup/<시각>`. 그 설치가 새로 만든 mod·plist·링크가 빠진다. `panel-auto.json` 은 남아도 되며, 옛 mod 는 읽지 않는다 |

## 5. 라이브 적용 절차(병합 뒤, 코디네이터가 한다)

1. 메인 체크아웃 `~/orca/claude-config` 에서 `./install.sh --dry-run` 으로 바뀔 항목을 본다. 새로 생기는 것: `~/.config/lrm-hubs/panel_collect.{sh,py}`·`panel.env.example`·`docs/panel.md` 링크, `~/Library/LaunchAgents/ai.lrm.panelcollect.plist`, `~/.claude/skills/lrm-board/` 5개 파일.
2. `./install.sh` 를 돌린다. 끝에 `launchd 재적재 필요: … ai.lrm.panelcollect` 줄이 나온다(설치기는 적재하지 않는다).
3. `bash ~/.config/lrm-hubs/panel_collect.sh --print | head -40` 으로 한 번 확인한다. `hubs`·`pending`·`push`·`workers` 의 `ok` 가 `true` 면 된다.
4. 수집기를 켠다: `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/ai.lrm.panelcollect.plist`. 90초 안에 `~/.config/lrm-hubs/panel-auto.json` 이 생긴다.
5. PR 수가 필요하면 `cp ~/.config/lrm-hubs/panel.env.example ~/.config/lrm-hubs/panel.env` 뒤 `PANEL_GH_REPOS=` 를 채운다. 다음 수집부터 반영된다.
6. 메인 세션을 새로 시작한다. 지금 메인 세션은 세션 전용 mod 폴더(dev-mods)에서 옛 `lrm-board` 를 읽고 있어, 그대로 두면 같은 이름의 mod 가 둘이 된다. 새 세션은 `~/.claude/skills/lrm-board` 만 읽는다. 새 세션에서 `/lrm-board` 를 열어 맨 위에 ⚠️ 가 없는지 본다.
7. 옛 사본 `~/.config/lrm-hubs/mods/lrm-board` 는 더 쓰지 않는다. 헷갈리면 다른 이름으로 옮겨 둔다(설치기는 지우지 않는다).

### 안 보일 때

새 세션에서 `/lrm-board` 명령이 없으면 Claude Code 가 `~/.claude/skills/lrm-board` 를 플러그인으로 읽지 않은 것이다. 먼저 `claude plugin validate ~/.claude/skills/lrm-board` 로 형식을 본다. 형식이 맞는데도 안 읽히면 사용자 설정 `~/.claude/settings.json` 의 `env` 에 아래 한 줄을 넣는 방법이 있다(사용자 승인 뒤, 설정 파일은 사람이 고친다).

```json
"CLAUDE_CODE_PLUGIN_DIRS": "~/.claude/skills/lrm-board"
```

이 변수는 `--plugin-dir` 과 같게 그 폴더를 매 세션 읽게 한다. 이 경우 저장소 `claude/settings.base.json` 의 `env` 에도 같은 줄을 넣어야 다음 설치 때 사라지지 않는다.

## 6. 메인 코디네이터가 할 일

- `panel.json` 에는 손 칸만 쓴다: `headline`·`approvals`·`todos`·`history`. 결정 카드를 보내거나 답을 받았을 때 고친다.
- `hubs`·`updatedAt` 은 더 쓰지 않아도 된다. 허브 상태는 수집기가 채운다.
- `panel-auto.json` 은 쓰지 않는다. 자동 칸이 틀리면 수집기 규칙을 고친다.
- 패널 맨 위 ⚠️ 는 수집기가 멈췄다는 뜻이다. `tail ~/.config/lrm-hubs/panel-collect.log` 와 `launchctl print gui/$(id -u)/ai.lrm.panelcollect` 를 본다.

## 7. 알려진 한계

- 화면 분류는 Orca 가 주는 화면 끝 300자만 본다. 위쪽에 있는 질문은 못 보고, `allow`·`승인` 키워드는 평범한 글에도 걸린다. 확인불가가 나오면 허브 화면을 직접 본다.
- 결정 대기는 `결정` 제목으로 Slack 에 나간 카드만 센다. 허브 화면에만 있는 결정은 못 센다.
- 최근 종료 작업 시각은 Orca 의 `releaseCompletedAt`(터미널 정리 시각, UTC 로 읽음)이다. 작업 완료 시각과 몇 분 차이가 날 수 있다.
- 워크스페이스 목록은 Orca 가 관리하는 것만 센다. Orca 밖에서 만든 git worktree·폴더는 빠진다. 활동 시각(`lastActivityAt`)이 0 이면 만든 시각을 쓰고, 둘 다 없으면 목록 뒤로 보낸다.
- 작업 제목은 `task-list --run <Run>` 로 얻는다. 최근 기록의 Run 이 셋보다 많으면 나머지는 제목 없이 상태만 나온다.
- 실제 launchd 적재와 패널 화면 렌더는 이 브랜치에서 확인하지 않았다(라이브 적용 금지). 검증은 샌드박스 시험과 `claude plugin validate` 까지다.
- mod 의 타입 검사(`tsc`)는 이 맥에 `tsc` 가 없어 하지 못했다. 판정 함수는 node 시험으로, 화면 코드는 `claude plugin validate` 로만 확인했다.

## 8. 후속 제안

- Slack 할 일 목록(리스트) 읽기: 봇 토큰 스코프(`lists:read` 등) 확인이 먼저다. 수집기는 지금 토큰을 읽지 않으므로, 읽게 하려면 토큰을 다루는 별도 단계(폴러 쪽)에 붙이는 편이 맞다.
- 프로세스별 메모리(에이전트 세션 RSS 합계)를 쉬는 자원 칸에 더하기. `ps` 로 잴 수 있으나 이번 범위 밖이다.
- 허브별 `decisions.tsv` 의 오늘 줄 수를 자동 칸에 더하면 `catch-up` 없이도 "사람 없이 내린 결정" 수를 볼 수 있다.

## 참고

- 수집기: `hubs/scripts/panel_collect.sh`(진입)·`panel_collect.py`(본체), 설정 양식 `hubs/panel.env.example`
- launchd: `hubs/launchd/ai.lrm.panelcollect.plist`
- mod: `hubs/mods/lrm-board/hooks/register.tsx`(화면)·`board.ts`(판정 함수)·`types/index.d.ts`
- 시험: `bash tests/panel/run.sh`, 변이 `bash tests/panel/mutate.sh`, node 시험 `node --test tests/panel/board.test.mjs`
- 조사 사실표: `~/Documents/90_정리기록/2026-11-10-소통개선조사/facts-panel.md`
