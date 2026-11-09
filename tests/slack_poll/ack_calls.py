"""fake_slack.py 호출 기록에서 chat.postMessage 줄만 뽑아 JSON 한 줄씩 출력: {"channel","thread_ts","text"}.
사용: ack_calls.py <호출 기록 파일> [update]. 쿼리를 퍼센트 디코딩해 문구를 그대로 비교할 수 있게 함.
update 를 주면 chat.postMessage 대신 chat.update(POST, 본문을 쿼리처럼 기록) 호출을 {"channel","ts","text"} 로 출력함."""
import json
import sys
import urllib.parse

UPDATE = len(sys.argv) > 2 and sys.argv[2] == "update"
WANT, KEYS = ("POST:chat.update", ("channel", "ts", "text")) if UPDATE else ("chat.postMessage", ("channel", "thread_ts", "text"))
with open(sys.argv[1], encoding="utf-8") as f:
    for line in f:
        method, _, query = line.rstrip("\n").partition("\t")
        if method != WANT:
            continue
        q = urllib.parse.parse_qs(query, keep_blank_values=True)
        print(json.dumps({k: q.get(k, [""])[0] for k in KEYS}, ensure_ascii=False))
