"""1단계: Claude·Codex 세션 기록에서 기간 안 메시지 시각·토큰 수 수집 → sess.json"""
import datetime as D
import glob
import json
import os
import re

from common import KST, args, claude_base, kst

a = args(__doc__)
R = re.compile(rb'"timestamp":\s*"(\d{4}-\d\d-\d\dT[0-9:]{8})')
TOK = [re.compile(k) for k in (rb'"input_tokens":(\d+)', rb'"output_tokens":(\d+)', rb'"cache_read_input_tokens":(\d+)', rb'"cache_creation_input_tokens":(\d+)')]
in_range = lambda s: a.start <= kst(s) < a.end
lo = (a.start - KST - D.datetime(1970, 1, 1)).total_seconds()  # 기간 시작 전 수정된 파일은 건너뜀

out = []
base = claude_base()
for d in os.listdir(base) if os.path.isdir(base) else []:
    for f in glob.glob(f"{base}/{d}/*.jsonl"):
        if os.path.getmtime(f) < lo:
            continue
        ts, tu = [], 0
        with open(f, "rb") as fh:
            for line in fh:
                m = R.search(line)
                if m and in_range(m.group(1).decode()):
                    ts.append(m.group(1).decode())
                if b'"input_tokens"' in line:
                    tu += sum(int(mm.group(1)) for k in TOK for mm in [k.search(line)] if mm)
        if ts:
            out.append(dict(src="claude", dir=d, f=os.path.basename(f), ts=ts, tok=tu, size=os.path.getsize(f)))
for f in glob.glob(os.path.expanduser("~/.codex/sessions/*/*/*/*.jsonl")):
    if os.path.getmtime(f) < lo:
        continue
    ts = []
    with open(f, "rb") as fh:
        for line in fh:
            m = R.search(line)
            if m and in_range(m.group(1).decode()):
                ts.append(m.group(1).decode())
    cwd = ""
    try:
        with open(f) as fh:
            cwd = json.loads(fh.readline()).get("payload", {}).get("cwd", "")
    except (ValueError, OSError):
        pass
    if ts:
        out.append(dict(src="codex", dir=cwd, f=os.path.basename(f), ts=ts, tok=0, size=os.path.getsize(f)))
with open(a.path("sess.json"), "w") as fh:
    json.dump(out, fh)
print("세션", len(out))
