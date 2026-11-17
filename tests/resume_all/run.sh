#!/usr/bin/env bash
# 휴면 허브 표식 시험(resume_all.sh·hubs.tsv). hub_status 시험 준비(임시 폴더·가짜 orca)를 재사용함. 실제 허브를 띄우지 않음.
set -uo pipefail
source "$(dirname "$0")/../hub_status/lib.sh"
SCR="$REPO/hubs/scripts"

D="$SB/dorm.tsv"
printf '# slug\tname\trole\tws\trepo\tparent\nalpha\t알파\t~/r/role-a.txt\t-\t-\nbeta\t베타\t~/r/role-b.txt\t-\t-\ngamma\t감마\t~/r/role-g.txt\t-\t-\n#dormant\tbeta\t기능 팀으로 대체\n' > "$D"
export HUBS_TSV="$D"

OUT=$(bash "$RA" --dry-run 2>&1); RC=$?
chk "dry-run: 종료 코드 0" '[ "$RC" = 0 ]' "rc=$RC"
chk "휴면 허브는 기동 명령 없이 건너뜀 + 사유·깨우기 안내" 'printf "%s" "$OUT" | grep -q "== 베타: 휴면(기능 팀으로 대체), 건너뜀 — 깨우기: resume_all.sh --wake beta" && ! printf "%s" "$OUT" | grep -q -- "--slug beta "'
chk "활성 허브는 그대로 기동 명령" 'printf "%s" "$OUT" | grep -q -- "--slug alpha " && printf "%s" "$OUT" | grep -q -- "--slug gamma "'

OUT=$(bash "$RA" --dry-run --only beta 2>&1); RC=$?
chk "--only 로 휴면 허브 지정해도 건너뜀, 종료 코드 0" '[ "$RC" = 0 ] && ! printf "%s" "$OUT" | grep -q -- "--slug beta "' "rc=$RC"
OUT=$(bash "$RA" --dry-run --wake beta 2>&1); RC=$?
chk "--wake: 그 허브만 이번에 기동" '[ "$RC" = 0 ] && printf "%s" "$OUT" | grep -q -- "--slug beta " && ! printf "%s" "$OUT" | grep -q -- "--slug alpha "' "rc=$RC"
chk "--wake 는 표를 고치지 않음(반복 실행 안전)" '[ "$(grep -c "^#dormant" "$D")" = 1 ]'
bash "$RA" --dry-run --wake zzz >/dev/null 2>"$SB/e"; RC=$?
chk "--wake 없는 slug: 종료 코드 2 + 조치" '[ "$RC" = 2 ] && grep -q "hubs.tsv\|$D" "$SB/e"' "rc=$RC"

chk "--list: 활성·휴면과 사유" '[ "$(bash "$RA" --list | tr "\t" "|" | tr "\n" ";")" = "alpha|활성|-;beta|휴면|기능 팀으로 대체;gamma|활성|-;" ]'
bash "$RA" --is-dormant beta >"$SB/o" 2>&1; R1=$?
bash "$RA" --is-dormant alpha >/dev/null 2>&1; R2=$?
bash "$RA" --is-dormant zzz >/dev/null 2>&1; R3=$?
chk "--is-dormant: 휴면 0(사유 출력)·활성 1·없음 2" '[ "$R1 $R2 $R3" = "0 1 2" ] && grep -q "기능 팀으로 대체" "$SB/o"' "$R1 $R2 $R3"

# 표식 오타는 휴면 허브가 다시 뜨는 사고라 거부
printf 'alpha\t알파\t~/r/role-a.txt\t-\t-\n#dormant\tbetaa\t오타\n' > "$SB/typo.tsv"
HUBS_TSV="$SB/typo.tsv" bash "$RA" --dry-run >/dev/null 2>"$SB/e"; RC=$?
chk "표식 slug 오타: 종료 코드 2 + 원인·조치" '[ "$RC" = 2 ] && grep -q "betaa" "$SB/e" && grep -q "맞춤" "$SB/e"' "rc=$RC"
bash "$RA" --is-dormant >/dev/null 2>&1; chk "--is-dormant 인자 없음: 종료 코드 2" '[ $? = 2 ]'
chk "--help 에 휴면·깨우기·종료 코드" 'bash "$RA" --help | grep -q -- "--wake" && bash "$RA" --help | grep -q "#dormant" && bash "$RA" --help | grep -q "휴면 건너뜀 포함"'

# 표 호환: #dormant 줄이 있어도 이 표를 읽는 기존 코드의 결과가 같음
chk "ci_review 읽기(이름·별칭·저장소): 표식 줄 무시" 'PYTHONPATH="$SCR" python3 -c "
import sys, ci_review as r
n=r.load_hub_names(sys.argv[1]);assert n=={\"alpha\":\"알파\",\"beta\":\"베타\",\"gamma\":\"감마\"},n
assert set(r.load_hub_aliases(sys.argv[1]))=={\"a\",\"b\",\"g\"} and set(r.load_hub_repos(sys.argv[1]))==set(n)" "$D"'
chk "hub_split 읽기: 표식 줄 무시" 'LRM_HUBS_TSV="$D" PYTHONPATH="$SCR" python3 -c "
import hub_split as h
assert [x.slug for x in h.read_hubs()]==[\"alpha\",\"beta\",\"gamma\"]"'
chk "restart_watch 읽기: 표식 줄 무시" 'PYTHONPATH="$SCR" python3 -c "
import restart_watch as w
assert [s for s,_ in w.hub_rows()]==[\"alpha\",\"beta\",\"gamma\"]"'

# 실제 hubs.tsv
chk "hubs.tsv: 휴면 표식이 있고 slug 가 전부 목록에 있음·사유 비어 있지 않음" 'N=$(grep -c "^#dormant	" "$TSV"); [ "$N" -ge 1 ] && HUBS_TSV="$TSV" bash "$RA" --list >/dev/null && [ -z "$(awk -F"\t" "\$1==\"#dormant\" && \$3==\"\"" "$TSV")" ]'
chk "hubs.tsv: 휴면 허브가 dry-run 에서 기동되지 않음, 활성은 기동" 'O=$(HUBS_TSV="$TSV" bash "$RA" --dry-run 2>&1); for s in $(awk -F"\t" "\$1==\"#dormant\"{print \$2}" "$TSV"); do printf "%s" "$O" | grep -q -- "--slug $s " && exit 1; done; printf "%s" "$O" | grep -q -- "--slug config "'

printf '\n%s\n' "결과: PASS $PASS / FAIL $FAIL"
[ "$FAIL" = 0 ]
