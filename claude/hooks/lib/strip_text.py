"""명령 문자열에서 '명령이 아닌 글자'(heredoc 본문, 따옴표 안 문구)를 지워 훅의 명령 판정이 문구에 속지 않게 함.
stdin: 명령, stdout: 정리된 명령. bash -c·sh -c·eval 이 있거나 따옴표 안에 $( )·백틱이 있으면 그 부분은 실행될 수 있어 그대로 둠."""
import re
import sys

cmd = sys.stdin.read()

# 1) heredoc 본문 제거(<<WORD, <<'WORD', <<"WORD", <<-WORD)
lines, out, end = cmd.split("\n"), [], None
for line in lines:
    if end is not None:
        if line.strip() == end[0] if end[1] else line == end[0]:
            end = None
        continue
    out.append(line)
    m = re.search(r"<<(-?)\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\2", line)
    if m and "<<<" not in line:
        end = (m.group(3), m.group(1) == "-")
cmd = "\n".join(out)

# 2) 셸이 문자열을 다시 실행하는 경우는 따옴표 안을 지우지 않음
if re.search(r"(^|[\s;&|(])(bash|sh|zsh|eval)\b[^|;&]*(\s-[a-zA-Z]*c\b|\beval\b)|(^|[\s;&|(])eval\s", cmd):
    sys.stdout.write(cmd)
    sys.exit(0)

# 3) 따옴표 안 내용을 공백으로
res, i, n = [], 0, len(cmd)
while i < n:
    c = cmd[i]
    if c == "\\" and i + 1 < n:
        res.append(cmd[i:i + 2]); i += 2; continue
    if c in "'\"":
        j = i + 1
        while j < n and cmd[j] != c:
            j += 2 if (c == '"' and cmd[j] == "\\") else 1
        body = cmd[i + 1:j]
        if c == '"' and ("$(" in body or "`" in body):
            res.append(cmd[i:j + 1])
        else:
            res.append(c + " " * len(body.replace("\n", "")) + c)
        i = j + 1
        continue
    res.append(c); i += 1
sys.stdout.write("".join(res))
