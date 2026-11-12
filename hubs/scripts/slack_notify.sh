#!/bin/bash
# 봇으로 사용자 DM 에 글·파일 게시. 토큰은 slack-bot.env(SLACK_BOT_TOKEN, SLACK_USER_ID)에서만 읽음, 출력 금지.
# 보낸 메시지 ts·channel 은 slack-sent.jsonl 에 기록(slack_poll.sh 가 스레드 답글·반응 수집에 사용).
# --kind 를 주면 메시지 양식 A(hubs/docs/slack-format.md)를 검사하고, 종류별 색 띠(attachment)에 본문을 담아 보냄.
# --kind 없는 기존 호출은 검사·출력·페이로드 모두 불변. 파일 첨부 호출은 Slack API 한계로 색 띠 없음.
set -eu
usage() {
  cat <<'EOF'
사용: slack_notify.sh [--tag 허브] [--title 제목] [--thread TS] [--kind 종류 [--choices N] [--emoji-set 종류]] [--no-check] "메시지" [파일경로]
      slack_notify.sh --kind decide --options '1=문구,2=문구' --recommend N --decision-id D-xxxx [--reversible 쉬움|보통|어려움] [--seq 3/9] "제목·배경"
      slack_notify.sh --kind todo --todo-id T-xxxx "할 일"
  --tag        관련 허브 태그(기록용)
  --title      기록용 제목(기본: 메시지 첫 줄)
  --thread     해당 ts 메시지의 스레드 답글로 게시
  --kind       decide|todo|done|progress|warn|doc. 첫 줄에 종류 이모지가 없으면 붙이고 양식 A 를 검사.
               종류별 색 띠(decide·todo 주황, warn 빨강, progress 파랑, done·doc 초록)에 본문을 담고 첫 줄을 굵게 함(이미 굵으면 그대로)
  --choices    2~4(--kind decide 전용). 메시지 끝에 '👉 답:' 이모지 안내 줄을 붙임(본문에 👉 줄이 있으면 생략)
  --emoji-set  custom(기본, :비서-a: 등)|std(표준 :a: :regional_indicator_c: 등). 안내 줄 이모지 종류
  --options    (--kind decide 전용) 버튼·번호 이모지로 고르는 카드(파일 첨부 시 카드를 먼저 올리고 파일은 그 스레드에 붙임). '번호=문구' 를 쉼표로 이음. 번호는 1부터 연속 2~4개, 문구 1~80자.
               문구에 :이름:·👉·✅·❌·키캡은 못 씀(답 이모지로 오인). --thread 와 함께 못 씀(스레드 답글의 반응은 수집 안 됨).
               선택지 줄(:one: 등)·추천 줄·'👉 답:' 줄·결정 번호 줄을 도구가 붙임. 본문에는 제목과 배경만 씀(본문 '👉 답:' 줄은 거부)
  --recommend  (--options 필수) 추천 선택지 번호
  --decision-id (--options 필수) 결정 번호 D-영문·숫자 1~16자. 태그에서 만들지 않음(결정끼리 겹치면 답이 다른 결정에 붙음).
               최근 7일 안에 같은 번호로 보낸 카드가 있으면 stderr 경고(전송은 함)
  --reversible (--options 선택) 되돌리기 난이도 쉬움|보통|어려움
  --seq        (--options 선택) 순번/전체(예 3/9)
  --todo-id    (--kind todo 전용) 할 일 번호 T-영문·숫자 1~16자. 본문에 답 줄이 없으면 완료 안내 줄을 붙임
  --alive      주제(첫 줄) 아래에 봇 생존 줄('봇: 연결됨 · 마지막 수신 N분 전', bot_alive.py)을 넣음. 정기 점검·진행 메시지용
  --no-check   양식 검사 생략(slack-sent.jsonl 에 unchecked:true 기록)
환경 변수: LRM_BTN_WARN=off 는 '버튼 없음' 경고를 끔(시험용). LRM_FORMAT=warn|strict|off (기본 warn). warn 은 위반해도 전송하고 stderr 경고, strict 는 위반 시 미전송·종료 코드 2
  --kind decide 는 결정이 2개 이상이거나 python3 검사가 실패하면 모드와 무관하게 미전송·종료 코드 2(결정 1건 = 메시지 1개). --no-check·off 로 우회하면 로그에 한 줄 남음
예:
  slack_notify.sh "빌드 완료"
  slack_notify.sh --tag ui-hub "결정1 A/B?" report.html
  slack_notify.sh --thread 1730790000.123456 "반영함"
  slack_notify.sh --kind done "알림 시각 통일"
  slack_notify.sh --kind decide --choices 3 "알림 시각 정하기"
  slack_notify.sh --kind decide --options '1=오전 9시,2=오전 10시' --recommend 1 --decision-id D-0001 --seq 1/3 "알림 시각"
종료 코드: 0 성공, 1 API 실패, 2 설정·인자·양식 오류
EOF
}
die() { echo "$1" >&2; exit 2; }
TAG="" TITLE="" THREAD="" KIND="" NOCHECK=0 ALIVE=0CHOICES="" CHOICES_SET=0 EMOJISET=custom
# 번호 카드 인자. 값 검증은 FORM_PY opts 가 함. *_SET 은 빈 값으로 준 경우도 '준 것'으로 보기 위함
OPTIONS="" OPT_SET=0 RECOMMEND="" REC_SET=0 REVERSIBLE="" REV_SET=0 DECISION_ID="" DID_SET=0 SEQ="" SEQ_SET=0 TODO_ID="" TID_SET=0
while [ $# -gt 0 ]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    --tag) TAG="${2:?--tag 값 필요}"; shift 2 ;;
    --title) TITLE="${2:?--title 값 필요}"; shift 2 ;;
    --thread) THREAD="${2:?--thread 값 필요}"; shift 2 ;;
    --kind) [ $# -ge 2 ] || die "--kind 값 필요 — decide|todo|done|progress|warn|doc 중 하나"; KIND="$2"; shift 2 ;;
    --choices) [ $# -ge 2 ] || die "--choices 값 필요 — 2~4 사이 숫자"; CHOICES="$2" CHOICES_SET=1; shift 2 ;;
    --emoji-set) [ $# -ge 2 ] || die "--emoji-set 값 필요 — custom|std 중 하나"; EMOJISET="$2"; shift 2 ;;
    --options) [ $# -ge 2 ] || die "--options 값 필요 — 예 '1=오전 9시,2=오전 10시'"; OPTIONS="$2" OPT_SET=1; shift 2 ;;
    --recommend) [ $# -ge 2 ] || die "--recommend 값 필요 — 선택지 번호"; RECOMMEND="$2" REC_SET=1; shift 2 ;;
    --reversible) [ $# -ge 2 ] || die "--reversible 값 필요 — 쉬움|보통|어려움"; REVERSIBLE="$2" REV_SET=1; shift 2 ;;
    --decision-id) [ $# -ge 2 ] || die "--decision-id 값 필요 — 예 D-0001"; DECISION_ID="$2" DID_SET=1; shift 2 ;;
    --seq) [ $# -ge 2 ] || die "--seq 값 필요 — 예 3/9"; SEQ="$2" SEQ_SET=1; shift 2 ;;
    --todo-id) [ $# -ge 2 ] || die "--todo-id 값 필요 — 예 T-0001"; TODO_ID="$2" TID_SET=1; shift 2 ;;
    --no-check) NOCHECK=1; shift ;;
    --alive) ALIVE=1; shift ;;
    --) shift; break ;;
    -*) echo "알 수 없는 옵션: $1 — --help 참고" >&2; exit 2 ;;
    *) break ;;
  esac
done
[ $# -ge 1 ] || { usage >&2; exit 2; }
case "$KIND" in
  ""|decide|todo|done|progress|warn|doc) ;;
  *) die "알 수 없는 --kind: $KIND — decide|todo|done|progress|warn|doc 중 하나로 지정" ;;
esac
case "$EMOJISET" in
  custom|std) ;;
  *) die "알 수 없는 --emoji-set: $EMOJISET — custom|std 중 하나로 지정" ;;
esac
if [ "$CHOICES_SET" = 1 ]; then
  case "$CHOICES" in 2|3|4) ;; *) die "--choices 범위 오류: $CHOICES — 2~4 사이 숫자로 지정" ;; esac
  [ "$KIND" = decide ] || die "--choices 는 --kind decide 와만 사용 — --kind decide 를 함께 지정"
fi
if [ "$OPT_SET$REC_SET$REV_SET$DID_SET$SEQ_SET" != 00000 ]; then
  [ "$KIND" = decide ] || die "--options·--recommend·--reversible·--decision-id·--seq 는 --kind decide 와만 사용 — --kind decide 를 함께 지정"
  [ "$OPT_SET" = 1 ] || die "--recommend·--reversible·--decision-id·--seq 는 --options 와 함께 사용 — --options '1=문구,2=문구' 를 지정"
  [ "$CHOICES_SET" = 0 ] || die "--options 와 --choices 는 함께 쓸 수 없음 — 번호 카드는 --options 만 지정"
  [ "$REC_SET" = 1 ] || die "--recommend 필요 — 선택지 번호 중 하나(예 --recommend 1)"
  [ "$DID_SET" = 1 ] || die "--decision-id 필요 — 예 --decision-id D-0001(태그에서 만들지 않음: 결정끼리 번호가 겹칠 수 있음)"
fi
if [ "$TID_SET" = 1 ] && [ "$KIND" != todo ]; then die "--todo-id 는 --kind todo 와만 사용 — --kind todo 를 함께 지정"; fi
# 폴러·대기 목록은 최상위 메시지의 반응만 읽음. 스레드 답글로 나간 카드는 답할 수 없으므로 미전송
if [ -n "$THREAD" ] && { [ "$OPT_SET" = 1 ] || [ "$TID_SET" = 1 ]; }; then die "--thread 는 --options·--todo-id 카드와 함께 쓸 수 없음 — 스레드 답글의 반응은 수집되지 않으므로 --thread 를 빼고 보내세요"; fi
[ "$OPT_SET" = 0 ] || [ $# -lt 2 ] || [ -f "$2" ] || die "첨부 파일 없음: $2 — 경로 확인 뒤 다시 보내세요(카드도 보내지 않음)"
DIR="$HOME/.config/lrm-hubs"
ENV="$DIR/slack-bot.env"
SENT="$DIR/slack-sent.jsonl"
FMTLOG="$DIR/slack-format.log"
[ -f "$ENV" ] || { echo "slack-bot.env 없음 — 사용자 설정 필요" >&2; exit 2; }
. "$ENV"
: "${SLACK_BOT_TOKEN:?}" "${SLACK_USER_ID:?}"
API="${SLACK_API_BASE:-https://slack.com/api}"  # SLACK_API_BASE: 시험용 주소 재정의
MSG="$1"
# 생존 줄은 주제 아래에 둠(첫 줄 40자 검사·알림 미리보기는 주제 그대로). 조회 실패는 전송에 영향 없음
if [ "$ALIVE" = 1 ]; then
  ALIVE_LINE="$(python3 "${LRM_BOT_ALIVE:-$(dirname "$(readlink "$0" || echo "$0")")/bot_alive.py}" 2>/dev/null || true)"
  if [ -n "$ALIVE_LINE" ]; then
    case "$MSG" in
      *$'\n'*) MSG="${MSG%%$'\n'*}"$'\n'"$ALIVE_LINE"$'\n'"${MSG#*$'\n'}" ;;
      *) MSG="$MSG"$'\n'"$ALIVE_LINE" ;;
    esac
  fi
fi

# --kind 종류 이모지. 첫 줄에 6종 중 하나(유니코드·치환 이름 모두)가 있으면 붙이지 않음. 붙인 이모지는 아래 치환표를 그대로 탐
kind_glyph() {
  case "$1" in
    decide) echo '🙋' ;; todo) echo '🛠️' ;; done) echo '✅' ;;
    progress) echo '🔄' ;; warn) echo '⚠️' ;; doc) echo '📄' ;;
  esac
}
has_kind_emoji() {
  local first="${1%%$'\n'*}" t
  for t in 🙋 🛠 ✅ 🔄 ⚠ 📄 :비서-선택해주세요: :raising_hand: :hammer_and_wrench: :비서-추천대로: :white_check_mark: \
    :비서-진행중: :arrows_counterclockwise: :비서-긴급: :warning: :비서-공유합니다: :page_facing_up:; do
    case "$first" in *"$t"*) return 0 ;; esac
  done
  return 1
}
if [ -n "$KIND" ] && ! has_kind_emoji "$MSG"; then MSG="$(kind_glyph "$KIND") $MSG"; fi

# todo 카드의 반응 표는 표준 이름(white_check_mark·x)만 읽음. 본문 ✅·❌ 를 강비서 이름으로 바꾸면 사용자가 누른 반응이 완료·보류로 안 읽히므로 표준 이름으로 둠
OK_NAME=':비서-추천대로:' NO_NAME=':비서-보류:'
if [ "$KIND" = todo ]; then OK_NAME=':white_check_mark:' NO_NAME=':x:'; fi

# 기본 이모지 → 워크스페이스 강비서 이모지
# 줄머리(공백·'•' 불릿 뒤 포함) ✅ 는 완료 표시라 :white_check_mark:, 줄 중간 ✅ 는 반응 선택지라 :비서-추천대로:(todo 는 위 OK_NAME).
# 기존 호출자 불변 원칙의 명시적 예외: --kind 없는 호출에도 줄머리 ✅ 규칙을 적용함
MSG="$(printf '%s' "$MSG" | sed -e 's/🅰️/:비서-a:/g' -e 's/🅱️/:비서-b:/g' -e 's/^\([[:blank:]]*\)✅/\1:white_check_mark:/' -e 's/^\([[:blank:]]*• *\)✅/\1:white_check_mark:/' -e "s/✅/$OK_NAME/g" -e "s/❌/$NO_NAME/g" -e 's/🙋/:비서-선택해주세요:/g' -e 's/📄/:비서-공유합니다:/g' -e 's/🔄/:비서-진행중:/g' -e 's/⚠️/:비서-긴급:/g')"
# 🅲·🅳 는 변형 선택자(VS16) 유무 둘 다 처리. 대응 이모지(:비서-c:·:비서-d:) 존재 미확인이라 --kind 지정 시에만 치환, 기존 호출은 원문 유지
if [ -n "$KIND" ]; then
  MSG="$(printf '%s' "$MSG" | sed -e 's/🅲️/:비서-c:/g' -e 's/🅲/:비서-c:/g' -e 's/🅳️/:비서-d:/g' -e 's/🅳/:비서-d:/g')"
fi

# 양식 A 검사·안내 줄 부착용 파이썬. 모든 정규식은 여기에만 둠. 구간 제거는 길이를 유지해 줄 위치를 보존함
#   check  <종류> <안내줄부착예정 0|1>: "수준<TAB>코드<TAB>메시지" 줄 출력. reject 는 위반(strict 에서 거부), warn 은 경고만, hard 는 모드와 무관하게 거부(decide 다중 결정)
#   attach <안내줄>: 본문에 '👉 답:' 줄이 없으면 마지막 '참고:' 줄 앞(없으면 맨 끝)에 안내 줄을 끼워 출력
#   opts <추천> <되돌리기> <결정번호> <순번> <할일번호>: 표준입력(--options 값)과 함께 검증해 기록용 JSON 출력. 각 인자는 '=값'(줌)|''(안 줌). 틀리면 종료 코드 2
#   card <JSON>: 번호 카드·할 일 줄을 붙여 출력. 본문 '👉 답:' 줄과 --options 가 겹치면 종료 코드 2
#   payload <종류> <채널> <스레드>: 표준입력 본문으로 chat.postMessage JSON 출력(종류가 색 표에 있으면 색 띠)
#   dup <sent 파일> <결정번호>: 최근 7일 안에 같은 결정 번호로 보낸 기록이 있으면 stderr 경고(종료 코드 항상 0)
# 검사 제외 구간: 코드 블록·인라인 코드·URL, 마지막 '참고:' 줄 이후(티켓·커밋 번호 허용, 첫 줄 제외). 결정 개수는 예외로 '참고:' 줄 자체만 빼고 그 뒤도 셈
read -r -d '' FORM_PY <<'PYEOF' || true
import json, re, sys, time, unicodedata

RX = {
    "fence": re.compile(r"```.*?```", re.S),
    "inline": re.compile(r"`[^`\n]*`"),
    "url": re.compile(r"https?://\S+"),
    "ref": re.compile(r"^[ \t]*(?:[•\-*][ \t]*)?참고[ \t]*:", re.M),
    "code": re.compile(r"(?<!\w):[\w+\-]{1,40}:(?!\w)"),
    "ticket": re.compile(r"(?<![0-9A-Za-z_])LRM-\d+(?![0-9A-Za-z_])"),
    "hex": re.compile(r"(?<![0-9A-Za-z_])(?=[0-9a-f]*\d)(?=[0-9a-f]*[a-f])[0-9a-f]{7,40}(?![0-9A-Za-z_])", re.I),
    "bold": re.compile(r"(?<![\w*])\*(?!\s)[^*\n]+?(?<!\s)\*(?![0-9A-Za-z_*])"),
    "answer": re.compile(r"^[ \t]*(?:👉|:point_right:)[ \t]*답[ \t]*:", re.M),
    # 결정 제목 줄 판정(규칙 고정). 줄머리 접두 = 인용 >·불릿 •-*·· 반복(굵게 * 포함)·머리글 #·대괄호 [. 번호 = ①-⑳·1.·1)·(1)·1️⃣·1번
    #   head_a: 접두 뒤 '결정' + [-#]? + 번호(①-⑳ 또는 1~2자리 숫자 + 선택 '번', 뒤가 [).:\]*]·공백·줄 끝). '결정 2026-11-01' 같은 날짜·수량은 제외
    #   head_b: 번호 머리 바로 뒤의 '결정'. 줄 중간의 번호 뒤 '결정'은 선택지 문구('② 결정 변경')라 세지 않음
    #   mid_a : 줄 중간 '결정 N' — 앞이 글자가 아니고 뒤가 [).:*] 일 때만. 확실하지 않은 신호라 거부 없이 경고만
    #   접두의 '*' 는 반복 한 곳에서만 먹음(굵게 '**' 도 포함). 뒤에 '\**' 를 또 두면 겹쳐 시간이 제곱으로 늘어남
    "head_a": re.compile(r"^[ \t]*(?:(?:>|[•\-*·#\[])[ \t]*)*결정[ \t]*[-#]?[ \t]*(?P<n>[①-⑳]|\d{1,2}(?![0-9]))(?:번)?(?=[).:*\]\s]|$)", re.M),
    "head_b": re.compile(r"^[ \t]*(?:(?:>|[•\-*·])[ \t]*)*(?P<n>[①-⑳]|\d{1,2}(?:[.)]|번)|\(\d{1,2}\)|\d{1,2}\uFE0F?\u20E3)[ \t]*\**[ \t]*결정", re.M),
    "mid_a": re.compile(r"(?<![0-9A-Za-z_가-힣])결정[ \t]*[-#]?[ \t]*(?P<n>[①-⑳]|\d{1,2}(?![0-9])(?=[).:*]))"),
    # 번호 카드 인자. 문구 안 쉼표는 뒤에 '숫자=' 가 올 때만 구분자로 봄. 두 자리 번호도 구분자로 봐 범위 오류로 거부(문구에 조용히 합쳐지지 않게)
    "opt_split": re.compile(r",[ \t]*(?=[0-9]+[ \t]*=)"),
    "opt_item": re.compile(r"[ \t]*([0-9]+)[ \t]*=(.*)", re.S),
    # 선택지 문구에 있으면 답 이모지·답 줄로 오인되는 것: :이름:·👉·✅·❌·키캡 숫자
    "opt_fake": re.compile(r"(?<!\w):[\w+\-]{1,40}:(?!\w)|👉|✅|❌|[0-9#*]️?⃣"),
    "decision_id": re.compile(r"D-[0-9A-Za-z]{1,16}", re.A),
    "todo_id": re.compile(r"T-[0-9A-Za-z]{1,16}", re.A),
    "seq": re.compile(r"([0-9]{1,3})/([0-9]{1,3})", re.A),
    "opt_line": re.compile(r"^[ \t]*(?::(?:one|two|three|four):|[1-4]️?⃣)", re.M),
}
# --kind 별 색 띠. 색 값은 여기에만 둠(bash 3.2 는 연관 배열이 없어 파이썬 dict 사용)
KIND_COLOR = {
    "decide": "#F2994A", "todo": "#F2994A",
    "warn": "#EB5757",
    "progress": "#2F80ED",
    "done": "#27AE60", "doc": "#27AE60",
}
# 번호 카드 이모지. 이름은 slack_poll.sh 의 PICK_EMOJI 와 같아야 함
OPT_EMOJI = {1: ":one:", 2: ":two:", 3: ":three:", 4: ":four:"}
OPT_TEXT_MAX = 80
DUP_DAYS = 7  # 같은 결정 번호 재사용을 경고하는 기간
REVERSIBLE = ("쉬움", "보통", "어려움")
# 줄을 새로 만들거나 글 방향을 뒤집어 가짜 선택지·답 줄처럼 보이게 하는 문자. 깨진 UTF-8 이 바뀐 U+FFFD 도 거부
BAD_CATS = ("Cc", "Cs", "Co", "Cn", "Zl", "Zp")
BAD_CHARS = frozenset("‪‫‬‭‮⁦⁧⁨⁩‎‏؜�")
TODO_GUIDE = "👉 답: 끝나면 :white_check_mark: (또는 :hammer_and_wrench:) · 보류는 :x:"

def num_key(token):
    """번호 표기 → 정수. ①→1, '2.'·'(2)'·'2번'·키캡 → 2"""
    if token and "①" <= token[0] <= "⑳":
        return ord(token[0]) - ord("①") + 1
    digits = re.search(r"\d+", token)
    return int(digits.group(0)) if digits else None

def scrub(msg):
    for key in ("fence", "inline", "url"):
        msg = RX[key].sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), msg)
    return msg

def count_decisions(scan):
    """(확실한 결정 수, 줄 중간에서만 보인 다른 번호 수). 수 = max(답 줄 수, 줄머리 결정 번호 종류 수)
    '참고:' 줄 이후는 답 줄만 세고, 표(| 시작) 줄은 번호 판정에서 제외. 같은 번호의 반복 참조는 1개"""
    pos = ref_start(scan)
    body = scan if pos is None else scan[:pos]
    heads, mids = set(), set()
    for ln in body.split("\n"):
        if ln.lstrip().startswith("|"):
            continue
        m = RX["head_a"].match(ln) or RX["head_b"].match(ln)
        if m:
            heads.add(num_key(m.group("n")))
        for x in RX["mid_a"].finditer(ln, m.end() if m else 0):
            mids.add(num_key(x.group("n")))
    return max(len(RX["answer"].findall(scan)), len(heads)), len((heads | mids) - heads)

def ref_start(scan):
    """마지막 '참고:' 줄의 시작 위치. 첫 줄은 주제라 제외, 없으면 None"""
    pos = [m.start() for m in RX["ref"].finditer(scan) if m.start() > 0]
    return pos[-1] if pos else None

def check_form(msg, kind, guided):
    out = []
    msg = unicodedata.normalize("NFC", msg)
    head = RX["code"].sub("", msg.split("\n", 1)[0])
    head = "".join(c for c in head if unicodedata.category(c) not in ("So", "Sk", "Cf", "Mn", "Me") and not c.isspace())
    if len(head) > 40:
        out.append(("reject", "title-len", "첫 줄이 %d자(최대 40) — 주제를 짧게 줄이고 세부는 '• 무엇:' 줄로 옮기세요" % len(head)))
    scan = scrub(msg)
    pos = ref_start(scan)
    body = scan if pos is None else scan[:pos]
    for key, label in (("ticket", "티켓 번호"), ("hex", "커밋 번호로 보이는 값")):
        hits = list(dict.fromkeys(x.group(0) for x in RX[key].finditer(body)))
        if hits:
            out.append(("reject", key, "본문에 %s %s — 맨 아래 '참고:' 줄로 옮기세요" % (label, ", ".join(hits[:3]))))
    if kind in ("decide", "todo") and not guided and not RX["answer"].search(body):
        out.append(("reject", "no-answer", "결정·할 일인데 '👉 답:' 줄이 없음 — '👉 답: …' 줄을 쓰거나 --kind decide --choices N 으로 안내 줄을 붙이세요"))
    if kind == "decide":
        count, extra = count_decisions(scan)
        if count > 1:
            out.append(("hard", "multi", "결정이 %d개임(전송 안 함) — 1건씩 따로 보내세요. 예: --kind decide 로 '결정 1 주제'와 '결정 2 주제'를 각각 한 번씩 호출" % count))
        elif extra > 0:
            out.append(("warn", "mid-decide", "줄 중간에 다른 번호의 '결정 N' 이 있음 — 결정이 2건 이상이면 1건씩 따로 보내세요(참조라면 무시)"))
    # 첫 줄(제목)은 도구가 굵게 하므로 세지 않음
    n = len(RX["bold"].findall(scan.split("\n", 1)[1] if "\n" in scan else ""))
    if n > 1:
        out.append(("warn", "bold", "굵게가 %d곳 — 메시지당 1곳으로 줄이세요" % n))
    return out

def attach_guide(msg, guide):
    scan = scrub(msg)
    pos = ref_start(scan)
    if RX["answer"].search(scan if pos is None else scan[:pos]):
        return msg
    if pos is None:
        return msg.rstrip("\n") + "\n\n" + guide
    return msg[:pos].rstrip("\n") + "\n\n" + guide + "\n\n" + msg[pos:].lstrip("\n")

def bold_title(msg):
    """첫 줄을 *굵게*. 첫 줄에 '*' 가 이미 있으면 중복·깨짐을 막으려 그대로 둠"""
    first, sep, rest = msg.partition("\n")
    if "*" in first or not first.strip():
        return msg
    return "*" + first.strip() + "*" + sep + rest

SECTION_MAX = 3000  # Block Kit section text 상한
BUTTON_TEXT_MAX = 75  # 버튼 글자 상한

def buttons(spec):
    """번호 카드 → actions 블록. 추천은 primary, 끝에 '직접 입력'. value 는 '결정번호|번호'(slack_socket.py 가 해석)"""
    did, rec = spec["decision_id"], spec["recommended"]
    els = []
    for n in sorted(int(k) for k in spec["options"]):
        text = "%d. %s" % (n, spec["options"][str(n)])
        b = {"type": "button", "action_id": "pick_%d" % n, "value": "%s|%d" % (did, n),
             "text": {"type": "plain_text", "text": text[:BUTTON_TEXT_MAX], "emoji": True}}
        if n == rec:
            b["style"] = "primary"
        els.append(b)
    els.append({"type": "button", "action_id": "custom", "value": "%s|custom" % did,
                "text": {"type": "plain_text", "text": "직접 입력", "emoji": True}})
    return {"type": "actions", "block_id": "lrm_decide:%s" % did, "elements": els}

def build_payload(msg, kind, channel, thread, card=""):
    """chat.postMessage 본문 JSON. kind 가 색 표에 있으면 attachment 색 띠에 본문을 담고, 알림 미리보기용 text 에는 제목만 둠.
    번호 카드(card 에 options)면 색 띠 안을 section + 버튼 블록으로 바꿈. 이모지 답 줄은 버튼이 안 될 때를 위해 남김"""
    body = {"channel": channel, "text": msg}
    spec = json.loads(card) if card else {}
    if kind in KIND_COLOR:
        title = msg.split("\n", 1)[0].replace("*", "").strip()
        body["text"] = title
        att = {"color": KIND_COLOR[kind], "text": bold_title(msg), "fallback": title, "mrkdwn_in": ["text"]}
        if "options" in spec:
            att = {"color": KIND_COLOR[kind], "fallback": title, "blocks": [
                {"type": "section", "text": {"type": "mrkdwn", "text": bold_title(msg)[:SECTION_MAX]}}, buttons(spec)]}
        body["attachments"] = [att]
    if thread:
        body["thread_ts"] = thread
    return json.dumps(body)

def given(arg):
    """bash 가 '=값'(줌) 또는 ''(안 줌)으로 넘긴 인자. 안 줬으면 None"""
    return arg[1:] if arg.startswith("=") else None

def parse_card(raw, rec, rev, did, seq, tid):
    """번호 카드·할 일 번호 인자를 검증해 보낸 기록에 더할 dict 반환. 틀리면 ValueError(원인 — 조치)"""
    rec, rev, did, seq, tid = map(given, (rec, rev, did, seq, tid))
    if tid is not None:
        if not RX["todo_id"].fullmatch(tid):
            raise ValueError("--todo-id 형식 오류: T-영문·숫자 1~16자 — 예 T-0001")
        return {"todo_id": tid}
    opts = {}
    for part in RX["opt_split"].split(unicodedata.normalize("NFC", raw)):
        m = RX["opt_item"].fullmatch(part)
        if not m:
            raise ValueError("--options 형식 오류 — '1=문구,2=문구' 처럼 번호=문구 를 쉼표로 이어 쓰세요")
        n, text = m.group(1), m.group(2).strip(" \t")
        if n in opts:
            raise ValueError("--options 번호 중복: %s — 번호마다 한 번씩만 쓰세요" % n)
        if any(unicodedata.category(c) in BAD_CATS or c in BAD_CHARS for c in text):
            raise ValueError("--options 문구에 줄바꿈·제어 문자가 있음(%s번) — 한 줄 문구로 쓰세요" % n)
        if RX["opt_fake"].search(text):
            raise ValueError("--options 문구에 이모지 이름(:이름:)·👉·✅·❌·키캡이 있음(%s번) — 답 이모지·답 줄로 오인되므로 글자로만 쓰세요" % n)
        if not text:
            raise ValueError("--options 문구 없음(%s번) — 번호마다 문구를 쓰세요" % n)
        if len(text) > OPT_TEXT_MAX:
            raise ValueError("--options 문구가 %d자(%s번, 최대 %d) — 줄이세요" % (len(text), n, OPT_TEXT_MAX))
        opts[n] = text
    if not 2 <= len(opts) <= 4 or set(opts) != {str(i) for i in range(1, len(opts) + 1)}:
        raise ValueError("--options 범위 오류: 번호는 1부터 빠짐없이 2~4개 — 예 '1=가,2=나,3=다'")
    if rec not in opts:
        raise ValueError("--recommend 범위 오류: %s — 선택지 번호(1~%d) 중 하나로 지정" % (rec, len(opts)))
    if not RX["decision_id"].fullmatch(did or ""):
        raise ValueError("--decision-id 형식 오류: D-영문·숫자 1~16자 — 예 D-0001")
    out = {"decision_id": did, "options": opts, "recommended": int(rec)}
    if rev is not None:
        if rev not in REVERSIBLE:
            raise ValueError("--reversible 값 오류: %s — 쉬움|보통|어려움 중 하나" % rev)
        out["reversible"] = rev
    if seq is not None:
        m = RX["seq"].fullmatch(seq)
        if not m or not 1 <= int(m.group(1)) <= int(m.group(2)):
            raise ValueError("--seq 형식 오류: 순번/전체(1 ≤ 순번 ≤ 전체 ≤ 999) — 예 3/9")
        out["seq"] = seq
    return out

def build_card(msg, spec):
    """검사를 마친 본문에 카드 줄을 붙여 (본문, 경고 목록) 반환. 카드의 답 줄은 1개여야 해서 본문 답 줄은 ValueError.
    선택지·추천·답 줄은 마지막 '참고:' 줄 앞(없으면 끝). 번호 줄은 메시지 맨 끝 = 참고 블록 끝('참고:' 줄 뒤에 다른 줄이 더 있어도 그 뒤. 없으면 '참고:' 줄을 새로 만듦)"""
    scan = scrub(msg)
    pos = ref_start(scan)
    has_answer = RX["answer"].search(scan)
    warns, block = [], []
    if "options" in spec:
        if has_answer:
            raise ValueError("거부: --options 카드 본문에 '👉 답:' 줄이 있음(전송 안 함) — 답 줄은 도구가 만듦. 본문에는 제목과 배경만 쓰세요")
        if RX["opt_line"].search(scan if pos is None else scan[:pos]):
            warns.append("경고: 본문 줄 머리에 선택지 이모지(:one: 등)가 있음 — --options 가 선택지 줄을 만들므로 본문에는 배경만 쓰세요")
        nums = sorted(int(k) for k in spec["options"])
        block = ["%s %s" % (OPT_EMOJI[n], spec["options"][str(n)]) for n in nums]
        rec = ":star: 추천: %s" % OPT_EMOJI[spec["recommended"]]
        block.append(rec + (" · 되돌리기: %s" % spec["reversible"] if "reversible" in spec else ""))
        block.append("👉 답: %s 중 하나 · :white_check_mark: 추천대로 · :x: 보류" % " ".join(OPT_EMOJI[n] for n in nums))
        ref = "결정 %s" % spec["decision_id"] + (" · %s" % spec["seq"] if "seq" in spec else "")
    else:
        if not has_answer:
            block = [TODO_GUIDE]
        ref = "할 일 %s" % spec["todo_id"]
    out = msg
    if block:
        guide = "\n".join(block)
        out = msg.rstrip("\n") + "\n\n" + guide if pos is None else msg[:pos].rstrip("\n") + "\n\n" + guide + "\n\n" + msg[pos:].lstrip("\n")
    return out.rstrip("\n") + ("\n\n참고: " + ref if pos is None else "\n" + ref), warns

def recent_dup(path, did):
    """최근 DUP_DAYS 일 안에 같은 결정 번호로 보낸 기록 수. 파일이 없거나 깨진 줄은 무시"""
    since, n = time.time() - DUP_DAYS * 86400, 0
    try:
        with open(path, encoding="utf-8") as f:
            for ln in f:
                try:
                    r = json.loads(ln)
                except ValueError:
                    continue
                at = r.get("at") if isinstance(r, dict) else None
                if r.get("decision_id") == did and isinstance(at, (int, float)) and not isinstance(at, bool) and at >= since:
                    n += 1
    except OSError:
        pass
    return n

data = sys.stdin.buffer.read().decode("utf-8", "replace")
if sys.argv[1] == "dup":
    if recent_dup(sys.argv[2], sys.argv[3]):
        sys.stderr.buffer.write(("경고: --decision-id %s 로 최근 %d일 안에 보낸 카드가 있음(전송함) — 메인 전달문 '결정 %s = …' 이 어느 카드의 답인지 구분되지 않으니 번호를 새로 정하세요\n" % (sys.argv[3], DUP_DAYS, sys.argv[3])).encode("utf-8"))
elif sys.argv[1] == "check":
    for lvl, code, text in check_form(data, sys.argv[2], sys.argv[3] == "1"):
        sys.stdout.buffer.write(("%s\t%s\t%s\n" % (lvl, code, text)).encode("utf-8"))
elif sys.argv[1] == "payload":
    sys.stdout.buffer.write(build_payload(data, *sys.argv[2:6]).encode("utf-8"))
elif sys.argv[1] in ("opts", "card"):
    try:
        if sys.argv[1] == "opts":
            out, warns = json.dumps(parse_card(data, *sys.argv[2:7]), ensure_ascii=False), []
        else:
            out, warns = build_card(data, json.loads(sys.argv[2]))
    except ValueError as e:
        sys.stderr.buffer.write(("%s\n" % e).encode("utf-8"))
        sys.exit(2)
    sys.stderr.buffer.write("".join(w + "\n" for w in warns).encode("utf-8"))
    sys.stdout.buffer.write(out.encode("utf-8"))
else:
    sys.stdout.buffer.write(attach_guide(data, sys.argv[2]).encode("utf-8"))
PYEOF
check_form() { printf '%s' "$1" | python3 -c "$FORM_PY" check "$2" "$3"; }
attach_guide() { printf '%s' "$1" | python3 -c "$FORM_PY" attach "$2"; }
setv() { if [ "$1" = 1 ]; then printf '=%s' "$2"; fi; }  # opts 인자: 준 값은 '=값', 안 준 값은 ''

# 번호 카드·할 일 번호 검증. 틀리거나 검사가 실패하면 모드·우회와 무관하게 미전송(카드 줄 없이 보내면 답을 해석할 수 없음)
CARD_JSON=""
if [ "$OPT_SET" = 1 ] || [ "$TID_SET" = 1 ]; then
  CARD_JSON="$(printf '%s' "$OPTIONS" | python3 -c "$FORM_PY" opts "$(setv "$REC_SET" "$RECOMMEND")" "$(setv "$REV_SET" "$REVERSIBLE")" \
    "$(setv "$DID_SET" "$DECISION_ID")" "$(setv "$SEQ_SET" "$SEQ")" "$(setv "$TID_SET" "$TODO_ID")")" \
    || { rc=$?; [ "$rc" = 2 ] || echo "카드 인자 검사 실패(전송 안 함) — python3 동작 확인 뒤 다시 보내세요" >&2; exit 2; }
fi
# 버튼은 --options 카드에만 붙음. --options 없는 결정 카드는 이모지 반응뿐이라 사용자가 스레드에 글로 답하게 됨
if [ "$KIND" = decide ] && [ "$OPT_SET" = 0 ] && [ "${LRM_BTN_WARN:-on}" != off ]; then
  echo "경고: 선택지 버튼 없음(전송함) — --options '1=문구,2=문구' --recommend N --decision-id D-xxxx 로 보내야 버튼이 붙음(지금은 이모지 반응·스레드 글로만 답 가능)" >&2
fi
# 같은 결정 번호 재사용은 경고만(재전송일 수 있음). 검사 실패·기록 없음은 전송에 영향 없음
if [ "$OPT_SET" = 1 ]; then python3 -c "$FORM_PY" dup "$SENT" "$DECISION_ID" < /dev/null || true; fi
GUIDED=0  # 답 줄을 도구가 붙이면 no-answer 검사 생략
if [ "$CHOICES_SET" = 1 ] || [ -n "$CARD_JSON" ]; then GUIDED=1; fi

# 결정 메시지 끝의 이모지 답 안내 줄. --choices N 만큼 A.. 나열, 추천대로·보류는 항상 붙임. 이름은 slack_poll.sh 반응표와 일치해야 함
guide_line() {
  local a b c d ok no g
  if [ "$EMOJISET" = std ]; then
    a=:a: b=:b: c=:regional_indicator_c: d=:regional_indicator_d: ok=:white_check_mark: no=:x:
  else
    a=:비서-a: b=:비서-b: c=:비서-c: d=:비서-d: ok=:비서-추천대로: no=:비서-보류:
  fi
  g="👉 답: $a A · $b B"
  if [ "$CHOICES" -ge 3 ]; then g="$g · $c C"; fi
  if [ "$CHOICES" -ge 4 ]; then g="$g · $d D"; fi
  printf '%s' "$g · $ok 추천대로 · $no 보류"
}

# 집계 로그 1줄(시각, 종류, 위반 코드 또는 ok, 모드, 태그). 본문·제목은 기록하지 않음. 쓰기 실패는 전송에 영향 없음
log_format() {
  local tag="${TAG:--}"; tag="${tag//[$'\t\r\n']/ }"; tag="$(printf '%s' "$tag" | LC_ALL=C tr -d '\000-\010\013-\037\177')"; tag="${tag:0:40}"
  ( umask 077; printf '%s\t%s\t%s\t%s\t%s\n' "$(date '+%Y-%m-%dT%H:%M:%S%z')" "$1" "$2" "$3" "$tag" >> "$FMTLOG" ) 2>/dev/null || true
  chmod 600 "$FMTLOG" 2>/dev/null || true
}

if [ -n "$KIND" ] && [ "$NOCHECK" = 0 ]; then
  MODE="${LRM_FORMAT:-warn}"
  case "$MODE" in warn|strict|off) ;; *) die "LRM_FORMAT 값 오류: $MODE — warn|strict|off 중 하나로 지정" ;; esac
  if [ "$MODE" != off ]; then
    FORM_N=0 HARD_N=0 FORM_CODES="" FORM_OUT="" HARD_TXT=""
    if ! FORM_OUT="$(check_form "$MSG" "$KIND" "$GUIDED")"; then
      # decide 는 다중 결정 검사를 건너뛰면 안 되므로 warn 이어도 미전송. 다른 kind 는 warn 에서 검사 없이 전송
      if [ "$MODE" = strict ] || [ "$KIND" = decide ]; then
        log_format "$KIND" check-fail "$MODE"
        die "양식 검사 실패(전송 안 함) — python3 동작 확인 뒤 다시 보내거나 --no-check 로 우회"
      fi
      echo "양식 검사 실패 — 검사 없이 전송" >&2
      FORM_OUT="" FORM_CODES="check-fail"
    fi
    while IFS=$'\t' read -r lvl code text; do
      [ -n "$code" ] || continue
      FORM_CODES="$FORM_CODES${FORM_CODES:+,}$code"
      if [ "$lvl" = reject ]; then FORM_N=$((FORM_N + 1)); fi
      if [ "$lvl" = hard ]; then HARD_N=$((HARD_N + 1)); fi
      if [ "$lvl" = hard ]; then HARD_TXT="$HARD_TXT${HARD_TXT:+$'\n'}거부: $text"  # 다른 위반 뒤에 마지막 줄로 출력
      elif [ "$lvl" = reject ] && [ "$MODE" = strict ]; then echo "거부: $text" >&2
      else echo "경고: $text" >&2; fi
    done <<< "$FORM_OUT"
    [ -z "$HARD_TXT" ] || echo "$HARD_TXT" >&2
    log_format "$KIND" "${FORM_CODES:-ok}" "$MODE"
    [ "$HARD_N" -eq 0 ] || exit 2  # 결정 다중은 warn 이어도 미전송
    if [ "$FORM_N" -gt 0 ]; then
      if [ "$MODE" = strict ]; then echo "양식 위반 ${FORM_N}건(전송 안 함) — 고치거나 --no-check 로 우회" >&2; exit 2; fi
      echo "양식 위반 ${FORM_N}건(전송함)" >&2
    fi
  fi
fi

# 우회 기록: decide 의 --no-check·LRM_FORMAT=off 는 허용하되 한 줄 남김(본문 없음). 다중 결정 검사를 건너뛴 흔적용
if [ "$KIND" = decide ]; then
  if [ "$NOCHECK" = 1 ]; then log_format "$KIND" bypass-no-check no-check
  elif [ "${LRM_FORMAT:-warn}" = off ]; then log_format "$KIND" bypass-off off; fi
fi

# 검사 뒤에 안내 줄 부착. 치환을 이미 마쳤으므로 안내 줄의 이모지 이름은 다시 치환되지 않음
if [ "$CHOICES_SET" = 1 ]; then
  if GUIDED="$(attach_guide "$MSG" "$(guide_line)")"; then
    MSG="$GUIDED"
  else
    [ "${LRM_FORMAT:-warn}" != strict ] || die "안내 줄 부착 실패(전송 안 함) — python3 동작 확인 뒤 다시 보내거나 --choices 없이 '👉 답:' 줄을 직접 작성"
    echo "안내 줄 부착 실패 — 안내 줄 없이 전송" >&2
  fi
fi
# 번호 카드·할 일 줄도 검사 뒤에 부착(선택지 문구의 '결정'을 결정 개수로 세지 않음). 실패하면 미전송
if [ -n "$CARD_JSON" ]; then
  CARDED="$(printf '%s' "$MSG" | python3 -c "$FORM_PY" card "$CARD_JSON")" \
    || { rc=$?; [ "$rc" = 2 ] || echo "카드 줄 부착 실패(전송 안 함) — python3 동작 확인 뒤 다시 보내세요" >&2; exit 2; }
  MSG="$CARDED"
fi

[ -n "$TITLE" ] || TITLE="${MSG%%$'\n'*}"
CH=$(curl -s -H "Authorization: Bearer $SLACK_BOT_TOKEN" -d "users=$SLACK_USER_ID" $API/conversations.open | python3 -c 'import sys,json;print(json.load(sys.stdin)["channel"]["id"])')

# 응답 JSON 을 받아 ok/에러 출력 + 성공 시 sent 기록. ts 는 postMessage 만 제공, 파일 업로드는 file_id 로 남김.
# form_kind(--kind 지정 시), unchecked(--no-check 시), 카드 필드(decision_id·options·recommended·reversible·seq 또는 todo_id)는
# 해당할 때만 기록해 기존 레코드 모양을 유지함. 카드 필드가 없는 기록은 폴러가 옵션 없는 옛 카드로 읽음
record() {
  python3 -c '
import sys,json,time,os
d=json.load(sys.stdin); sent,ch,title,tag,thread,kind,fkind,unchecked,card=sys.argv[1:10]
if not d.get("ok"):
    print(d.get("error")); sys.exit(1)
rec={"at":int(time.time()),"channel":ch,"ts":d.get("ts",""),"thread_ts":thread,"kind":kind,"title":title[:200],"tag":tag}
if fkind: rec["form_kind"]=fkind
if unchecked=="1": rec["unchecked"]=True
if card: rec.update(json.loads(card))
if kind=="file":
    rec["file_id"]=(d.get("files") or [{}])[0].get("id","")
    rec["path"]=os.environ.get("LRM_FILE_PATH","")
with open(sent,"a",encoding="utf-8") as f: f.write(json.dumps(rec,ensure_ascii=False)+"\n")
print("ok", rec["ts"])
' "$SENT" "$CH" "$TITLE" "$TAG" "$THREAD" "$1" "$KIND" "$NOCHECK" "${2-$CARD_JSON}"
}

# 텍스트 게시. 번호 카드면 색 띠 안에 버튼 블록이 들어감
post_text() {
  PAYLOAD="$(printf '%s' "$MSG" | python3 -c "$FORM_PY" payload "$KIND" "$CH" "$THREAD" "$CARD_JSON")" \
    || die "페이로드 생성 실패(전송 안 함) — python3 동작 확인 뒤 다시 보내세요"
  curl -s -H "Authorization: Bearer $SLACK_BOT_TOKEN" -H 'Content-Type: application/json' \
    -d "$PAYLOAD" $API/chat.postMessage | record text
}
# 파일 업로드. 인자: 경로, 본문, 스레드 ts, 기록에 붙일 카드 필드(카드 본체가 따로 있으면 빈 값)
upload_file() {
  local F="$1" BODY="$2" TH="$3" REC="$4" R U ID N L
  export LRM_FILE_PATH="$F"; N=$(basename "$F"); L=$(stat -f %z "$F")
  R=$(curl -s -H "Authorization: Bearer $SLACK_BOT_TOKEN" --data-urlencode "filename=$N" -d "length=$L" $API/files.getUploadURLExternal)
  U=$(echo "$R" | python3 -c 'import sys,json;print(json.load(sys.stdin)["upload_url"])'); ID=$(echo "$R" | python3 -c 'import sys,json;print(json.load(sys.stdin)["file_id"])')
  curl -s -o /dev/null -F "file=@$F" "$U"
  curl -s -H "Authorization: Bearer $SLACK_BOT_TOKEN" -H 'Content-Type: application/json' \
    -d "$(python3 -c '
import json,sys
b={"files":[{"id":sys.argv[1],"title":sys.argv[2]}],"channel_id":sys.argv[3],"initial_comment":sys.argv[4]}
if sys.argv[5]: b["thread_ts"]=sys.argv[5]
print(json.dumps(b))' "$ID" "$N" "$CH" "$BODY" "$TH")" \
    $API/files.completeUploadExternal | record file "$REC"
}

if [ $# -ge 2 ] && [ "$OPT_SET" = 1 ]; then
  # 파일 업로드 메시지에는 버튼 블록을 달 수 없음(Slack API 한계). 버튼 카드를 먼저 올리고 파일은 그 스레드에 붙임.
  # 카드가 최상위 메시지라 버튼·반응·스레드 글 답이 모두 카드 ts 에 묶임
  OUT="$(post_text)" || { echo "$OUT"; exit 1; }
  echo "$OUT"
  case "$OUT" in "ok "?*) ;; *) exit 1 ;; esac
  FOUT="$(upload_file "$2" "첨부: $(basename "$2")" "${OUT#ok }" "")" || true
  case "$FOUT" in ok*) ;; *) echo "경고: 카드는 보냈지만 파일 첨부 실패(${FOUT:-응답 없음}) — 파일을 따로 보내세요" >&2 ;; esac
elif [ $# -ge 2 ]; then
  upload_file "$2" "$MSG" "$THREAD" "$CARD_JSON"
else
  post_text
fi
