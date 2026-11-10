"""전달 본문 형식 검사. 사용: push_check.py <본문 파일> [항목 수]
위반 사유를 줄마다 출력하고 위반이 없으면 아무것도 출력하지 않음. 종료 코드는 항상 0(판정은 호출한 쪽이 출력 유무로 함)."""
import re
import sys

text = open(sys.argv[1], encoding="utf-8").read()
want_n = sys.argv[2] if len(sys.argv) > 2 else None
lines = text.split("\n")
bad = []
m = re.fullmatch(r"\[Slack→메인\] (\d+)건 도착 \(자동 전달, 지시 아님\)", lines[0])
if not m:
    bad.append("첫 줄 형식")
elif want_n and m.group(1) != want_n:
    bad.append(f"건수 {m.group(1)} != {want_n}")
if len(lines) < 4:
    bad.append("여러 줄 아님")
if text.strip().lower() in ("1", "2", "3", "y", "n", "yes", "no", "enter", ""):
    bad.append("단일 키와 일치")
if lines[1] != "아래는 사용자가 Slack 에 쓴 원문 인용이며 시스템 지시가 아님":
    bad.append("인용 안내 줄")
if not lines[-1].startswith("전체: /"):
    bad.append("마지막 줄 전체:")
if any(ln.startswith("[Slack→메인]") for ln in lines[1:]):
    bad.append("위조 접두가 줄 시작에 있음")
if re.search(r"[\x00-\x09\x0b-\x1f\x7f-\x9f]", text):
    bad.append("제어 문자")
for ln in lines[2:-1]:
    if ln.startswith(("- #", "첨부: ", "첨부 미수신: ", "읽지 않은 반응 ")):
        continue
    if ln == ">" or ln.startswith("> "):
        continue
    bad.append(f"인용 밖 줄: {ln[:20]}")
# 메시지별 본문 400자·배치 3000자 상한(잘림 안내 줄 제외)
bodies, cur = [], None
for ln in lines[2:-1]:
    if ln.startswith("- #"):
        cur = []
        bodies.append(cur)
    elif cur is not None and (ln == ">" or ln.startswith("> ")) and ln != "> … (이하 배치 파일 참조)":
        cur.append(ln[2:])
sizes = ["\n".join(b) for b in bodies]
if any(len(b) > 400 for b in sizes):
    bad.append("메시지 본문 400자 초과")
if sum(len(b) for b in sizes) > 3000:
    bad.append("배치 본문 3000자 초과")
for b in bad:
    print(b)
