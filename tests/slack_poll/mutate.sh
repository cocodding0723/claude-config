#!/usr/bin/env bash
# 슬랙 반응·전달 변이 검증: 스크립트 사본에 결함을 하나씩 심고 해당 시험이 실패하는지 확인함.
# 원본 작업 트리는 바꾸지 않고 임시 사본(mktemp -d)에서만 실행. ONLY="S1 S3" 처럼 일부만 실행 가능. 검출 못 한 변이가 있으면 종료 코드 1.
set -uo pipefail
export PYTHONDONTWRITEBYTECODE=1

REPO=$(cd "$(dirname "$0")/../.." && pwd -P)
WORK=$(mktemp -d "${TMPDIR:-/tmp}/cc-slackmut.XXXXXX")
trap 'rm -rf "${WORK:?}"' EXIT
MISSED=0

fresh_copy() {
  rm -rf "${WORK:?}/repo"
  mkdir -p "$WORK/repo/hubs" "$WORK/repo/tests"
  cp -R "$REPO/hubs/scripts" "$WORK/repo/hubs/"
  cp -R "$REPO/tests/slack_poll" "$REPO/tests/slack_notify" "$WORK/repo/tests/"
}

case_run() { # 번호 설명 시험(react|push|decide|notify) 파일 perl 치환식. decide.sh 는 push·pending 전달문·표시도, notify 는 tests/slack_notify/run.sh
  local id="$1" desc="$2" suite="$3" file="$4" expr="$5" out rc first runner
  if [ -n "${ONLY:-}" ] && [[ " $ONLY " != *" $id "* ]]; then return; fi
  fresh_copy
  cp "$WORK/repo/$file" "$WORK/before"
  perl -0pi -e "$expr" "$WORK/repo/$file"
  if cmp -s "$WORK/before" "$WORK/repo/$file"; then printf '| %s | %s | 정의 오류 | - |\n' "$id" "$desc"; MISSED=$((MISSED + 1)); return; fi
  runner="tests/slack_poll/$suite.sh"; [ "$suite" = notify ] && runner="tests/slack_notify/run.sh"
  out=$(bash "$WORK/repo/$runner" 2>&1); rc=$?
  first=$(echo "$out" | grep '^FAIL' | head -1 | sed 's/^FAIL  //' | cut -c1-70)
  if [ "$rc" != 0 ]; then printf '| %s | %s | 검출 (%s.sh) | %s |\n' "$id" "$desc" "$suite" "$first"
  else printf '| %s | %s | **미검출** | - |\n' "$id" "$desc"; MISSED=$((MISSED + 1)); fi
}

echo '| 변이 | 내용 | 결과 | 먼저 실패한 검사 |'
echo '|---|---|---|---|'
P=hubs/scripts/slack_poll.sh U=hubs/scripts/slack_push.py PD=hubs/scripts/slack_pending.sh N=hubs/scripts/slack_notify.sh
case_run S1 "시안 매핑 키 바꿈(❌ fail→hold)" react "$P" 's/"x": "fail"/"x": "hold"/'
case_run S2 "kind 분기 제거(항상 결정 표)" react "$P" 's/return \(REVIEW_ACTION if kind == REVIEW else REACTION_ACTION\)/return (REACTION_ACTION)/'
case_run S3 "첨부 결정 카드 예외 제거('결정' 검사)" react "$P" 's/ and "결정" not in str\(rec.get\("title"\) or ""\)//'
case_run S4 "반응 확인 완료 표식을 ✅ 로" react "$P" 's/RX_ON_DONE = "ballot_box_with_check"/RX_ON_DONE = "white_check_mark"/'
case_run S5 "thread_broadcast 답글 제외" react "$P" 's/REPLY_SUBTYPES = USER_SUBTYPES \+ \("thread_broadcast",\)/REPLY_SUBTYPES = USER_SUBTYPES/'
case_run S6 "push 반응 필터 제거(반응 전달 안 함)" push "$U" 's/return d.get\("type"\) == REACT_TYPE and bool\(d.get\("parent_ts"\)\) and n > floor/return False/'
case_run S7 "옛 반응 판정 제거(옛 반응이 표식을 안 막음)" push "$U" 's/return d is not None and d.get\("type"\) == REACT_TYPE and n <= floor/return False/'
case_run S8 "전달 기록 스레드 건수에 반응 포함" push "$U" 's/        if it.get\("type"\) == REACT_TYPE:  # 확인 답장은[^\n]*\n            continue\n//'
case_run S9 "broadcast 수집 경계 제거(옛 broadcast 답글 재생)" react "$P" 's/\n\s*or \(rp.get\("subtype"\) == "thread_broadcast" and float\(rp\["ts"\]\) < bcast_since\)//'
case_run S10 "reacted_on 30일 정리 제거" react "$P" 's/if v\["at"\] < now - RX_KEEP\]/if False]/'
case_run S11 "첨부 판별을 ts 기준으로(글 메시지도 review)" react "$P" 's/if rec.get\("file_id"\) and "결정"/if rec.get("ts") and "결정"/'
case_run S12 "전달문 이모지 정제 제거" push "$U" 's/one_line\(it.get\(.emoji.\), 40\)/it.get("emoji")/'
case_run S13 "전달문 tag 정제 제거" push "$U" 's/tag = one_line\(it.get\("tag"\) or "", 30\)/tag = str(it.get("tag") or "")/'
case_run S14 "번호 이모지 → pick 오프바이원" decide "$P" 's/PICK_EMOJI = \{"one": 1, "two": 2, "three": 3, "four": 4\}/PICK_EMOJI = {"one": 2, "two": 3, "three": 4, "four": 5}/'
case_run S15 "✅ 를 추천 번호 대신 1 로 고정" decide "$P" 's/out.update\(action="approve", pick=best\)/out.update(action="approve", pick=1)/'
case_run S16 "선택지 수 범위 검사 삭제" decide "$P" 's/if PICK_EMOJI\[name\] not in opts:/if False:/'
case_run S17 "할 일 kind 분기 제거(결정 표로 해석)" decide "$P" 's/    if kind == TODO:\n/    if False:\n/'
case_run S18 "충돌 표시 제거" decide "$P" 's/    if len\(keys\) > 1:\n/    if False:\n/'
case_run S19 "옵션 기록 모양 검사 삭제(목록·추천 범위 밖도 해석)" decide "$P" 's/    except ValueError:\n        return \{"action": name, "kind": DECIDE\}, "options"\n/    except ValueError:\n        did, opts, best = rec.get("decision_id"), dict(enumerate(rec.get("options") or [], 1)), rec.get("recommended")\n/'
case_run S20 "전달문 충돌 표시 제거(충돌도 선택 N 으로)" decide "$U" 's/    if it.get\("conflict"\) is True:  #/    if False:  #/'
case_run S21 "전달문 추천대로를 선택 문장으로" decide "$U" 's/return f"결정 \{did\} = 추천대로\(선택 \{n\}\)" if act == "approve" else None/return f"결정 {did} = 선택 {n}"/'
case_run S22 "전달문 pick 범위 검사 삭제" decide "$U" 's/not isinstance\(n, int\) or isinstance\(n, bool\) or not 1 <= n <= 4/n is None/'
case_run S23 "대기 목록: 보류도 닫힘" decide "$PD" 's/        return "hold", "보류"/        return "closed", "보류"/'
case_run S24 "대기 목록: 번호 카드도 아무 반응이나 닫힘" decide "$PD" 's/done = \{t for t in reacted if t not in cards\}/done = set(reacted)/'
case_run S25 "본인 반응 필터 제거(남의·봇 반응도 수집)" decide "$P" 's/            if ME in r\.get\("users", \[\]\):/            if True:/'
case_run S26 "notify: --thread 와 번호 카드 거부 제거" notify "$N" 's/if \[ -n "\$THREAD" \] && \{ \[ "\$OPT_SET" = 1 \] \|\| \[ "\$TID_SET" = 1 \]; \}; then die/if false; then die/'
case_run S27 "notify: todo 카드 ✅·❌ 표준 이름 치환 제거" notify "$N" 's/if \[ "\$KIND" = todo \]; then OK_NAME=/if false; then OK_NAME=/'
case_run S28 "notify: todo 카드 ❌ 만 강비서 이름으로" notify "$N" 's/NO_NAME=.:x:.; fi/NO_NAME=":비서-보류:"; fi/'
case_run S29 "notify: 두 자리 번호를 구분자로 안 봄" notify "$N" 's/,\[ \\t\]\*\(\?=\[0-9\]\+/,[ \\t]*(?=[0-9]/'
case_run S30 "notify: 문구 속 이모지 이름·답 줄 기호 거부 제거" notify "$N" 's/if RX\["opt_fake"\]\.search\(text\):/if False:/'
case_run S31 "notify: 결정 번호 재사용 경고 제거" notify "$N" 's/if recent_dup\(sys\.argv\[2\], sys\.argv\[3\]\):/if False:/'
case_run S32 "대기 목록: 결정 번호 형식 검사 제거" decide "$PD" 's/\n             and isinstance\(s\.get\("decision_id"\), str\) and DECISION_ID_RE\.fullmatch\(s\["decision_id"\]\)\}/}/'
case_run S33 "대기 목록: 손상 번호 카드를 목록에서 뺌" decide "$PD" 's/ or \(s\.get\("form_kind"\) == "decide" and "options" in s\)\)\]/)]/'
echo
[ "$MISSED" = 0 ] && echo "변이 전부 검출" || echo "미검출·정의 오류 ${MISSED}건"
[ "$MISSED" = 0 ]
