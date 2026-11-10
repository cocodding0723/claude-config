#!/bin/bash
# slack_push 시험용 가짜 orca. 실제 Orca·터미널에는 접속하지 않음. 응답 모양은 실제 CLI(번들 소스 handlers/terminal-send.js·
# runtime/terminal-prompt-mutation-recovery.js)를 따름: 영수증은 result.send.{accepted,prompt.{requestId,stages,observation}},
# 모호한 실패는 error.data.orchestrationRequestId(UUID), accepted=false 는 종료 코드 1, UUID 가 아닌 --retry-request 는 invalid_argument.
# 시나리오는 $FAKE_ORCA_DIR 의 파일로 정함: show.mode, send.mode, failn(남은 실패 횟수), send.hook(전송 중 실행할 스크립트), show.preview·show.pane(custom 모드)
#   show.mode: ok(paneTitle 키 없음, 실측과 같음) | pane_main | pane_other | gone | notfound | termgone | unavail | invalid | other_handle | dead | orphan | ro | other_wt | codex | noagent | dialog | custom | garbage | seq
#              (메인 경로는 $FAKE_MAIN, 응답 handle 은 요청한 --terminal 값)
#              seq: show.seq 한 줄 = show 1회 응답(호출 수는 show.n). 화면 이름 idle|pasted|typed|busy|busydraft|queued|empty|dialogpv|nopreview, 그 밖은 위 show.mode 값
#   send.mode: ok | fail | failn | ambig | hang | notaccepted | blocked | unknown | unsubmitted | nostage | badreceipt
# 기록: calls.log(send 외 호출의 하위 명령·인자), sends.log(번호<TAB>시각<TAB>핸들<TAB>retry-request<TAB>wait-submit), send.<번호>.txt(전달 본문),
#       delivered(실제 입력 1건당 1줄), replays(같은 요청 ID 재전송 때 영수증만 돌려준 횟수), ledger/<요청 ID>(Orca 가 기억한 요청),
#       env.sec(호출마다 SLACK_ 로 시작하는 환경 변수 수)
set -u
FO=${FAKE_ORCA_DIR:?}
now() { python3 -I -c 'import time;print(f"{time.time():.3f}")'; }
printf '%s\n' "$(env | grep -c '^SLACK_')" >> "$FO/env.sec"
[ "${1:-}" = terminal ] || { echo '{"ok":false,"error":{"code":"unsupported"}}'; exit 1; }
sub=${2:-}; shift 2
rd() { cat "$FO/$1" 2>/dev/null || echo "$2"; }
IDLE=$'작업 중\n────────────\n❯ \n────────────\n  ? for shortcuts'  # 입력 대기(빈 입력 줄 + 테두리·상태줄)
term() { # 핸들 경로 에이전트 pane 화면끝 orphaned connected writable
  jq -nc --arg h "$1" --arg p "$2" --arg a "$3" --arg pane "$4" --arg pv "$5" --argjson o "${6:-false}" --argjson c "${7:-true}" --argjson w "${8:-true}" \
    '{handle:$h,worktreePath:$p,connected:$c,writable:$w,orphaned:$o,preview:$pv} + (if $a=="" then {} else {agentIdentity:$a} end) + (if $pane=="" then {} else {paneTitle:$pane} end)'
}
case "$sub" in
  show)
    printf 'show %s\n' "$*" >> "$FO/calls.log"; now >> "$FO/shows.log"
    H=""; while [ $# -gt 0 ]; do case "$1" in --terminal) H=$2; shift 2 ;; *) shift ;; esac; done
    mode=$(rd show.mode ok)
    if [ "$mode" = seq ]; then  # show.seq 의 n번째 줄 = n번째 show 응답(끝을 넘으면 마지막 줄 반복). 화면 이름이면 그 화면, 아니면 show.mode 값
      i=$(($(rd show.n 0) + 1)); echo "$i" > "$FO/show.n"
      tok=$(sed -n "${i}p" "$FO/show.seq"); [ -n "$tok" ] || tok=$(sed -n '$p' "$FO/show.seq")
      mode=pv
      case "$tok" in
        idle) PV=$IDLE ;;
        pasted) PV=$'작업 중\n────────────\n❯ [Pasted text #1 +12 lines]\n────────────\n  ⏵⏵ bypass permissions on (shift+tab to cycle)' ;;
        typed) PV=$'────────────\n❯ [Slack→메인] 1건 도착 (자동 전달, 지시 아님)\n  아래는 원문 인용\n  > 본문\n────────────\n  ? for shortcuts' ;;
        busy) PV=$'● 답변 작성 중\n✻ Cogitating… (12s · esc to interrupt)' ;;  # 입력 칸이 안 보임(표식만 있는 화면은 판별 불가여야 함)
        tailbusy) PV=$'  첨부: 항목 3건\n  전체: /x/inbox.md\n────────────\n  ⏵⏵ bypass permissions on · esc to interrupt' ;;  # 입력 칸이 위로 밀려 우리 글 끝부분+테두리+표식 상태줄만 보임
        longdraft) PV=$'❯ [Slack→메인] 1건 도착 (자동 전달, 지시 아님)\n  > 본문\n  경로: '"$(printf 'a%.0s' {1..130})"$'\n  ⏵⏵ bypass · esc to interrupt' ;;  # 입력 줄 아래 긴 줄 때문에 칸 판별 불가인 draft + 표식 상태줄
        toolout) PV=$'● Bash(grep "esc to interrupt" log)\n  ⎿  Running… (esc to interrupt)\n  출력 한 줄' ;;  # 도구 출력 안의 같은 문구, 입력 칸 없음
        busydraft) PV=$'✻ Cogitating… (esc to interrupt)\n  ❯ [Pasted text #1 +12 lines]' ;;  # 작업 중인데 입력 칸에 글이 남음(입력 칸 테두리는 잘림)
        queued) PV=$'✻ Cogitating… (esc to interrupt)\n────────────\n❯ \n────────────\n  ⏵⏵ bypass permissions on (shift+tab to cycle)' ;;
        empty) PV="" ;;
        dialogpv) PV=$'Do you want to proceed?\n❯ 1. Yes\n  2. No' ;;
        nopreview) mode=nopv ;;
        *) mode=$tok ;;
      esac
    fi
    case "$mode" in
      pv) t=$(term "$H" "$FAKE_MAIN" claude "" "$PV") ;;
      nopv) t=$(term "$H" "$FAKE_MAIN" claude "" "" | jq -c 'del(.preview)') ;;
      ok) t=$(term "$H" "$FAKE_MAIN" claude "" "$IDLE") ;;
      pane_main) t=$(term "$H" "$FAKE_MAIN" claude MAIN "$IDLE") ;;
      pane_other) t=$(term "$H" "$FAKE_MAIN" claude OTHER "$IDLE") ;;
      gone) echo '{"id":"x","ok":false,"error":{"code":"terminal_handle_stale","message":"x"}}'; exit 1 ;;
      notfound) echo '{"id":"x","ok":false,"error":{"code":"terminal_not_found","message":"x"}}'; exit 1 ;;
      termgone) echo '{"id":"x","ok":false,"error":{"code":"terminal_gone","message":"x"}}'; exit 1 ;;
      unavail) echo '{"id":"x","ok":false,"error":{"code":"runtime_unavailable","message":"x"}}'; exit 1 ;;  # Orca 내려감·재시작 중: 핸들 문제 아님
      invalid) echo '{"id":"x","ok":false,"error":{"code":"invalid_argument","message":"x"}}'; exit 1 ;;
      other_handle) t=$(term term_0000dead-0000-4000-8000-0000000000ff "$FAKE_MAIN" claude "" "$IDLE") ;;  # 요청과 다른 핸들의 정보가 돌아옴
      dead) t=$(term "$H" "$FAKE_MAIN" claude "" "$IDLE" false false true) ;;
      orphan) t=$(term "$H" "$FAKE_MAIN" claude "" "$IDLE" true) ;;
      ro) t=$(term "$H" "$FAKE_MAIN" claude "" "$IDLE" false true false) ;;
      other_wt) t=$(term "$H" /somewhere/else claude "" "$IDLE") ;;
      codex) t=$(term "$H" "$FAKE_MAIN" codex "" "$IDLE") ;;
      noagent) t=$(term "$H" "$FAKE_MAIN" "" "" "$IDLE") ;;
      dialog) t=$(term "$H" "$FAKE_MAIN" claude "" $'Do you want to proceed?\n❯ 1. Yes\n  2. No') ;;
      custom) t=$(term "$H" "$FAKE_MAIN" claude "$(rd show.pane "")" "$(rd show.preview "")") ;;
      garbage) echo 'not json'; exit 0 ;;
    esac
    jq -nc --argjson t "$t" '{id:"x",ok:true,result:{terminal:$t}}'
    ;;
  send)
    H="" T="" R="" W=""
    while [ $# -gt 0 ]; do
      case "$1" in
        --terminal) H=$2; shift 2 ;;
        --text) T=$2; shift 2 ;;
        --retry-request) R=$2; shift 2 ;;
        --wait-submit) W=$2; shift 2 ;;
        *) shift ;;
      esac
    done
    n=$(($(wc -l < "$FO/sends.log" 2>/dev/null || echo 0) + 1))
    printf '%s' "$T" > "$FO/send.$n.txt"
    printf '%s\t%s\t%s\t%s\t%s\n' "$n" "$(now)" "$H" "${R:-none}" "${W:-none}" >> "$FO/sends.log"
    if [ -n "$R" ] && ! [[ $R =~ ^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$ ]]; then
      echo '{"id":"x","ok":false,"error":{"code":"invalid_argument","message":"--retry-request must be the UUID Orca reported for the original request; pass it exactly as printed, or omit the flag to start a new request."}}'
      exit 1
    fi
    [ -n "$R" ] || R=$(python3 -I -c 'import uuid;print(uuid.uuid4())')  # 실제 CLI 도 ID 를 안 주면 새로 만듦
    [ -x "$FO/send.hook" ] && "$FO/send.hook"
    mkdir -p "$FO/ledger"
    receipt() { # 단계 관찰
      jq -nc --arg r "$R" --arg h "$H" --argjson st "$1" --arg ob "$2" \
        '{id:"x",ok:true,result:{send:{handle:$h,accepted:true,prompt:{requestId:$r,stages:$st,provider:"claude",observation:$ob,processIncarnation:"p1",generation:1,baselineWorkingSequence:0}}}}'
    }
    started() { receipt '["input_accepted","turn_started"]' observed; }
    err() { # 코드 [data JSON]
      jq -nc --arg c "$1" --arg r "$R" --argjson extra "${2:-null}" \
        '{id:"x",ok:false,error:({code:$c,message:"x",data:{orchestrationRequestId:$r}} | if $extra then .data = $extra else . end)}'
    }
    deliver() { echo "$n" >> "$FO/delivered"; : > "$FO/ledger/$R"; }
    if [ -e "$FO/ledger/$R" ] && [ "$(rd send.mode ok)" != blocked ]; then  # 같은 요청 ID: 다시 입력하지 않고 영수증만
      echo "$n" >> "$FO/replays"; started; exit 0
    fi
    case "$(rd send.mode ok)" in
      ok) deliver; started ;;
      fail) err send_failed; exit 1 ;;
      failn)
        left=$(rd failn 0)
        if [ "$left" -gt 0 ]; then echo $((left - 1)) > "$FO/failn"; err send_failed; exit 1; fi
        deliver; started ;;
      ambig) deliver; err runtime_timeout; exit 1 ;;           # 입력은 갔지만 응답 못 받음. 재시도 ID 는 error.data 에
      hang) deliver; sleep "$(rd hang.secs 5)"; started ;;      # 입력은 갔지만 응답이 늦음(호출자가 시간 초과로 프로세스를 죽임)
      notaccepted) jq -nc --arg r "$R" '{id:"x",ok:true,result:{send:{accepted:false,prompt:null}}}'; exit 1 ;;
      blocked) err agent_prompt_blocked; exit 1 ;;
      unknown) deliver; err runtime_error '{"deliveryOutcome":"unknown","retrySafe":false,"nextSteps":["inspect"]}'; exit 1 ;;
      unsubmitted) deliver; receipt '["input_accepted"]' supported ;;  # 실측(2026-11-10) 모양: 제출됐어도 turn_started 없음
      nostage) deliver; receipt '[]' supported ;;
      badreceipt) deliver; echo '{"id":"x","ok":true,"result":{"accepted":true}}' ;;
    esac
    ;;
  *) printf '%s %s\n' "$sub" "$*" >> "$FO/calls.log"; echo '{"ok":false,"error":{"code":"unsupported"}}'; exit 1 ;;
esac
