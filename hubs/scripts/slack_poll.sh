#!/bin/bash
# 봇 DM 에서 사용자 응답(최상위 메시지·스레드 답글·반응)을 읽어 slack-inbox.jsonl 에 추가.
export SSL_CERT_FILE="${SSL_CERT_FILE:-/etc/ssl/cert.pem}"  # python.org 파이썬은 시스템 인증서를 못 찾음
# 읽기와 파일 기록만 함. 수신 본문은 데이터로만 다루고 실행·eval 하지 않음.
set -eu
usage() {
  cat <<'EOF'
사용: slack_poll.sh --once [--json] [--days N]
  --once    한 번 수집 후 종료(필수, launchd 가 주기 실행)
  --json    결과 요약을 JSON 한 줄로 출력 {"ok":true,"added":N}
  --days N  목록 조회 기간(기본 7)
예:
  slack_poll.sh --once
  slack_poll.sh --once --json
출력 파일: ~/.config/lrm-hubs/slack-inbox.jsonl (항목: type=reaction|thread_reply|command|note)
           사용자 메시지에 첨부가 있으면 "files":[저장 경로...] 추가(없으면 키 없음)
           내려받기에 실패한 첨부는 "files_failed":[{"id":파일 id,"why":사유 코드}] 추가(없으면 키 없음)
첨부 저장: ~/.config/lrm-hubs/slack-files/<file_id>_<정리한 이름> (폴더 700, 파일 600, 25MB 상한, 봇 파일 제외)
상태 파일: ~/.config/lrm-hubs/slack-poll-state.json (중복 방지, 스레드별 마지막 답글 시각,
           file_share_since = 첨부 메시지 수집을 시작한 시각. 그 이전 첨부 메시지는 기록·내려받기 없이 건너뜀,
           reacted = 읽음 반응 진행 표 {메시지 ts: new|eyes|done|skip}, reacted_on = 반응 확인 진행 표 {봇 메시지 ts: {s,at}},
           reactions_disabled_reason = 반응 권한 부족 사유,
           acked = {스레드 루트 ts: 확인 답장 시각}, ack_sent = 최근 확인 답장 시각 목록,
           ack_msgs = {스레드 루트 ts: {ts,ch,n}} 확인 답장 메시지 ts·채널·누적 전달 건수, upd_pos = 처리한 전달 기록의 마지막 seq,
           upd_part = 처리 중인 기록 {seq,k}(회차 상한으로 중단된 위치))
읽음 반응: 새 사용자 메시지(note·command·thread_reply)에 👀, 메인이 읽음 처리(.main-read-lines 의 줄 수가 그 줄을 지남)하면 ✅ 로 교체.
           사용자가 봇 메시지에 단 반응(이모지)은 반응에 반응을 달 수 없어 그 봇 메시지에 👀, 그 반응 줄이 모두 읽히면 ☑️(ballot_box_with_check)로 교체.
           완료 표식을 ✅ 와 다르게 둬 사용자의 ✅(승인·합격)와 구분함. 끝난 메시지에 새 반응이 오면 다시 👀
           두 표가 회차당 호출 상한(LRM_REACT_MAX)을 함께 쓰고, 30분 넘게 묵은 대기는 달지 않음(반응은 수집 시각 기준)
           reactions:write scope 가 없으면 조용히 건너뛰고 10분마다 재시도(stderr 에 사유·scope 이름 한 줄, 토큰 없음). 수집·전달은 계속
반응 해석: 결정 카드 ✅=approve ❌(x)=hold :비서-a~d:=a~d, 시안(kind=review) ✅=pass ❌=fail :비서-a:=pick_a :비서-b:=pick_b.
           표에 없는 이모지는 이름 그대로. 종류는 slack-sent.jsonl 의 form_kind·kind(text·file 제외) → 없으면 첨부 기록이고 제목에 '결정'이 없을 때 review.
           review 메시지의 반응·답글 레코드에 "kind":"review" 추가(옛 결정 카드는 키 없음)
           번호 카드(form_kind=decide + options): :one:~:four:=pick(pick=N, 선택지 수 안일 때만) ✅=approve(pick=추천 번호) ❌=hold,
           레코드에 kind=decide·decision_id·pick·option_text(문구 앞 80자). 같은 카드 답이 갈리면 conflict:true·choices
           할 일(form_kind=todo): ✅·🛠️(hammer_and_wrench)=done ❌=hold, kind=todo·todo_id. 범위 밖·기록 모양 오류는 이름 그대로(stderr 1줄)
스레드 답글: '채널에도 보내기'를 켠 답글(subtype thread_broadcast)도 thread_reply 로 수집
           번호 카드 스레드의 글은 kind=decide·decision_id·action 을 더함. 글 전체가 A~D·🅰️·1~4·추천(전부 추천)이면 action=pick|approve(+pick·option_text),
           그 밖은 action=free(원문 그대로). 옵션 기록 없는 카드도 free
수신 확인 답장: 새 사용자 메시지의 스레드에 "📥 메시지 집계 중이에요" 1회(chat:write). 같은 스레드 30초 1회, 60초 3회, 회차 3회. 결정·승인 같은 명령 답글에는 달지 않음.
           slack_cmd.sh 즉답 명령·30분 넘게 묵은 항목은 제외. 권한 부족이면 조용히 건너뛰고 10분마다 재시도(상태: ack_disabled_reason).
           전송 전에 상태(acked·ack_sent)부터 저장하므로 실패해도 다시 보내지 않음. 이미 보낸 답장은 지우거나 고치지 않음(아래 전달 완료 표시만 예외)
전달 완료 표시: 메인 전달(slack_push.sh)이 slack-push-delivered.jsonl 에 남긴 기록을 읽어 그 스레드의 확인 답장을 chat.update(POST, chat:write)로
           "📨 메인에 전달했어요 (n건)" 으로 고침. n 은 스레드 누적 전달 건수, 기록 1건·스레드 1개당 1회, 회차당 5회.
           처리 위치는 기록의 단조 증가 번호(seq)로 저장해 그보다 큰 기록만 처리(기록 파일이 잘려도 옛 기록 재처리 없음).
           앞 확인 답장이 아직 '집계 중'이면 같은 스레드에 새 답장을 보내지 않음(앞 답장이 영구히 '집계 중'으로 남는 것 방지)
           실패(message_not_found·cant_update_message·edit_window_closed 등)는 stderr 1줄만 남기고 재시도 안 함. LRM_ACK_UPDATE=off 로 끔
메인 전달(push): 회차 끝에 slack_push.sh --kick 호출. 사용자 메시지를 묶어 메인 터미널에 한 번 전달하고 .main-read-lines 를 올림.
           상세·끄는 법(LRM_PUSH=off)은 slack_push.sh --help
환경 변수: LRM_HUB_DIR 작업 폴더(기본 ~/.config/lrm-hubs), SLACK_API_BASE API 주소(테스트용),
           LRM_ACK=off 수신 확인 답장 끔(기본 켜짐), LRM_ACK_TEXT 답장 문구(200자 제한·제어 문자 제거),
           LRM_RETRY_CAP 429 대기 상한 초(기본 20), LRM_DL_BUDGET 회차 첨부 내려받기 시간 상한 초(기본 60),
           LRM_REACT_MAX 회차당 반응 호출 상한(기본 10, 0~50. 정수가 아니면 10. 1은 2로 올림: ✅ 전환은 호출 2개 한 쌍)
종료 코드: 0 성공, 1 API·네트워크 실패, 2 설정·인자 오류
EOF
}
ONCE=0 JSON=0 DAYS=7
while [ $# -gt 0 ]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    --once) ONCE=1; shift ;;
    --json) JSON=1; shift ;;
    --days) DAYS="${2:?--days 값 필요}"; shift 2 ;;
    *) echo "알 수 없는 인자: $1 — --help 참고" >&2; exit 2 ;;
  esac
done
[ "$ONCE" = 1 ] || { echo "--once 필요 — 반복은 launchd 가 담당" >&2; exit 2; }
case "$DAYS" in ''|*[!0-9]*) echo "--days 는 정수" >&2; exit 2 ;; esac
DIR="${LRM_HUB_DIR:-$HOME/.config/lrm-hubs}"
ENV="$DIR/slack-bot.env"
[ -f "$ENV" ] || { echo "slack-bot.env 없음 — 사용자 설정 필요" >&2; exit 2; }
. "$ENV"
: "${SLACK_BOT_TOKEN:?}" "${SLACK_USER_ID:?}"
# 동시 실행 방지(launchd 2분 간격·소켓 수신기·수동 실행 겹침)
LOCK="$DIR/.slack-poll.lock"
# 5분 넘은 잠금은 죽은 실행의 잔재로 보고 제거
[ -d "$LOCK" ] && [ -n "$(find "$LOCK" -maxdepth 0 -mmin +5 2>/dev/null)" ] && rmdir "$LOCK" 2>/dev/null
mkdir "$LOCK" 2>/dev/null || { [ "$JSON" = 1 ] && echo '{"ok":true,"added":0,"skipped":"locked"}'; exit 0; }
trap 'rmdir "$LOCK"' EXIT
export SLACK_BOT_TOKEN SLACK_USER_ID
# 수집 실패(429 포함)여도 아래 즉답·대기 목록 단계는 건너뛰지 않음. 종료 코드만 마지막에 전달
RC=0
# 회차 전체 시간 예산(초). 수집·대기 목록·즉답(내부에서 대기 목록 재호출)의 재시도 예산 합이 5분 잠금 만료(300초) 아래여야 동시 실행이 안 생김.
# 앞 단계가 쓴 만큼 뒤 단계 예산을 줄여 줌
TOTAL="${LRM_TOTAL_BUDGET:-270}"; T0=$(date +%s)
cap() {  # cap <단계 상한>: 남은 총 예산과 상한 중 작은 값, 최소 1초
  local v=$((TOTAL - ($(date +%s) - T0)))
  [ "$v" -gt "$1" ] && v="$1"
  [ "$v" -lt 1 ] && v=1
  echo "$v"
}
LRM_POLL_BUDGET="$(cap "${LRM_POLL_BUDGET:-90}")" python3 -I - "$DIR" "$DAYS" "$JSON" <<'PY' || RC=$?
import json, os, re, subprocess, sys, time, unicodedata, urllib.error, urllib.parse, urllib.request
DIR, DAYS, AS_JSON = sys.argv[1], int(sys.argv[2]), sys.argv[3] == "1"
TOKEN, ME = os.environ["SLACK_BOT_TOKEN"], os.environ["SLACK_USER_ID"]
BASE = os.environ.get("SLACK_API_BASE", "https://slack.com/api")
RETRY_CAP = float(os.environ.get("LRM_RETRY_CAP", "20"))  # 429 한 번당 대기 상한
# 회차 전체 재시도 시간 예산. 5분 잠금 만료 전에 끝나야 동시 실행·이중 기록이 안 생김
DEADLINE = time.time() + float(os.environ.get("LRM_POLL_BUDGET", "90"))
RETRIES = 2
PAGE_LIMIT = 200
MAX_PAGES = 5
SENT, INBOX, STATE = (os.path.join(DIR, n) for n in ("slack-sent.jsonl", "slack-inbox.jsonl", "slack-poll-state.json"))
# 반응 → 답 해석. 같은 이모지도 메시지 종류에 따라 뜻이 다름(결정 카드 REACTION_ACTION, 시안 REVIEW_ACTION). 표에 없으면 이름 그대로
REACTION_ACTION = {"white_check_mark": "approve", "x": "hold", "비서-추천대로": "approve", "비서-lgtm": "approve", "비서동의": "approve", "비서-보류": "hold", "비서-a": "a", "비서-b": "b", "비서-c": "c", "비서-d": "d"}
REVIEW_ACTION = {"white_check_mark": "pass", "x": "fail", "비서-a": "pick_a", "비서-b": "pick_b"}
REVIEW = "review"
# 번호 카드(보낸 기록에 options 가 있는 decide)·할 일(todo) 해석. 이름은 slack_notify.sh 의 OPT_EMOJI 와 같아야 함.
# 사용자 답이 메인에 넘어가므로 틀린 해석이 가장 나쁨: 표·범위·기록 모양이 하나라도 어긋나면 해석하지 않고 이름 그대로
DECIDE, TODO = "decide", "todo"
PICK_EMOJI = {"one": 1, "two": 2, "three": 3, "four": 4}
TODO_ACTION = {"white_check_mark": "done", "hammer_and_wrench": "done", "x": "hold"}
ANSWER_TYPES = ("reaction", "thread_reply")  # 결정 답이 되는 inbox 레코드 종류
ANSWER_ACTIONS = ("pick", "approve", "hold")  # 결정 답. 이 셋만 충돌 판정·대기 목록 닫힘에 씀
DECISION_ID_RE = re.compile(r"D-[0-9A-Za-z]{1,16}", re.ASCII)
TODO_ID_RE = re.compile(r"T-[0-9A-Za-z]{1,16}", re.ASCII)
OPTION_TEXT_MAX = 80
SKIP_WHY = {"range": "범위 밖", "options": "옵션 정보 오류"}  # 해석하지 않은 사유(stderr). 문구·제목은 쓰지 않음
TRANSPORT_KINDS = ("text", "file")  # slack_notify.sh 기록의 kind 중 전송 방식 값(메시지 종류 아님)
COMMANDS = [
    ("decision", re.compile(r"^결정\s*(\d+)\s+(\S+)$")),
    ("all_recommended", re.compile(r"^전부\s*추천$")),
    ("status", re.compile(r"^현황$")),
    ("hold", re.compile(r"^보류(\s+.+)?$")),
    ("approve", re.compile(r"^승인(\s+.+)?$")),
]
# 첨부 내려받기 설정. 토큰은 slack.com 계열 https 주소에만 보냄
# 주소 판정은 파싱 전 원문 기준. urlsplit 은 \t\r\n 을 지우고 \ 를 다르게 읽어 curl 해석과 어긋나므로, 공백·제어 문자·"·\ 가 든 주소는 거부함
SLACK_URL_RE = re.compile(r"https://(?:[a-z0-9-]+\.)*slack\.com(?:/[!#-\[\]-~]*)?", re.ASCII | re.IGNORECASE)
NON_ASCII_RE = re.compile(r"[^\x00-\x7f]+")
CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")  # curl 설정 파일에서 줄바꿈이 새 옵션이 되는 것을 막는 이중 방어
# curl 자식에게 넘길 환경 허용 목록. 프록시·CA 변수는 사내망에서 내려받기에 필요하며 토큰·비밀이 아님
CURL_ENV_KEYS = ("PATH", "HOME", "SSL_CERT_FILE", "LANG", "LC_ALL", "TMPDIR", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy",
                 "NO_PROXY", "no_proxy", "CURL_CA_BUNDLE")
FILES_DIR = os.path.join(DIR, "slack-files")
FILE_MAX_BYTES = 25 * 1024 * 1024
FILE_MAX_TIME = 45                 # 파일 1개 curl --max-time 초
# 회차 전체 내려받기 시간 상한(초). api() 와 같은 벽시계 DEADLINE 을 쓰므로 내려받기가 길면 뒤의 conversations.replies·reactions.get
# 호출 시간이 줄어 회차가 실패할 수 있음. 그래서 DEADLINE 앞 DL_RESERVE 초는 내려받기에 쓰지 않음
FILE_BUDGET = float(os.environ.get("LRM_DL_BUDGET", "60"))
DL_RESERVE = 20
NAME_MAX_CHARS, NAME_MAX_BYTES = 100, 200  # 파일명 상한. 바이트 상한은 한글 3바이트 × 이름 + 접두사가 APFS 255바이트를 넘지 않게 함
USER_SUBTYPES = (None, "file_share")       # 사용자 본인 메시지로 인정할 subtype. 첨부 메시지는 file_share
SHARE_SINCE_KEY = "file_share_since"       # 상태 파일 키: 첨부 메시지 수집 시작 시각(초)
SHARE_GRACE = 300                          # 수집 시작 시각을 현재보다 앞당기는 여유(초)
OWN_SINCE_KEY = "own_reply_since"          # 상태 파일 키: 본인 최상위 메시지의 스레드 답글 수집 시작 시각(초)
ID_RE = re.compile(r"[A-Za-z0-9]{1,32}")
UNSAFE_CHARS = re.compile(r"[^0-9A-Za-z가-힣._-]")
DL = {"ok": 0, "fail": {}, "spent": 0.0}  # 회차 집계: 성공 수, 사유별 실패 수, 사용 시간
# 읽음 반응 설정. 진행 표 state["reacted"][ts] = new(👀 대기) → eyes(👀 달림) → done(✅) | skip(포기)
RX_SEEN, RX_DONE = "eyes", "white_check_mark"
RX_TYPES = ("note", "command", "thread_reply")  # 반응 대상 레코드. type=reaction·봇 줄은 제외
# 사용자 반응(이모지)의 확인 표식. Slack 은 반응에 반응을 못 달아 반응이 달린 봇 메시지에 👀 → ☑️ 를 닮.
# 완료 표식을 ✅ 와 다르게 둔 이유: 사용자가 결정·시안에 누른 ✅(승인·합격)와 한 칩으로 합쳐져 구분이 안 됨
RX_ON_DONE = "ballot_box_with_check"
BCAST_SINCE_KEY = "thread_broadcast_since"  # 상태 파일 키: '채널에도 보내기' 답글 수집 시작 시각(초). 이전 broadcast 답글은 seen 에만 기록
REPLY_SUBTYPES = USER_SUBTYPES + ("thread_broadcast",)  # 스레드 답글로 인정할 subtype. '채널에도 보내기'를 켠 답글은 thread_broadcast
def env_int(name, default, lo, hi):
    """정수 환경 변수. 정수가 아니면 default, 범위 밖이면 경계값. 잘못된 설정 하나가 수집 전체를 죽이지 않게 함"""
    try:
        v = int(os.environ.get(name, default))
    except ValueError:
        return default
    return max(lo, min(v, hi))

RX_MAX = env_int("LRM_REACT_MAX", 10, 0, 50)  # 회차당 반응 API 호출 상한
RX_MAX = max(RX_MAX, 2) if RX_MAX else 0      # ✅ 전환이 제거+추가 한 쌍이라 1이면 영영 전환할 수 없음
RX_RETRY = 600                  # 권한 부족 뒤 재시도 간격(초)
RX_STALE = 1800                 # 이 시간보다 오래된 새 메시지에는 👀 를 달지 않음(권한 복구 직후 묵은 메시지 폭주 방지)
RX_KEEP = 30 * 86400            # 진행 표 보관 기간(seen 과 같음)
READ_MARK = ".main-read-lines"  # 메인이 읽은 inbox 줄 수. 메인만 갱신하고 여기서는 읽기만 함
# 같은 호출을 다시 해도 되는 오류는 성공으로 봄
RX_OK = {"add": "already_reacted", "remove": "no_reaction"}
# 권한·인증 문제: 회차를 멈추고 RX_RETRY 뒤 재시도 / 메시지 단위 문제: 그 메시지만 포기. 그 밖(429 소진·5xx·시간 예산)은 다음 회차에 재시도
RX_DISABLE = frozenset({"missing_scope", "not_allowed_token_type", "not_authed", "invalid_auth", "account_inactive", "token_revoked", "no_permission", "access_denied", "org_login_required", "ekm_access_denied", "team_access_not_granted"})
RX_SKIP = frozenset({"message_not_found", "channel_not_found", "bad_timestamp", "not_reactable", "too_many_emoji", "too_many_reactions", "is_archived", "thread_locked", "not_in_channel", "user_is_restricted"})
# 수신 확인 자동 답장 설정. 새 사용자 메시지의 스레드에 짧은 답장 1회. 켜고 끄기 LRM_ACK=off, 문구 LRM_ACK_TEXT
ACK_DEFAULT_TEXT = "📥 메시지 집계 중이에요"
ACK_TEXT_MAX = 200                           # 문구 상한(글자 수)
ACK_OFF = frozenset({"off", "0", "false", "no"})
ACK_THREAD_GAP = 30                          # 같은 스레드 재전송 간격(초)
ACK_WINDOW, ACK_MAX = 60, 3                  # 상한: ACK_WINDOW 초 안 ACK_MAX 회. 회차당에도 같은 값을 적용(회차가 창보다 길어 창이 비어도 폭주하지 않게)
ACK_MIN_LEFT = 5                             # 남은 시간 예산이 이보다 짧으면 호출하지 않고 건너뜀(초)
ACK_KEEP, ACK_KEYS_MAX = 30 * 86400, 500     # 스레드별 기록 보관 기간·개수 상한
ACK_DISABLE = RX_DISABLE | {"channel_not_found", "not_in_channel", "is_archived", "restricted_action"}  # 권한·채널 문제: ACK_RETRY 뒤 재시도
ACK_RETRY = RX_RETRY
# 전달 완료 표시: 메인 전달(slack_push.py)이 slack-push-delivered.jsonl 에 남긴 기록을 읽어 확인 답장을 chat.update 로 고침.
# 토큰이 없는 push 대신 여기서 호출. LRM_ACK=off 또는 LRM_ACK_UPDATE=off 면 안 함
PUSH_DELIVERED = os.path.join(DIR, "slack-push-delivered.jsonl")
DONE_TEXT = "📨 메인에 전달했어요 ({n}건)"
UPD_MAX = 5                                  # 회차당 chat.update 상한
# slack_cmd.sh 가 직접 답하는 명령. case 라벨(대기|미승인|"승인 대기" / PR|pr|"초안 PR" / 할일|"할 일" / CI|ci / 문서\ *)을 옮긴 것이라 그쪽을 바꾸면 함께 바꿀 것
CMD_REPLY_EXACT = frozenset({"대기", "미승인", "승인 대기", "PR", "pr", "초안 PR", "할일", "할 일", "CI", "ci"})
CMD_REPLY_PREFIX = "문서 "

class ApiError(Exception):
    code = None  # Slack 응답의 error 코드. HTTP·시간 예산 오류는 None

def api(method, _post=False, **params):
    """_post=True 는 form 본문 POST. 문서상 POST 만 허용하는 쓰기 메서드(chat.update)용. 기존 호출은 GET 그대로"""
    q = urllib.parse.urlencode(params)
    for attempt in range(RETRIES + 1):
        left = DEADLINE - time.time()
        if left <= 0:
            raise ApiError(f"{method}: 재시도 시간 예산 초과")
        if _post:
            req = urllib.request.Request(f"{BASE}/{method}", data=q.encode(), method="POST",
                                         headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/x-www-form-urlencoded"})
        else:
            req = urllib.request.Request(f"{BASE}/{method}?{q}", headers={"Authorization": f"Bearer {TOKEN}"})
        try:
            with urllib.request.urlopen(req, timeout=min(15, left)) as r:
                d = json.load(r)
            break
        except urllib.error.HTTPError as e:
            # 429 는 Retry-After 만큼 쉬고 재시도. 횟수·시간 예산 소진 시 ApiError 로 올려 회차를 실패 처리
            if e.code != 429 or attempt == RETRIES:
                raise ApiError(f"{method}: HTTP {e.code}")
            try:
                wait = min(float(e.headers.get("Retry-After", "1")), RETRY_CAP)
            except ValueError:
                wait = 1.0
            if wait >= DEADLINE - time.time():
                raise ApiError(f"{method}: HTTP 429, 대기 {wait:g}초가 남은 예산을 넘김")
            time.sleep(wait)
    if not d.get("ok"):
        err = ApiError(f"{method}: {d.get('error')}")
        err.code = d.get("error")
        raise err
    return d

def safe_name(name):
    # NFC 정규화(macOS 의 NFD 한글 이름 보존) 뒤 경로 구분자는 마지막 조각만 남기고, ".."·제어 문자·앞 점·허용 문자 밖은 제거 또는 _ 치환
    n = "".join(c for c in unicodedata.normalize("NFC", str(name or "")) if c.isprintable()).replace("\\", "/").rsplit("/", 1)[-1]
    n = UNSAFE_CHARS.sub("_", n)
    while ".." in n:
        n = n.replace("..", ".")
    n = n.lstrip(".")
    stem, dot, ext = n.rpartition(".")
    stem, ext = (stem, "." + ext) if dot and 0 < len(ext) <= 10 else (n, "")
    while len(stem) + len(ext) > NAME_MAX_CHARS or len((stem + ext).encode()) > NAME_MAX_BYTES:
        stem = stem[:-1]
    return (stem or "file") + ext

def slack_url(url):
    """slack.com 계열 https 주소면 비ASCII 만 퍼센트 인코딩한 주소를, 아니면 None 반환. curl 에는 이 반환값을 넘김"""
    if not isinstance(url, str):
        return None
    # 한글 경로는 허용. 비ASCII 공백·제어 문자(U+2028·U+2029·U+0085·NBSP 등)는 인코딩해 통과시키지 않고 거부
    if any(ord(c) > 127 and (c.isspace() or not c.isprintable()) for c in url):
        return None
    try:
        url = NON_ASCII_RE.sub(lambda m: urllib.parse.quote(m.group()), url)
    except UnicodeEncodeError:  # 짝 없는 서로게이트
        return None
    if not SLACK_URL_RE.fullmatch(url):
        return None
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    return url if host == "slack.com" or host.endswith(".slack.com") else None

def is_slack_url(url):
    return slack_url(url) is not None

def curl_conf(url):
    # 토큰이 명령줄(ps)에 안 보이게 curl 설정을 표준입력(-K -)으로 넘김
    if CONTROL_RE.search(str(url)) or CONTROL_RE.search(TOKEN):
        raise ValueError("curl 설정에 제어 문자 불가")
    esc = lambda s: str(s).replace("\\", "\\\\").replace('"', '\\"')
    return f'header = "Authorization: Bearer {esc(TOKEN)}"\nurl = "{esc(url)}"\n'

def fetch_file(f):
    """첨부 1개를 FILES_DIR 에 저장하고 (경로, None) 반환. 건너뛰면 (None, 사유 코드). 예외를 밖으로 내지 않음"""
    fid = str(f.get("id") or "")
    if not ID_RE.fullmatch(fid):
        return None, "id"
    # 본인이 올린 파일만 받음. 다른 사람 소유 파일은 건너뜀(owner). user 키가 없는 파일(삭제·접근 확인 필요)은 통과하나 url 이 없어 url 실패가 됨
    if f.get("user") and f["user"] != ME:
        return None, "owner"
    os.makedirs(FILES_DIR, mode=0o700, exist_ok=True)
    os.chmod(FILES_DIR, 0o700)
    prefix = f"{fid}_"
    have = sorted(n for n in os.listdir(FILES_DIR) if n.startswith(prefix))
    if have:  # 같은 file_id 가 이미 있으면 다시 받지 않음
        return os.path.join(FILES_DIR, have[0]), None
    url = f.get("url_private_download") or f.get("url_private")
    url = slack_url(url)
    if not url:
        return None, "url"
    size = f.get("size")
    if isinstance(size, int) and size > FILE_MAX_BYTES:
        return None, "size"
    left = min(FILE_BUDGET - DL["spent"], DEADLINE - time.time() - DL_RESERVE)
    if left < 1:
        return None, "budget"
    max_time = int(min(FILE_MAX_TIME, left))
    tmp = os.path.join(FILES_DIR, f".part-{os.getpid()}-{fid}")
    t0 = time.monotonic()
    try:
        os.close(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600))
        env = {k: os.environ[k] for k in CURL_ENV_KEYS if k in os.environ}
        # -q 는 첫 인자여야 ~/.curlrc 를 무시함
        p = subprocess.run(
            ["curl", "-q", "-sS", "-L", "--proto", "=https", "--proto-redir", "=https", "--max-redirs", "5",
             "--max-filesize", str(FILE_MAX_BYTES), "--max-time", str(max_time),
             "-o", tmp, "-w", "%{http_code}\t%{content_type}", "-K", "-"],
            input=curl_conf(url), capture_output=True, text=True, timeout=max_time + 10, env=env)
        if p.returncode != 0:
            return None, "curl"
        code, _, ctype = p.stdout.strip().partition("\t")
        if code != "200":
            return None, "429" if code == "429" else "http"
        orig = (f.get("mimetype") or "").lower()
        is_html = orig.startswith("text/html") or f.get("filetype") == "html"
        if not is_html:  # 권한 부족 시 로그인 HTML 로 리다이렉트됨
            with open(tmp, "rb") as fh:
                head = fh.read(512).lstrip().lower()
            if ctype.lower().startswith("text/html") or head.startswith((b"<!doctype html", b"<html")):
                return None, "html"
        if os.path.getsize(tmp) > FILE_MAX_BYTES:
            return None, "size"
        dest = os.path.join(FILES_DIR, prefix + safe_name(f.get("name") or f.get("title")))
        os.replace(tmp, dest)
        os.chmod(dest, 0o600)
        return dest, None
    except subprocess.TimeoutExpired:
        return None, "timeout"
    except Exception:
        return None, "error"
    finally:
        DL["spent"] += time.monotonic() - t0
        if os.path.exists(tmp):
            os.unlink(tmp)

def fetch_files(files):
    """(저장 경로 목록, 실패 목록[{"id","why"}]) 반환. 실패 항목에는 id 와 사유 코드만 담음(이름·URL·토큰 금지)"""
    paths, failed = [], []
    for f in files or []:
        try:  # 폴더 생성 실패 같은 OSError 가 main() 의 API 실패 처리로 번지지 않게 막음
            path, why = fetch_file(f) if isinstance(f, dict) else (None, "id")
        except Exception:
            path, why = None, "error"
        if path:
            paths.append(path)
            DL["ok"] += 1
        else:
            DL["fail"][why] = DL["fail"].get(why, 0) + 1
            fid = str(f.get("id") or "") if isinstance(f, dict) else ""
            failed.append({"id": fid if ID_RE.fullmatch(fid) else "", "why": why})
    return paths, failed

def load_jsonl(path):
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return out

def msg_kind(rec):
    """보낸 기록의 메시지 종류. 명시값 우선: form_kind(양식 A), 전송 방식(text·file)이 아닌 kind.
    없으면 파일 첨부 기록이고 제목에 '결정'이 없을 때 review(시안), 그 밖은 None(결정 해석 유지).
    '결정' 제외는 첨부가 달린 결정 카드의 ✅ 를 합격으로 잘못 읽지 않게 함(slack_pending.sh 도 제목의 '결정'으로 결정을 판별)"""
    for k in ("form_kind", "kind"):
        v = rec.get(k)
        if isinstance(v, str) and v and v not in TRANSPORT_KINDS:
            return v
    if rec.get("file_id") and "결정" not in str(rec.get("title") or ""):
        return REVIEW
    return None

def reaction_action(kind, name):
    return (REVIEW_ACTION if kind == REVIEW else REACTION_ACTION).get(name, name)

def card_options(rec):
    """번호 카드 기록의 (결정 번호, {번호: 문구}, 추천 번호). 모양이 틀리면 ValueError"""
    opts, best, did = rec.get("options"), rec.get("recommended"), rec.get("decision_id")
    if not isinstance(opts, dict) or not 2 <= len(opts) <= 4 or set(opts) != {str(i) for i in range(1, len(opts) + 1)} \
            or not all(isinstance(v, str) and v.strip() for v in opts.values()):
        raise ValueError
    if not isinstance(best, int) or isinstance(best, bool) or not 1 <= best <= len(opts):
        raise ValueError
    if not isinstance(did, str) or not DECISION_ID_RE.fullmatch(did):
        raise ValueError
    return did, {int(k): v for k, v in opts.items()}, best

def interpret(rec, kind, name):
    """반응 1건 → (레코드에 더할 필드, 해석하지 않은 사유|None). 옛 카드·시안은 기존 표 그대로이고 레코드 모양도 같음.
    할 일은 kind=todo, 번호 카드는 kind=decide·decision_id 를 붙이며 pick·approve 는 pick·option_text(문구 앞 80자)도 붙임"""
    if kind == TODO:
        out = {"action": TODO_ACTION.get(name, name), "kind": TODO}
        tid = rec.get("todo_id")
        if isinstance(tid, str) and TODO_ID_RE.fullmatch(tid):
            out["todo_id"] = tid
        return out, None
    if kind != DECIDE or "options" not in rec:
        return {"action": reaction_action(kind, name)}, None
    try:
        did, opts, best = card_options(rec)
    except ValueError:
        return {"action": name, "kind": DECIDE}, "options"
    out = {"action": name, "kind": DECIDE}
    if name == "x":
        out["action"] = "hold"
    elif name == "white_check_mark":
        out.update(action="approve", pick=best)
    elif name in PICK_EMOJI:
        if PICK_EMOJI[name] not in opts:  # 범위 검사가 깨져도 KeyError 로 회차 전체가 죽지 않게 사전 조회로 거름
            out["decision_id"] = did
            return out, "range"
        out.update(action="pick", pick=PICK_EMOJI[name])
    out["decision_id"] = did
    if "pick" in out:
        out["option_text"] = opts.get(out["pick"], "")[:OPTION_TEXT_MAX]
    return out, None

# 스레드 글 답 해석. 글 전체가 선택 표기 하나일 때만 선택으로 봄('1. 좌에서 우로…' 같은 긴 글의 머리 숫자는 의견이므로 자유 글).
# 알파벳은 선택지 번호 순서(A=1), 이모지는 Slack 이 :이름: 으로 바꿔 보낸 꼴도 받음
REPLY_LETTER = {"a": 1, "b": 2, "c": 3, "d": 4, "🅰": 1, "🅱": 2, "🅲": 3, "🅳": 4}
REPLY_PICK_RE = re.compile(
    r"^(?::비서-([a-d]):|:regional_indicator_([a-d]):|:([abcd]):|:(one|two|three|four):|([a-dA-D1-4🅰🅱🅲🅳])️?⃣?)"
    r"\s*(?:번|안|으로|로)?\s*(?:ㄱㄱ?|가자|할게|해줘)?\s*[.!)]?$")
REPLY_RECOMMEND_RE = re.compile(r"^(?:전부\s*|다\s*|모두\s*)?추천(?:\s*대로)?\s*(?:ㄱㄱ?|가자|으로|로|해줘)?\s*[.!]?$")

def reply_answer(rec, text):
    """결정 카드 스레드 글 → 레코드에 더할 필드. 카드가 아니면 {}. 선택·추천은 action=pick·approve, 그 밖은 action=free(원문 그대로 전달).
    옵션 기록이 없는 카드(이모지 안내만)는 선택 번호를 알 수 없어 free"""
    if rec is None or rec.get("form_kind") != DECIDE or not DECISION_ID_RE.fullmatch(str(rec.get("decision_id") or "")):
        return {}
    out = {"kind": DECIDE, "decision_id": rec["decision_id"], "action": "free"}
    try:
        _, opts, best = card_options(rec)
    except ValueError:
        return out
    t = " ".join(unicodedata.normalize("NFC", text or "").split())
    if REPLY_RECOMMEND_RE.match(t):
        return {**out, "action": "approve", "pick": best, "option_text": opts[best][:OPTION_TEXT_MAX]}
    m = REPLY_PICK_RE.match(t)
    if m:
        tok = next(g for g in m.groups() if g).lower()
        n = {"one": 1, "two": 2, "three": 3, "four": 4}.get(tok) or (int(tok) if tok.isdigit() else REPLY_LETTER[tok])
        if n in opts:
            return {**out, "action": "pick", "pick": n, "option_text": opts[n][:OPTION_TEXT_MAX]}
    return out

def answer_key(d):
    """결정 답의 비교 값. 같은 답(선택 1 과 추천대로=1)은 같은 값"""
    return "hold" if d.get("action") == "hold" else str(d.get("pick"))

def mark_conflict(items, prior):
    """같은 카드(parent_ts·decision_id)의 이전 답(inbox)과 이번 회차 답이 둘 이상 갈리면 이번 회차 답 전부에 conflict·choices 를 붙임.
    inbox 는 추가 전용이라 앞서 기록된 줄은 고치지 않음. 취소를 볼 수 없어 1 취소 → 2 도 충돌로 남김(사용자 확인)"""
    head = items[0]
    keys = {answer_key(d) for d in prior if d.get("type") in ANSWER_TYPES and d.get("parent_ts") == head["parent_ts"]
            and d.get("decision_id") == head["decision_id"] and d.get("action") in ANSWER_ACTIONS}
    keys |= {answer_key(d) for d in items}
    if len(keys) > 1:
        choices = sorted(keys, key=lambda k: (k == "hold", k))
        for d in items:
            d.update(conflict=True, choices=choices)

def classify(text):
    t = " ".join(text.split())
    for name, pat in COMMANDS:
        m = pat.match(t)
        if m:
            args = [g.strip() for g in m.groups() if g]
            return "command", name, args
    return "note", None, []

def seen_from_inbox():
    seen = {}
    for d in load_jsonl(INBOX):
        key = f"react:{d.get('parent_ts')}:{d.get('emoji')}" if d.get("type") == "reaction" else f"msg:{d.get('ts')}"
        seen[key] = int(time.time())
    return seen

def load_state():
    # 상태 파일이 없거나 손상된 경우 모두 inbox 로 seen 복원. 손상 뒤 회차가 API 실패로 끝나 파일이 안 만들어져도 다음 회차에 중복 기록이 안 생김
    if not os.path.exists(STATE):
        return {"seen": seen_from_inbox()}
    try:
        with open(STATE, encoding="utf-8") as f:
            state = json.load(f)
        if not isinstance(state, dict):
            raise ValueError("상태 파일 형식 오류")
        return state
    except ValueError:
        os.replace(STATE, f"{STATE}.corrupt-{int(time.time())}")
        return {"seen": seen_from_inbox()}

def read_ts():
    """(메시지 ts 집합, 반응 대상 메시지 ts 집합). 메인이 읽은 inbox 줄(READ_MARK 의 줄 수)까지 기준. 표식이 없거나 깨졌으면 둘 다 빈 집합.
    반응 대상은 그 메시지의 반응 줄이 모두 읽혔을 때만 읽음(뒤에 온 반응이 안 읽혔으면 ☑️ 로 바꾸지 않음)"""
    try:
        with open(os.path.join(DIR, READ_MARK), encoding="utf-8") as f:
            n = int(f.read().strip())
        out, on, unread = set(), set(), set()
        # n 이 inbox 줄 수보다 크면 전부 읽음으로 봄. inbox 를 회전·자르면 표식(.main-read-lines)도 0 으로 함께 리셋할 것
        with open(INBOX, encoding="utf-8") as f:
            for i, line in enumerate(f):
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(d, dict):
                    continue
                if d.get("type") == "reaction" and d.get("parent_ts"):
                    (on if i < n else unread).add(str(d["parent_ts"]))
                elif i < n and d.get("type") in RX_TYPES and d.get("ts"):
                    out.add(d["ts"])
        return out, on - unread
    except (OSError, ValueError):
        return set(), set()

def rx_map(state):
    r = state.get("reacted")
    if not isinstance(r, dict):
        r = state["reacted"] = {}
    return r

def on_map(state):
    """반응 확인 진행 표 {봇 메시지 ts: {"s": new|eyes|done|skip, "at": 반응 수집 시각}}. 메시지 ts 가 아니라 수집 시각으로
    묵음·보관을 판정함(결정 카드는 몇 시간 전 메시지라 ts 기준이면 바로 묵음 처리됨). 형식이 틀린 칸은 버림"""
    r = state.get("reacted_on")
    num = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool)
    r = {k: v for k, v in (r if isinstance(r, dict) else {}).items()
         if isinstance(k, str) and k.replace(".", "", 1).isdigit() and isinstance(v, dict)
         and v.get("s") in ("new", "eyes", "done", "skip") and num(v.get("at"))}
    state["reacted_on"] = r
    return r

def mark_on(state, ts, now):
    """새 반응이 수집된 봇 메시지에 확인 표식 대기(new). 끝난(done·skip) 메시지에 새 반응이 오면 다시 대기로 돌림"""
    on = on_map(state)
    if on.get(ts, {}).get("s") not in ("new", "eyes"):
        on[ts] = {"s": "new", "at": now}

def react_phase(state, ch, now):
    """👀 달기(new → eyes), 읽힌 줄은 👀 제거 + ✅ 추가(→ done). 호출은 회차당 RX_MAX 개까지.
    다른 반응은 건드리지 않음(제거는 이 봇의 👀 만). 예외는 호출부가 삼킴"""
    rx, on = rx_map(state), on_map(state)
    for k in [k for k in rx if not (isinstance(k, str) and k.replace(".", "", 1).isdigit()) or float(k) < now - RX_KEEP]:
        del rx[k]
    for k in [k for k, v in on.items() if v["at"] < now - RX_KEEP]:
        del on[k]
    # 권한 정지·호출 상한과 무관하게 묵은 new 를 정리. 정지 중 쌓인 과거 메시지에 복구 뒤 소급 반응이 달리는 것을 막음
    for k, s in rx.items():
        if s == "new" and float(k) < now - RX_STALE:
            rx[k] = "skip"
    for v in on.values():
        if v["s"] == "new" and v["at"] < now - RX_STALE:
            v["s"] = "skip"
    on_s = {k: v["s"] for k, v in on.items()}
    if not any(s in ("new", "eyes") for s in list(rx.values()) + list(on_s.values())):
        return
    at = state.get("reactions_disabled_at")
    if state.get("reactions_disabled_reason") and isinstance(at, (int, float)) and now - at < RX_RETRY:
        return
    read, read_on = read_ts()
    calls = 0

    def call(op, ts, name):
        """'ok' 성공 / 'skip' 이 메시지 포기 / 'stop' 이번 회차 중단"""
        nonlocal calls
        if calls >= RX_MAX:
            return "stop"
        calls += 1
        try:
            api(f"reactions.{op}", channel=ch, timestamp=ts, name=name)
        except ApiError as e:
            if e.code == RX_OK[op]:
                return "ok"
            if e.code in RX_DISABLE:
                if state.get("reactions_disabled_reason") != e.code:  # 같은 사유는 다시 알리지 않음
                    print(f"읽음 반응 사용 불가({e.code}) — Slack 앱 scope(reactions:write) 확인, {RX_RETRY // 60}분마다 재시도", file=sys.stderr)
                state["reactions_disabled_reason"], state["reactions_disabled_at"] = e.code, int(time.time())
            return "skip" if e.code in RX_SKIP else "stop"
        state.pop("reactions_disabled_reason", None)
        state.pop("reactions_disabled_at", None)
        return "ok"

    # 사용자 메시지 표(✅) → 반응 확인 표(☑️) 순. 두 표가 호출 상한 RX_MAX 를 함께 씀
    tables = ((rx, read, RX_DONE), (on_s, read_on, RX_ON_DONE))
    try:
        for tab, rd, _ in tables:
            for ts in sorted(k for k, s in tab.items() if s == "new" and k not in rd):
                r = call("add", ts, RX_SEEN)
                if r == "stop":
                    return
                tab[ts] = "eyes" if r == "ok" else "skip"
        for tab, rd, done in tables:
            for ts in sorted(k for k, s in tab.items() if s in ("new", "eyes") and k in rd):
                if tab[ts] == "eyes":
                    if calls + 2 > RX_MAX:  # 제거만 하고 추가가 상한에 걸리면 👀·완료 표식 둘 다 없는 회차가 생김. 한 쌍이 들어갈 때만 시작
                        return
                    r = call("remove", ts, RX_SEEN)
                    if r == "stop":
                        return
                    if r == "skip":
                        tab[ts] = "skip"
                        continue
                r = call("add", ts, done)
                if r == "stop":
                    return
                tab[ts] = "done" if r == "ok" else "skip"
    finally:
        for k, s in on_s.items():
            on[k]["s"] = s

def ack_text():
    """LRM_ACK_TEXT 를 정리한 답장 문구. 제어 문자·짝 없는 서로게이트(잘못된 UTF-8)는 제거, ACK_TEXT_MAX 자로 자름. 비면 기본 문구"""
    raw = os.environ.get("LRM_ACK_TEXT") or ACK_DEFAULT_TEXT
    t = "".join(c for c in raw if unicodedata.category(c) not in ("Cc", "Cs")).strip()[:ACK_TEXT_MAX].strip()
    return t or ACK_DEFAULT_TEXT

def cmd_replies(item):
    """slack_cmd.sh 가 직접 답하는 메시지인가. slack_cmd.sh 의 처리 대상(type=command|note, 스레드 답글 제외)과
    줄 단위 case 판별(read -r 이 줄 양끝 공백·탭 제거)을 그대로 따름"""
    if item.get("type") not in ("command", "note") or item.get("parent_ts"):
        return False
    for line in (item.get("text") or item.get("command") or "").strip().split("\n"):
        t = line.strip(" \t")
        if t in CMD_REPLY_EXACT or t.startswith(CMD_REPLY_PREFIX):
            return True
    return False

def ack_phase(state, ch, now, items):
    """새 사용자 메시지의 스레드(스레드 답글이면 부모, 최상위면 그 메시지)에 확인 답장 1회.
    제외: 결정·승인 같은 명령(command, 분석 대상 아님), slack_cmd.sh 즉답 명령, RX_STALE 보다 묵은 항목. 같은 스레드 ACK_THREAD_GAP 초 1회, ACK_WINDOW 초 ACK_MAX 회, 회차 ACK_MAX 회.
    전송 전에 상태를 먼저 저장(at-most-once): 실패해도 재시도하지 않음. 예외는 호출부가 삼킴"""
    if os.environ.get("LRM_ACK", "").strip().lower() in ACK_OFF:
        return
    cand = sorted((it for it in items if it.get("type") in RX_TYPES and float(it["ts"]) >= now - RX_STALE
                 and not it.get("command") and not cmd_replies(it)),
                  key=lambda it: float(it["ts"]))
    if not cand:
        return
    at = state.get("ack_disabled_at")
    if state.get("ack_disabled_reason") and isinstance(at, (int, float)) and time.time() - at < ACK_RETRY:
        return
    num = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool)
    t = int(time.time())
    acked = state.get("acked")
    acked = {k: v for k, v in (acked if isinstance(acked, dict) else {}).items() if num(v) and v > t - ACK_KEEP}
    for k in sorted(acked, key=acked.get)[:max(0, len(acked) - (ACK_KEYS_MAX - ACK_MAX))]:  # 이번 회차에 더해도 ACK_KEYS_MAX 이하
        del acked[k]
    state["acked"] = acked
    msgs = state.get("ack_msgs")
    state["ack_msgs"] = {k: v for k, v in (msgs if isinstance(msgs, dict) else {}).items() if k in acked}
    sent = state.get("ack_sent")
    sent = [x for x in (sent if isinstance(sent, list) else []) if num(x)]
    text, count = ack_text(), 0
    for it in cand:
        root = str(it.get("parent_ts") or it["ts"])
        t = int(time.time())
        sent = [x for x in sent if 0 <= t - x < ACK_WINDOW]
        if count >= ACK_MAX or len(sent) >= ACK_MAX:
            break
        if 0 <= t - acked.get(root, -ACK_THREAD_GAP - 1) < ACK_THREAD_GAP:
            continue
        prev = state["ack_msgs"].get(root)
        if isinstance(prev, dict) and isinstance(prev.get("ts"), str) and not prev.get("n"):
            continue  # 앞 답장이 아직 '집계 중'. 새로 보내면 ack_msgs 가 새 답장으로 바뀌어 앞 답장은 영구히 '집계 중'으로 남음
        if DEADLINE - time.time() < ACK_MIN_LEFT:  # 수집이 예산을 다 쓴 회차. 남은 항목은 새 항목이 아니게 돼 다시 오지 않으므로 답장 없이 끝남(재시도 안 함)
            print("수신 확인 답장 건너뜀(budget) — 회차 시간 예산 부족, 재시도 안 함", file=sys.stderr)
            return
        acked[root] = t
        sent.append(t)
        state["ack_sent"] = sent
        save_state(state)
        count += 1
        try:
            d = api("chat.postMessage", channel=ch, thread_ts=root, text=text)
            if isinstance(d.get("ts"), str):  # 전달 완료 표시(done_phase)가 이 메시지를 고치려고 보관
                state.setdefault("ack_msgs", {})[root] = {"ts": d["ts"], "ch": d.get("channel") or ch, "n": 0}
        except ApiError as e:
            if e.code in ACK_DISABLE:
                if state.get("ack_disabled_reason") != e.code:  # 같은 사유는 다시 알리지 않음
                    print(f"수신 확인 답장 사용 불가({e.code}) — Slack 앱 scope(chat:write)·채널 확인, {ACK_RETRY // 60}분마다 재시도", file=sys.stderr)
                state["ack_disabled_reason"], state["ack_disabled_at"] = e.code, int(time.time())
            else:
                print(f"수신 확인 답장 실패({e.code or 'http'}) — 재시도 안 함", file=sys.stderr)
            return
        state.pop("ack_disabled_reason", None)
        state.pop("ack_disabled_at", None)
    state["ack_sent"] = sent

def done_phase(state):
    """메인에 전달된 스레드의 확인 답장을 "📨 메인에 전달했어요 (n건)" 으로 고침. n 은 그 스레드의 누적 전달 건수.
    기록 1건·스레드 1개당 chat.update 1회, 회차당 UPD_MAX 회. 호출 전에 처리 위치와 건수를 먼저 저장(at-most-once): 실패해도 재시도하지 않음.
    처리 위치는 upd_pos(마지막으로 끝낸 기록의 seq, 단조 증가)이고 그보다 큰 seq 의 기록만 처리. 기록이 한 기록 안에서 회차 상한으로 끊기면
    upd_part={seq,k} 로 이어 처리. seq 가 없는 옛 형식 기록은 처리하지 않음.
    확인 답장 ts 가 없는 스레드(답장 안 보냄·옛 답장)는 호출 없이 처리 위치만 넘김. 예외는 호출부가 삼킴"""
    if os.environ.get("LRM_ACK", "").strip().lower() in ACK_OFF or os.environ.get("LRM_ACK_UPDATE", "").strip().lower() in ACK_OFF:
        return
    state.pop("upd_seen", None)
    pos = state.get("upd_pos")
    pos = pos if isinstance(pos, int) and not isinstance(pos, bool) else 0
    part = state.get("upd_part") if isinstance(state.get("upd_part"), dict) else {}
    msgs = state.get("ack_msgs") if isinstance(state.get("ack_msgs"), dict) else {}
    recs = sorted((r for r in load_jsonl(PUSH_DELIVERED)
                   if isinstance(r, dict) and isinstance(r.get("seq"), int) and not isinstance(r["seq"], bool) and r["seq"] > pos),
                  key=lambda r: r["seq"])
    calls = 0
    for rec in recs:
        roots = list((rec.get("roots") if isinstance(rec.get("roots"), dict) else {}).items())
        for i in range(part.get("k", 0) if part.get("seq") == rec["seq"] else 0, len(roots)):
            root, n = roots[i]
            ent = msgs.get(root)
            live = isinstance(n, int) and isinstance(ent, dict) and isinstance(ent.get("ts"), str)
            if live and (calls >= UPD_MAX or DEADLINE - time.time() < ACK_MIN_LEFT):
                state["upd_part"] = {"seq": rec["seq"], "k": i}
                return
            state["upd_part"] = {"seq": rec["seq"], "k": i + 1}
            if not live:
                continue
            ent["n"] = int(ent.get("n") or 0) + n
            save_state(state)
            calls += 1
            try:
                api("chat.update", _post=True, channel=ent.get("ch") or "", ts=ent["ts"], text=DONE_TEXT.format(n=ent["n"]))
            except ApiError as e:
                print(f"전달 완료 표시 건너뜀({e.code or 'http'}) — 재시도 안 함", file=sys.stderr)
                if e.code in ACK_DISABLE:  # 권한·채널 문제는 나머지도 실패하므로 이번 회차 중단
                    return
        state["upd_pos"] = rec["seq"]
        state.pop("upd_part", None)
        part = {}

def fetch_history(ch, oldest):
    msgs, cursor = [], None
    for _ in range(MAX_PAGES):
        params = {"channel": ch, "oldest": oldest, "limit": PAGE_LIMIT}
        if cursor:
            params["cursor"] = cursor
        d = api("conversations.history", **params)
        msgs += d.get("messages", [])
        cursor = (d.get("response_metadata") or {}).get("next_cursor")
        if not cursor:
            break
    return msgs

def save_state(state):
    tmp = STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f)
    os.replace(tmp, STATE)

def main():
    state = load_state()
    # 첨부 메시지 수집 시작 시각. 키가 없으면 API 호출 전에 저장해, 첫 회차가 실패를 반복해도 시각이 뒤로 밀리지 않게 함.
    # now-SHARE_GRACE: 배포 직전 5분 안에 올린 본인 첨부는 새 것으로 처리하는 것이 의도이며, 그 이전 첨부(옛 명령)는 계속 재생하지 않음
    if not isinstance(state.get(SHARE_SINCE_KEY), (int, float)):
        state[SHARE_SINCE_KEY] = int(time.time()) - SHARE_GRACE
        save_state(state)
    since = state[SHARE_SINCE_KEY]
    # 본인 메시지 스레드 답글 수집 시작 시각. 이전에는 수집하지 않던 묵은 답글이 배포 직후 새 명령으로 재생되지 않게 하는 경계. 키가 없으면 지금(-여유)으로 저장
    if not isinstance(state.get(OWN_SINCE_KEY), (int, float)):
        state[OWN_SINCE_KEY] = int(time.time()) - SHARE_GRACE
        save_state(state)
    own_since = state[OWN_SINCE_KEY]
    # broadcast 답글 수집 시작 시각. 배포 전에 달린 broadcast 답글(옛 결정 명령 등)이 스레드 재조회 때 새 명령으로 재생되지 않게 하는 경계.
    # 여유(SHARE_GRACE) 없이 지금: 배포 전 답글은 전부 옛 것이며 상태를 잃은 첫 회차도 같은 규칙
    if not isinstance(state.get(BCAST_SINCE_KEY), (int, float)):
        state[BCAST_SINCE_KEY] = int(time.time())
        save_state(state)
    bcast_since = state[BCAST_SINCE_KEY]
    seen = state.setdefault("seen", {})
    latest = state.setdefault("latest", {})  # 스레드 부모 ts → 마지막으로 읽은 latest_reply
    ch = api("conversations.open", users=ME)["channel"]["id"]
    cutoff = time.time() - DAYS * 86400
    sent = {r["ts"]: r for r in load_jsonl(SENT) if r.get("ts") and r.get("at", 0) >= cutoff}
    # 파일 업로드는 ts 를 못 받아 file_id 로만 기록됨. 메시지의 files[].id 로 찾아 답글·반응 수집
    sent_files = {r["file_id"]: r for r in load_jsonl(SENT) if r.get("file_id") and r.get("at", 0) >= cutoff}
    new = []
    now = int(time.time())

    # since 이전 첨부 메시지는 지난 메시지가 새 명령·답으로 재생되지 않게 seen 에만 넣음
    def is_backlog(msg):
        return msg.get("subtype") == "file_share" and float(msg["ts"]) < since

    def add(key, item, files=None, backlog=False):
        """새 항목이면 기록하고 True"""
        if key in seen:
            return False
        seen[key] = now
        if backlog:
            return False
        item.update({"channel": ch, "received_at": now})
        # 새 항목일 때만 내려받음(주기 재조회에서 반복 방지). 파일이 없으면 기존 레코드 모양 유지
        paths, failed = fetch_files(files)
        if paths:
            item["files"] = paths
        if failed:
            item["files_failed"] = failed
        new.append(item)
        if item["type"] in RX_TYPES:
            rx_map(state).setdefault(item["ts"], "new")
        return True

    def collect_replies(m, meta, floor=None, card=None):
        """답글이 바뀐 스레드만 조회해 사용자 답글을 thread_reply 로 추가. latest_reply 가 응답에 없을 수 있어 없으면 reply_count 로 비교.
        floor: 이 시각(초) 이전 답글은 기록만 하고 새 항목으로 만들지 않음(본인 메시지 스레드의 배포 전 답글)"""
        ts = m["ts"]
        marker = m.get("latest_reply") or f"n{m.get('reply_count')}"
        if m.get("reply_count") and marker != latest.get(ts):
            lr = m.get("latest_reply")
            if not (floor and lr and float(lr) < floor):  # 마지막 답글이 경계 이전이면 조회 자체를 생략
                for rp in api("conversations.replies", channel=ch, ts=ts, limit=200).get("messages", []):
                    if rp["ts"] != ts and rp.get("user") == ME and not rp.get("bot_id") and rp.get("subtype") in REPLY_SUBTYPES:
                        kind, cmd, args = classify(rp.get("text", ""))
                        item = {"type": "thread_reply", "ts": rp["ts"], "text": rp.get("text", ""), **meta, **reply_answer(card, rp.get("text", ""))}
                        if cmd:
                            item.update({"command": cmd, "args": args})
                        add(f"msg:{rp['ts']}", item, rp.get("files"), is_backlog(rp) or bool(floor and float(rp["ts"]) < floor)
                            or (rp.get("subtype") == "thread_broadcast" and float(rp["ts"]) < bcast_since))
            latest[ts] = marker

    # 반응·답글 수는 목록 응답에 들어 있어 메시지별 조회가 필요 없음. 반응은 오래된 메시지에도 달리므로 매번 기간 전체를 읽음
    hist = fetch_history(ch, f"{cutoff:.6f}")
    for m in hist:
        ts = m["ts"]
        mine = m.get("user") == ME
        own_top = mine and not m.get("bot_id") and m.get("subtype") in USER_SUBTYPES and m.get("thread_ts", ts) == ts
        if own_top:
            kind, cmd, args = classify(m.get("text", ""))
            item = {"type": kind, "ts": ts, "text": m.get("text", "")}
            if cmd:
                item.update({"command": cmd, "args": args})
            add(f"msg:{ts}", item, m.get("files"), is_backlog(m))
        # 반응·답글은 slack_notify.sh 가 기록한 메시지만. 대기 목록 같은 다른 봇 메시지의 반응이 결정 답으로 오판되지 않게 함
        rec = sent.get(ts) or next((sent_files[f["id"]] for f in m.get("files", []) if f.get("id") in sent_files), None)
        if mine and not m.get("files") or rec is None:
            # 본인 최상위 메시지: 확인 답장 스레드에 이어 쓴 사용자 답글만 수집(반응은 대상 아님, 봇 답장은 collect_replies 가 제외)
            if own_top:
                collect_replies(m, {"parent_ts": ts, "title": "", "tag": ""}, own_since)
            continue
        meta = {"parent_ts": ts, "title": rec.get("title", ""), "tag": rec.get("tag", "")}
        kind = msg_kind(rec)
        if kind == REVIEW:  # 다른 종류는 기존 레코드 모양 유지
            meta["kind"] = REVIEW
        reactions = m.get("reactions", [])
        # 응답의 users 는 잘릴 수 있어 본인 반응이 안 보이고 개수가 더 많으면 그 메시지만 reactions.get 으로 보강
        if any(r.get("count", 0) > len(r.get("users", [])) and ME not in r.get("users", []) for r in reactions):
            reactions = api("reactions.get", channel=ch, timestamp=ts).get("message", {}).get("reactions", [])
        for r in reactions:
            if ME in r.get("users", []):
                name = r["name"]
                extra, why = interpret(rec, kind, name)
                item = {"type": "reaction", "emoji": name, **extra, **meta}
                if add(f"react:{ts}:{name}", item):
                    mark_on(state, ts, now)
                    if why:  # 새 반응일 때만 1줄(주기 재조회에서 반복 안 함)
                        print(f"반응 해석 안 함({SKIP_WHY[why]}) — 이모지 이름 그대로 전달", file=sys.stderr)
        collect_replies(m, meta, card=rec)

    # 같은 카드(parent_ts·decision_id)의 결정 답은 반응·스레드 글을 함께 충돌 판정. 앞 회차 답은 inbox 에서 읽음(아래에서 추가 기록하기 전)
    groups = {}
    for it in new:
        if it.get("type") in ANSWER_TYPES and it.get("decision_id") and it.get("action") in ANSWER_ACTIONS:
            groups.setdefault((it["parent_ts"], it["decision_id"]), []).append(it)
    if groups:
        prior = load_jsonl(INBOX)
        for g in groups.values():
            mark_conflict(g, prior)

    # 30일 지난 중복 방지 키 정리, 기간 밖 스레드 기록 정리
    for k in [k for k, v in seen.items() if v < now - 30 * 86400]:
        del seen[k]
    in_range = {m["ts"] for m in hist}
    for k in [k for k in latest if k not in in_range and float(k) < cutoff]:
        del latest[k]
    with open(INBOX, "a", encoding="utf-8") as f:
        for it in new:
            f.write(json.dumps(it, ensure_ascii=False) + "\n")
    save_state(state)  # 반응 호출은 DEADLINE 까지 걸릴 수 있어, 그 사이 죽어도 inbox 중복 기록이 안 생기게 먼저 저장
    try:
        ack_phase(state, ch, now, new)
    except Exception:  # 확인 답장 실패가 수집·상태 저장·반응 단계를 막지 않게 함
        pass
    try:
        done_phase(state)
    except Exception:  # 전달 완료 표시 실패가 반응 단계·상태 저장을 막지 않게 함
        pass
    try:
        react_phase(state, ch, now)
    except Exception:  # 반응 실패가 수집·상태 저장을 막지 않게 함
        pass
    save_state(state)
    bad = sum(DL["fail"].values())
    if bad:  # 사유 코드만 출력. URL·파일명·토큰은 넣지 않음
        why = ", ".join(f"{k} {v}" for k, v in sorted(DL["fail"].items()))
        print(f"첨부 내려받기 실패 {bad}건(성공 {DL['ok']}건) — {why}", file=sys.stderr)
    return len(new)

try:
    n = main()
except (ApiError, OSError) as e:
    msg = str(e)
    print(json.dumps({"ok": False, "error": msg}, ensure_ascii=False) if AS_JSON else f"실패: {msg} — 네트워크·scope(im:history, reactions:read) 확인", file=sys.stdout if AS_JSON else sys.stderr)
    sys.exit(1)
print(json.dumps({"ok": True, "added": n}) if AS_JSON else f"새 항목 {n}건 → {INBOX}")
PY

# 2시간마다 미답 결정 목록 전송(11-05 사용자 요청). 마지막 전송 시각은 .pending-last
L="$DIR/.pending-last"; now=$(date +%s); last=$(cat "$L" 2>/dev/null || echo 0)
if [ $((now - last)) -ge 7200 ]; then echo "$now" > "$L"; LRM_PENDING_BUDGET="$(cap "${LRM_PENDING_BUDGET:-120}")" "$DIR/slack_pending.sh" >/dev/null 2>&1 || true; fi

# 봇 DM 즉답 명령(대기·PR) 처리. 대기 명령이 대기 목록을 다시 부르므로 남은 예산을 그대로 물려줌
LRM_PENDING_BUDGET="$(cap "${LRM_PENDING_BUDGET:-120}")" "$DIR/slack_cmd.sh" >/dev/null 2>&1 || true

# 새 명령 접수(번호·장부·머리말 라우팅). 접수 실패가 메인 전달을 막지 않게 하고, 같은 줄은 멱등이라 회차마다 불러도 중복 접수 없음
LRM_HUB_DIR="$DIR" python3 -I "$(dirname "$0")/intake.py" --once >/dev/null 2>&1 || true

# 새 메시지 묶음 전달(push). 수집이 실패한 회차에도 대기분을 전달하도록 RC 와 무관하게 호출.
# 워커는 분리돼 바로 반환하므로 이 잠금(300초)을 쥐지 않고, 출력 파이프도 닫아 소켓 수신기의 capture_output 이 기다리지 않음
LRM_HUB_DIR="$DIR" /bin/bash "$(dirname "$0")/slack_push.sh" --kick >/dev/null 2>&1 || true

# 허브 보고 전송(outbox → 주제 스레드). 메인 전달 뒤에 불러 Slack 지연이 전달을 막지 않음.
# 회차 총 예산(TOTAL)의 남은 만큼만 전송기 시간 상한으로 넘기고, 남은 예산이 없으면 이번 회차는 건너뜀(300초 잠금 만료 방지)
left=$((TOTAL - ($(date +%s) - T0)))
if [ "$left" -ge 1 ]; then
  [ "$left" -gt "${LRM_REPORT_BUDGET_S:-40}" ] && left="${LRM_REPORT_BUDGET_S:-40}"
  LRM_REPORT_BUDGET_S="$left" LRM_HUB_DIR="$DIR" python3 -I "$(dirname "$0")/report_sender.py" --once >/dev/null 2>&1 || true
fi
exit "$RC"
