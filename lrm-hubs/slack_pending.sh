#!/bin/bash
# 답 안 한 결정 메시지 목록을 링크와 함께 봇 DM 으로 보냄. 사용: slack_pending.sh [--dry-run]
set -eu
export SSL_CERT_FILE="${SSL_CERT_FILE:-/etc/ssl/cert.pem}"
set -a; . "$HOME/.config/lrm-hubs/slack-bot.env"; set +a
python3 - "$@" <<'PY'
import json,os,sys,urllib.request,urllib.parse
H=os.path.expanduser('~/.config/lrm-hubs')
tok=os.environ['SLACK_BOT_TOKEN']
sent=[json.loads(l) for l in open(f'{H}/slack-sent.jsonl') if l.strip()]
inbox=[json.loads(l) for l in open(f'{H}/slack-inbox.jsonl') if l.strip()]
done={d.get('parent_ts') for d in inbox if d.get('type')=='reaction'}|set(open(f'{H}/slack-done.txt').read().split() if os.path.exists(f'{H}/slack-done.txt') else [])
dec=[s for s in sent if s.get('ts') and not s.get('thread_ts') and ('결정' in s.get('title','')) and s['ts'] not in done]
seen=set();rows=[]
for s in dec:
    t=s['title']
    if t in seen: continue
    seen.add(t)
    q=urllib.parse.urlencode({'channel':s['channel'],'message_ts':s['ts']})
    r=json.load(urllib.request.urlopen(urllib.request.Request('https://slack.com/api/chat.getPermalink?'+q,headers={'Authorization':'Bearer '+tok})))
    rows.append(f"• <{r.get('permalink','')}|{t}>")
if not rows: print('대기 0'); sys.exit(0)
text="🙋 *답을 기다리는 결정 "+str(len(rows))+"개* — 눌러서 이동, 이모지로 답\n"+"\n".join(rows)
if '--dry-run' in sys.argv: print(text); sys.exit(0)
b=json.dumps({'channel':dec[0]['channel'],'text':text}).encode()
r=json.load(urllib.request.urlopen(urllib.request.Request('https://slack.com/api/chat.postMessage',data=b,headers={'Authorization':'Bearer '+tok,'Content-Type':'application/json'})))
print('ok' if r.get('ok') else r.get('error'), len(rows))
PY
