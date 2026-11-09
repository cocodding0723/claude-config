#!/usr/bin/env python3
"""before/after PUML 의 노드(as 별칭)를 비교해 바뀐 곳을 색으로 표시한 사본을 만든다.
사용: puml_diff.py before.puml after.puml
출력: before.hl.puml(삭제=빨강 점선) · after.hl.puml(추가=초록 굵게, 문구 변경=노랑) + 범례"""
import re, sys
NODE = re.compile(r'^(\s*(?:rectangle|component|class|node|package|database|interface|actor)\s+"([^"]*)"\s+as\s+(\w+))(.*)$')

def parse(lines):
    m = {}
    for i, l in enumerate(lines):
        g = NODE.match(l)
        if g: m[g.group(3)] = (i, g.group(2))
    return m

def paint(lines, idx, color, style):
    l = lines[idx]; g = NODE.match(l)
    head = g.group(1)
    lines[idx] = f"{head} {color};line:{style}\n" if not g.group(4).strip().startswith("#") else re.sub(r'#\S+', f"{color};line:{style}", l, count=1)

def legend():
    return ('legend right\n<back:#C8F7C5> 추가 </back>  <back:#FFF3B0> 변경 </back>  <back:#FFD0D0> 삭제 </back>\nendlegend\n')

def main():
    bp, ap = sys.argv[1:3]
    B = open(bp).readlines(); A = open(ap).readlines()
    nb, na = parse(B), parse(A)
    for k, (i, label) in na.items():
        if k not in nb: paint(A, i, "#C8F7C5", "green;line.bold")
        elif nb[k][1] != label: paint(A, i, "#FFF3B0", "orange;line.bold")
    for k, (i, _) in nb.items():
        if k not in na: paint(B, i, "#FFD0D0", "red;line.dashed")
    for L in (A, B):
        j = max(i for i, l in enumerate(L) if l.strip() == "@enduml"); L.insert(j, legend())
    open(bp.replace(".puml", ".hl.puml"), "w").writelines(B)
    open(ap.replace(".puml", ".hl.puml"), "w").writelines(A)
    add = [k for k in na if k not in nb]; rem = [k for k in nb if k not in na]
    chg = [k for k in na if k in nb and nb[k][1] != na[k][1]]
    print(f"추가 {len(add)} · 변경 {len(chg)} · 삭제 {len(rem)}")

if __name__ == "__main__":
    main()
