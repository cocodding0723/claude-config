#!/bin/bash
# 허브에 묶이지 않았거나 이름이 규칙에 안 맞는 워크스페이스 목록
. "$(dirname "$0")/lib/py.sh"  # PY = 3.10 이상 python3 (launchd 짧은 PATH 의 3.9 방지)
orca worktree list --json 2>/dev/null | "$PY" -c "
import json,sys,os,re
ws=json.load(sys.stdin)['result']['worktrees']; ids={w['id'] for w in ws}
for w in ws:
  p=w.get('path') or ''
  if '/LoveRevolutionMaker' not in p: continue
  b=os.path.basename(p)
  if b.startswith('coord-') or p.endswith('/orca/LoveRevolutionMaker'): continue
  n=w.get('displayName') or ''
  bad=[]
  if w.get('parentWorktreeId') not in ids: bad.append('허브 미연결')
  if not re.match(r'^\[(LRM-\d+|개인)\]',n): bad.append('이름 규칙 위반')
  if bad: print(' / '.join(bad),'|',n,'|',b)
"
