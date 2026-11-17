# GitHub PR·CI 감시기(gh-watch) 런북

GitHub 의 새 PR·병합·내 PR CI 결과를 폴링으로 받아 이벤트 파일과 요약 파일로 남긴다. 작성일은 2026-11-17 이다.

## 쉬운 말 3줄 요약

- 무엇: 상주 프로그램 하나가 1분마다(내 PR CI 가 도는 중이면 20초마다) GitHub 를 확인하고, 새 PR·병합·CI 결과를 `gh-events.jsonl` 에 한 줄씩, 지금 상태를 `gh-state.json` 에 쓴다.
- 왜: 웹훅은 저장소 관리자 권한이 필요하고 `gh webhook forward` 는 운영용이 아니다. 조건부 요청(ETag)이라 바뀐 게 없으면 GitHub 호출 한도를 쓰지 않는다.
- 할 일: 설치 뒤 `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/ai.lrm.ghwatch.plist` 한 번. 병합·push 직후 바로 반영하려면 `~/.config/lrm-hubs/gh-watch kick`.

## 1. 구성

```text
 GitHub REST(읽기만)                        감시기(상주 1개)                       출력
 ─────────────────────────────             ─────────────────────────             ───────────────────────────────
 GET pulls?state=all&sort=updated ──┐       gh_watch.py run                       gh-events.jsonl (이벤트, 추가만)
   (ETag 조건부, 304 = 무료)          ├────▶  잠금 gh-watch.lock          ──────▶  gh-state.json   (요약, 원자 교체)
 GET actions/runs?event=pull_request ┘       상태 gh-watch.state.json               ├ 작업판 카드 코멘트(bd comment)
   (ETag 조건부)                              kick = SIGUSR1 로 즉시 회차            ├ 후속 명령(gh-watch-actions.json, 기본 꺼짐)
 + 필요할 때만: user(하루 1회)·pulls/N(병합자)·runs/N/jobs(실패 잡)·runs/N(밀려난 진행 중 실행)   └ 데스크톱 알림(osascript)
```

쓰지 않는 것: 웹훅·GraphQL·검색 API·events API·notifications API. 쓰기 요청은 없다.

## 2. 명령

| 명령 | 하는 일 | 종료 코드 |
|---|---|---|
| `gh-watch run` | 상주 루프. launchd `ai.lrm.ghwatch` 가 실행 | — |
| `gh-watch once` | 한 회차. 결과 JSON 한 줄 | 0 정상 / 1 gh 오류 / 4 상주가 잠금 보유 |
| `gh-watch kick` | 상주가 있으면 즉시 깨움(`mode: signal`), 없으면 직접 한 회차(`mode: once`) | 0 / 1 |
| `gh-watch status` | `gh-state.json` 출력 | 0 / 1 파일 없음 |

진입점은 `~/.config/lrm-hubs/gh-watch`(bash, 3.10 이상 python 선택). `kick` 은 pid 파일의 명령줄이 `gh_watch.py` 인지 확인한 뒤에만 신호를 보낸다.

## 3. 간격과 따라잡기

- 평소 60초, 내 PR 실행이 `queued`·`in_progress` 등이면 20초. 연속 오류면 120→240→480→600초. 남은 호출이 200 미만이면 300초.
- 조절값은 `gh_watch.py` 의 `DEFAULTS` 한 곳. 바꾸려면 `~/.config/lrm-hubs/gh-watch.env`(양식 `gh-watch.env.example`) 또는 같은 이름 환경 변수.
- 저장소 목록은 `panel.env` 의 `PANEL_GH_REPOS` 를 그대로 쓴다. 비면 `Basakansoft/LoveRevolutionMaker`.
- 잠·끊김: 상태 파일에 마지막으로 본 PR `updated_at`·실행 id 를 남긴다. 깨어나면 1쪽부터 커서에 닿을 때까지 최대 5쪽을 읽어 한 번에 처리한다. 1쪽 밖으로 밀려난 진행 중 실행은 개별 조회(최대 10개).
- 중복 방지: 처리한 이벤트 id 를 상태 파일에 3000개까지 기록한다. 첫 실행은 기준만 기록하고 이벤트를 내지 않는다.
- 중간 실패: ETag·커서는 처리가 끝난 뒤 저장한다. 실패한 회차의 나머지는 다음 회차가 다시 받는다.

## 4. 출력 계약

### 4.1 `~/.config/lrm-hubs/gh-events.jsonl`

한 줄에 이벤트 하나. 2MB 를 넘으면 `.1` 로 돌린다. 이벤트를 쓴 뒤 상태를 저장하므로 회차 중간에 죽으면 같은 이벤트가 한 번 더 쓰일 수 있다. 읽는 쪽은 `id` 로 거른다.

| 키 | 값 |
|---|---|
| `id` | `opened:<저장소>#<번호>` · `merged:<저장소>#<번호>` · `ci:<저장소>:<실행 id>:<시도 번호>` |
| `ts` | 기록 시각(UTC ISO) |
| `kind` | `pr_opened` · `pr_merged` · `ci_done` |
| `repo` · `pr` · `title`(40자) · `branch` · `url` | 저장소·PR 번호·제목·머리 브랜치·링크(`ci_done` 은 실행 링크) |
| `result` | `opened` · `merged` · `success` · `failure` · `cancelled` (`timed_out`·`startup_failure` 는 `failure`, `skipped`·`neutral` 은 이벤트 없음) |
| `mine` | 내가 올린 PR 인가 (`ci_done` 은 항상 true, 내 PR 만 냄) |
| `author` | PR 작성자 (`pr_opened`·`pr_merged`) |
| `by` · `by_me` | 병합한 사람·내가 병합했나 (`pr_merged`) |
| `workflow` · `jobs` | 워크플로 이름·실패한 잡 이름 목록 (`ci_done`) |

### 4.2 `~/.config/lrm-hubs/gh-state.json` (`schema: 1`)

매 회차 원자 교체. 패널 수집기가 읽는 계약이다.

```json
{
  "schema": 1, "updated_at": "2026-11-17T00:45:47Z", "ok": true, "error": null,
  "me": "<로그인>", "repos": ["owner/repo"], "interval": 60, "busy": false,
  "next_poll_at": "2026-11-17T00:46:47Z",
  "my_open_prs": [
    {"repo": "owner/repo", "number": 12, "title": "40자", "branch": "feat/x", "draft": false,
     "url": "…", "updated_at": "…",
     "ci": {"state": "pending|failure|cancelled|success|none", "failed_jobs": ["잡 이름"],
            "runs": [{"workflow": "PR Test Pipeline", "state": "success", "url": "…"}]}}
  ],
  "recent_merges": [
    {"repo": "owner/repo", "number": 11, "title": "…", "branch": "…", "merged_at": "…",
     "merged_by": "<로그인>", "author": "<로그인>", "mine": true, "url": "…"}
  ],
  "rate": {"remaining": 4907, "used": 93, "limit": 5000, "reset": 1794877004}
}
```

- `ok: false` 면 `error` 에 원인 코드(`net` · `timeout` · `http-<상태>` · `json` · `shape` · `no-gh`). 값은 직전 성공 회차 기준이다.
- `ci.state` 는 PR 머리 커밋의 워크플로별 최신 실행을 합친 값이다. 우선순위 `pending` > `failure` > `cancelled` > `success`.
- `recent_merges` 는 최근 10개(새것 먼저). 판정이 낡았는지는 `updated_at` 과 `interval` 로 본다.

## 5. 작업판 연결

`pr_opened`·`pr_merged`·`ci_done`(실패·취소) 이벤트의 브랜치가 카드와 맞으면 `bd comment` 로 한 줄 단다(작성자 `gh-watch`).

- 맞는 카드: label `branch:<브랜치>`, 또는 설명·메모에 `branch: <브랜치>` / `브랜치: <브랜치>`(정확히 일치, `feat/x-2` 는 `feat/x` 와 다름).
- 맞는 카드가 없으면 건너뛴다. 작업판 폴더는 `BOARD_DIR`(기본 `~/orca/board`).
- 예: `[gh-watch] PR #883 병합됨(병합 <로그인>) · sprint-…/fix-x https://…`

## 6. 후속 명령(기본 꺼짐)

`~/.config/lrm-hubs/gh-watch-actions.json` 이 없으면 아무것도 실행하지 않는다. 규칙은 `enabled: true` 인 것만 돈다. 셸을 거치지 않고 `cmd` argv 그대로 실행하며 시간 상한 60초다.

- `on`: `pr_opened` · `pr_merged` · `ci_done` 또는 `ci_done:failure` 처럼 결과까지.
- 선택 조건: `mine` · `by_me` · `repo`.
- 환경 변수: `GH_WATCH_EVENT`(이벤트 JSON) · `GH_WATCH_KIND` · `GH_WATCH_REPO` · `GH_WATCH_PR` · `GH_WATCH_BRANCH` · `GH_WATCH_RESULT` · `GH_WATCH_URL`.

예시(`gh-watch-actions.example.json`, 켜지 않음). 자동 빌드·자동 조사 카드는 사용자가 켤 때까지 꺼 둔다.

```json
{"actions": [
  {"on": "ci_done:failure", "mine": true, "enabled": false,
   "cmd": ["/bin/sh", "-c", "bd create \"CI 실패 조사: PR #$GH_WATCH_PR\" -d \"$GH_WATCH_URL\""]},
  {"on": "pr_merged", "repo": "Basakansoft/LoveRevolutionMaker", "enabled": false,
   "cmd": ["/bin/echo", "병합 뒤 빌드 자리"]}
]}
```

## 7. 데스크톱 알림

- 내 PR CI 실패, 내가 병합하지 않은 병합만. 같은 이벤트는 1회.
- 시간당 상한 `GH_WATCH_NOTIFY_PER_HOUR`(기본 6). 보낸 시각을 상태 파일에 남겨 재시작해도 상한이 유지된다. 넘은 건은 로그에 `capped`.
- `osascript display notification` 만 쓴다. `open`·`activate` 는 쓰지 않는다(포커스를 뺏지 않음).

## 8. 호출 비용(2026-11-17 실측)

| 회차 | 호출 | `rate_limit core.used` 변화 |
|---|---|---|
| 첫 실행(기준 기록, 최근 병합 10개 병합자 조회 포함) | 13 | 80 → 93 (+13) |
| 변경 없음 | 2 (둘 다 304) | 93 → 93 (+0) |

평소 시간당 최대 120회(모두 200 일 때), 진행 중 20초 간격이면 360회. 한도 5000/시간.

## 9. 켜기·끄기·되돌리기

```bash
cd ~/orca/claude-config && ./install.sh && ./install.sh --check
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/ai.lrm.ghwatch.plist   # 켜기
launchctl bootout gui/$(id -u)/ai.lrm.ghwatch                                   # 끄기
~/.config/lrm-hubs/gh-watch status                                              # 확인
```

로그는 `gh-watch.log`(회차 결과 코드만, 256KB 에서 돌림)와 `gh-watch.launchd.log`(비정상 종료 출력). 토큰·헤더는 쓰지 않는다. 상태를 처음부터 다시 잡으려면 감시기를 끄고 `gh-watch.state.json` 을 지운 뒤 켠다(첫 실행처럼 기준만 기록).

## 10. 이어 붙일 곳

- 패널 수집기(`panel_collect.py`): 지금은 `gh pr list` 를 직접 부른다. `gh-state.json` 의 `my_open_prs`·`recent_merges`·`ok`·`updated_at` 을 읽으면 gh 호출을 없앨 수 있다.
- `board-merge`·push 직후: `~/.config/lrm-hubs/gh-watch kick` 을 부르면 다음 폴링을 기다리지 않는다. 실패해도 무시해도 된다(다음 폴링이 잡음).
- 다른 출처의 이벤트도 같은 처리기를 쓸 수 있다: `gh_watch_sinks.dispatch(events, state, conf, actions_path, log)`.
