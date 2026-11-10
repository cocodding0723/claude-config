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
  cp -R "$REPO/tests/slack_poll" "$WORK/repo/tests/"
}

case_run() { # 번호 설명 시험(react|push|decide) 파일 perl 치환식
  local id="$1" desc="$2" suite="$3" file="$4" expr="$5" out rc first
  if [ -n "${ONLY:-}" ] && [[ " $ONLY " != *" $id "* ]]; then return; fi
  fresh_copy
  cp "$WORK/repo/$file" "$WORK/before"
  perl -0pi -e "$expr" "$WORK/repo/$file"
  if cmp -s "$WORK/before" "$WORK/repo/$file"; then printf '| %s | %s | 정의 오류 | - |\n' "$id" "$desc"; MISSED=$((MISSED + 1)); return; fi
  out=$(bash "$WORK/repo/tests/slack_poll/$suite.sh" 2>&1); rc=$?
  first=$(echo "$out" | grep '^FAIL' | head -1 | sed 's/^FAIL  //' | cut -c1-70)
  if [ "$rc" != 0 ]; then printf '| %s | %s | 검출 (%s.sh) | %s |\n' "$id" "$desc" "$suite" "$first"
  else printf '| %s | %s | **미검출** | - |\n' "$id" "$desc"; MISSED=$((MISSED + 1)); fi
}

echo '| 변이 | 내용 | 결과 | 먼저 실패한 검사 |'
echo '|---|---|---|---|'
P=hubs/scripts/slack_poll.sh U=hubs/scripts/slack_push.py
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
case_run S16 "선택지 수 범위 검사 삭제" decide "$P" 's/if PICK_EMOJI\[name\] > len\(opts\):/if False:/'
case_run S17 "할 일 kind 분기 제거(결정 표로 해석)" decide "$P" 's/    if kind == TODO:\n/    if False:\n/'
case_run S18 "충돌 표시 제거" decide "$P" 's/    if len\(keys\) > 1:\n/    if False:\n/'
case_run S19 "옵션 기록 모양 검사 삭제(목록·추천 범위 밖도 해석)" decide "$P" 's/    except ValueError:\n        return \{"action": name, "kind": DECIDE\}, "options"\n/    except ValueError:\n        did, opts, best = rec.get("decision_id"), dict(enumerate(rec.get("options") or [], 1)), rec.get("recommended")\n/'
echo
[ "$MISSED" = 0 ] && echo "변이 전부 검출" || echo "미검출·정의 오류 ${MISSED}건"
[ "$MISSED" = 0 ]
