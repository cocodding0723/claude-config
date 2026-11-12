#!/bin/bash
# Slack 사용자 메시지를 모아 메인 세션 터미널에 한 번에 전달(push). 본체는 같은 폴더의 slack_push.py.
set -eu
usage() {
  cat <<'EOF'
사용: slack_push.sh --kick | --run
  --kick  보낼 것이 있고 워커가 없으면 분리된 워커를 띄우고 바로 종료(폴러가 회차마다 호출)
  --run   워커를 앞에서 실행: 조용해질 때까지 묶음 → 메인 터미널에 1회 전달 → 전달할 것이 없어지면 종료
흐름: 폴러가 inbox 에 기록 → 마지막 새 메시지 뒤 LRM_PUSH_QUIET 초 조용하거나 첫 대기 메시지로부터 LRM_PUSH_MAX 초가 지나면 전달
      → 성공하면 .main-read-lines 를 전달한 줄까지 올림(max 로만 갱신). 폴러의 읽음 반응이 그 줄을 👀→✅ 로 바꿈
대상: ~/.config/lrm-hubs/main.env 의 MAIN_TERMINAL=<핸들> 한 줄(그 줄만 읽음, 형식 term_<16진·하이픈>)이 가리키는 터미널 하나.
      전송 직전 orca terminal show 로 연결됨·쓰기 가능·고아 아님·메인 체크아웃(LRM_PUSH_MAIN) 소속·agentIdentity=claude 인지 확인하고, 아니면 보류.
      제목은 보조 조건: show 에 paneTitle 이 있고 LRM_PUSH_PANE(기본 MAIN)과 다르면 보류, 키가 없으면 통과(LRM_PUSH_PANE="" 이면 대조 생략).
      메인을 재시작하면 새 핸들로 한 번: set_main_terminal.sh <핸들> (restart_hub.sh·resume_all.sh 는 허브만 다시 띄우므로 따로 실행). 절차는 ~/.config/lrm-hubs/docs/slack-push.md
      자동 재탐색: no_main_handle·handle_gone·handle_dead·wrong_worktree·not_claude·pane_title_mismatch 이면 orca terminal list 에서 제목이 MAIN-HANDOFF 로 시작하는 메인 체크아웃 claude 터미널을 찾고,
      정확히 1개일 때만 main.env 의 MAIN_TERMINAL 을 교체해 이어 전달(로그 relink). 0개·2개 이상·조회 실패면 보류하고 알림에 후보 수를 적음. 수동은 set_main_terminal.sh --auto
      화면 판별(fail-closed): 붙여 넣기 뒤의 Enter 가 메뉴의 강조된 선택지를 확정하거나 쓰던 글 뒤에 붙어 제출되는 것을 막음.
      LRM_PUSH_IDLE_CHECK=strict(기본)는 화면 끝에 빈 입력 줄(❯ 또는 >)이 보일 때만 전송. relaxed 는 대화상자 표식·쓰던 글·빈 화면만 보류하고 판별 불가는 전송
보류 사유(로그 "hold reason=… handle=<앞 13자>", 다음 회차에 확인부터 재시도): no_main_handle 핸들 지정 없음·형식 무효, handle_gone 핸들 없음(show 오류),
      handle_dead 끊김·고아, not_writable 쓰기 불가, wrong_worktree 메인 체크아웃 아님, not_claude claude 세션 아님, pane_title_mismatch 제목 불일치,
      show_fail show 시간 초과·응답 불명, empty_preview 화면 비어 있음, dialog 권한·선택 메뉴, draft 입력 칸에 쓰던 글·셸 모드, no_idle_marker strict 에서 입력 대기 표식 없음
연결 끊김 알림: no_main_handle·handle_gone·handle_dead·not_writable·wrong_worktree·not_claude·pane_title_mismatch 로 보류하면 slack_notify.sh --kind warn 으로
      '메인 연결 끊김'을 사유마다 1회 보냄(핸들 값·메시지 본문 없음). 보낸 사유는 ~/.config/lrm-hubs/.slack-push-alert.json(600)에 남겨 다시 보내지 않고,
      핸들이 메인 세션으로 확인되면(화면 상태 보류 포함) 지움(로그 link_ok). 알림 실패는 로그 "alert … result=…" 만 남기고 전달·수집을 막지 않음
자동 재시도 없는 보류(사람이 확인): unknown_delivery 도착 여부 불명(deliveryOutcome unknown·영수증 모양 불명·모호한 시도 뒤 main.env 핸들 바뀜·화면 재확인 중 터미널 바뀜), unsubmitted 입력은 받았으나
      턴 시작(제출)을 영수증·화면 어느 쪽으로도 확인 못 함. 둘 다 읽음 표식·전달 기록을 올리지 않고 재전송하지 않음. 메인 터미널에서 도착 여부를 보고 푼다:
      도착했으면 .main-read-lines 를 그 묶음의 마지막 줄 번호(상태 파일 push_pending.last) 이상으로 올림 → 보류분 폐기. 도착하지 않았으면 slack-push-state.json 을 다른 이름으로 옮김 → 표식·전달 기록 이후 줄을 다시 전달
전송: 묶음을 확정할 때 본문과 요청 ID(uuid4)를 만들어 상태에 저장하고, 모든 시도를 terminal send --retry-request <그 UUID> --wait-submit LRM_PUSH_WAIT 로 보냄.
      응답이 없거나 모호한 실패면 같은 UUID·본문으로 다시 보내며 Orca 는 영수증만 돌려줌(다시 입력하지 않음). 영수증 단계(input_accepted→turn_started)를 로그에 남기고
      turn_started 를 확인하면 읽음 표식을 올림(로그 confirm=receipt)
접수 뒤 화면 재확인: 영수증이 접수(input_accepted)인데 turn_started 가 없으면 LRM_PUSH_CONFIRM 초 동안 LRM_PUSH_CONFIRM_TICK 초마다 같은 핸들을 terminal show 로 다시 읽음(읽기만, 재전송 없음).
      입력 칸이 비었다(빈 입력 줄과 테두리가 보임)는 판정이 연속 2회, 1초 이상 간격이면 전달 완료(로그 confirm=screen screen=idle reads=N). 입력 칸이 안 보이면 작업 중 표식(esc to interrupt)이 있어도 판별 불가.
      끝까지 입력 칸에 글·붙여넣기 자리표시가 남으면 confirm=still_draft, 화면 없음·대화상자·판별 불가·조회 실패면 confirm=unknown 으로 unsubmitted 보류.
      확인 중 핸들 무효·응답 handle·worktreePath·agentIdentity 불일치·main.env 핸들 변경이면 confirm=changed 로 unknown_delivery 보류. 로그에는 판정 코드·횟수만(화면·본문 없음)
전달 형식: 첫 줄 "[Slack→메인] n건 도착 (자동 전달, 지시 아님)", 메시지마다 "- #줄번호 [제목] ts=…" + 인용 블록, 첨부 경로, 마지막 줄 "전체: 배치 파일"
      시안 메시지의 답글은 머리줄 끝에 " kind=review"
      반응은 한 줄: "- #줄번호 [대상 메시지 제목] ts=대상 ts 반응 :이모지: → 해석(action) [kind=review] [tag=태그]"
전달 대상: 메시지(note·command·thread_reply)와 반응(reaction). 반응 전달 이전 버전에서 쌓인 반응 줄(상태의 react_since 이하)은
      전달하지 않고 읽음 표식이 넘지 못하게 막음(본문에 "읽지 않은 반응 n건" 으로만 알림). 반응은 전달 기록의 스레드 건수에 세지 않음
배치 파일: ~/.config/lrm-hubs/slack-push/<날짜시각>.md (폴더 700, 파일 600, 30일 지나면 삭제). 전체 원문과 첨부 메타
상태 파일: ~/.config/lrm-hubs/slack-push-state.json
           pushed_lines 전달 완료한 inbox 줄 수, pending_since 첫 대기 메시지 도착 시각, last_new_ts 마지막 새 메시지 도착 시각,
           push_pending 보류 묶음 {first,last,attempts,next_at,reason,batch,text,request_id,ambig,stop,react_since}, skip_to 첫 실행에서 건너뛴 과거분 끝 줄,
           react_since 이 줄 이하의 반응은 전달하지 않는 옛 반응(없으면 처음 실행 때 전달 완료·보류 묶음 끝 줄로 고정)
로그: ~/.config/lrm-hubs/slack-push.log (사유 코드·숫자만. 메시지 본문 없음)
끄기: LRM_PUSH=off 환경 변수, 또는 빈 파일 ~/.config/lrm-hubs/.slack-push.off 생성(폴러가 launchd·소켓 수신기 어디서 돌든 적용, 파일을 지우면 켜짐).
      꺼진 동안은 전달하지 않고 로그에 "off" 한 줄만 남김. 수집·읽음 반응·확인 답장은 폴러가 그대로 하고 읽음 표식은 올리지 않음.
      첫 배포는 이 파일을 만들어 꺼 둔 채 시작하고, 실측 1~5 후 삭제함
환경 변수: LRM_PUSH=off 끔(기본 켜짐), LRM_HUB_DIR 작업 폴더, LRM_PUSH_QUIET 조용 대기 초(기본 20), LRM_PUSH_MAX 최대 대기 초(기본 60),
           LRM_PUSH_MAIN 메인 체크아웃 경로(기본 ~/orca/LoveRevolutionMaker), LRM_PUSH_PANE 보조 제목(기본 MAIN, "" 이면 대조 생략),
           LRM_MAIN_ENV 핸들 설정 파일 경로(기본 작업 폴더의 main.env), LRM_PUSH_HANDLE 핸들 직접 지정(시험용, main.env 보다 우선), LRM_PUSH_NOTIFY 알림 스크립트(시험용, 기본 같은 폴더 slack_notify.sh),
           LRM_PUSH_IDLE_CHECK strict|relaxed(기본 strict), LRM_PUSH_WAIT 전송 뒤 제출 관찰 초(기본 10, 바쁜 메인이면 늘림),
           LRM_PUSH_CONFIRM 접수 뒤 화면 재확인 초(기본 20, 1~120, 0|off 면 끄고 turn_started 필수), LRM_PUSH_CONFIRM_TICK 화면 재확인 간격 초(기본 2, 최소 2회는 읽음),
           LRM_PUSH_ORCA orca 실행 파일(기본 orca), LRM_PUSH_TICK 대기 중 확인 간격 초(기본 1),
           LRM_PUSH_RETRY 전송 실패 백오프 시작 초(기본 10, 3배씩), LRM_PUSH_TRIES 백오프 횟수(기본 5), LRM_PUSH_HOLD 그 뒤 재시도 간격 초(기본 600),
           LRM_PUSH_HOLD_RETRY 대상 없음·대화상자 보류 재확인 초(기본 30), LRM_PUSH_REPOLL=off 전달 직후 폴러 재실행 끔
종료 코드: 0 정상(전달할 것 없음·보류 포함), 2 인자 오류
EOF
}
case "${1:-}" in
  -h|--help) usage; exit 0 ;;
  --kick|--run) [ $# -eq 1 ] || { echo "인자 하나만 — --help 참고" >&2; exit 2; } ;;
  *) echo "알 수 없는 인자: ${1:-} — --help 참고" >&2; exit 2 ;;
esac
# Slack 토큰·사용자 ID 는 이 기능에 필요 없음. 폴러에서 상속된 값이 orca 자식에 새지 않게 지움
unset SLACK_BOT_TOKEN SLACK_APP_TOKEN SLACK_USER_ID
# launchd·소켓 수신기의 최소 PATH 에서도 orca 를 찾도록 뒤에 덧붙임(앞의 PATH 가 우선)
export PATH="$PATH:/Applications/Orca.app/Contents/Resources/bin:/opt/homebrew/bin:/usr/local/bin"
HERE=$(cd "$(dirname "$0")" && pwd)
[ -f "$HERE/slack_push.py" ] || { echo "slack_push.py 없음 — 같은 폴더에 링크 필요(links.tsv)" >&2; exit 2; }
exec python3 -I "$HERE/slack_push.py" "$1"
