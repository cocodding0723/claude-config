#!/usr/bin/env python3
"""가짜 gh. `gh api -i [-H If-None-Match: x] <경로>` 만 흉내.
응답은 FAKE_DIR/api/<경로를 _ 로 바꾼 이름>.json 의 본문. etag 는 본문 해시라 본문이 같으면 304(종료 코드 1, 실제 gh 와 같음).
FAKE_DIR/fail 에 정규식 한 줄이 있으면 맞는 경로는 HTTP 502, 'net' 이면 응답 없이 실패. 호출은 FAKE_DIR/calls.log 에 한 줄씩."""
import hashlib
import os
import re
import sys

D = os.environ["FAKE_DIR"]
args = sys.argv[1:]
if args[:2] != ["api", "-i"]:
    sys.stderr.write("미지원: %s\n" % " ".join(args))
    sys.exit(2)
etag = None
rest = args[2:]
if rest[:1] == ["-H"]:
    etag = rest[1].split(":", 1)[1].strip()
    rest = rest[2:]
path = rest[0]
with open(os.path.join(D, "calls.log"), "a") as f:
    f.write("%s%s\n" % (path, " [etag]" if etag else ""))
try:
    rule = open(os.path.join(D, "fail")).read().strip()
except OSError:
    rule = ""
if rule == "net":
    sys.stderr.write("error connecting to api.github.com\n")
    sys.exit(1)
if rule and re.search(rule, path):
    print("HTTP/2.0 502 Bad Gateway\nX-Ratelimit-Remaining: 4000\n\n{\"message\":\"bad\"}")
    sys.stderr.write("gh: HTTP 502\n")
    sys.exit(1)
key = re.sub(r"[^A-Za-z0-9]+", "_", path).strip("_")
try:
    body = open(os.path.join(D, "api", key + ".json")).read()
except OSError:
    print("HTTP/2.0 404 Not Found\n\n{\"message\":\"Not Found\"}")
    sys.stderr.write("gh: Not Found (HTTP 404)\n")
    sys.exit(1)
tag = 'W/"%s"' % hashlib.sha1(body.encode()).hexdigest()
remaining = open(os.path.join(D, "remaining")).read().strip() if os.path.exists(os.path.join(D, "remaining")) else "4900"
if etag == tag:
    print("HTTP/2.0 304 Not Modified\nEtag: %s\nX-Ratelimit-Remaining: %s\n" % (tag, remaining))
    sys.stderr.write("gh: HTTP 304\n")
    sys.exit(1)
print("HTTP/2.0 200 OK\nEtag: %s\nX-Ratelimit-Remaining: %s\nX-Ratelimit-Used: 7\n\n%s" % (tag, remaining, body))
