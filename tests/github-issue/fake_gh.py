#!/usr/bin/env python3
"""시험용 가짜 gh. 호출을 FAKE_GH_LOG 에 남기고 FAKE_GH_STATE(JSON) 에 하위 이슈·보드 항목을 둠. 네트워크 없음."""
import json, os, sys
a = sys.argv[1:]; LOG, ST = os.environ["FAKE_GH_LOG"], os.environ["FAKE_GH_STATE"]
open(LOG, "a").write(json.dumps(a, ensure_ascii=False) + "\n")
st = json.load(open(ST)) if os.path.exists(ST) else {"subs": {}, "items": {}}
def out(o): print(json.dumps(o, ensure_ascii=False)); json.dump(st, open(ST, "w")); sys.exit(0)
if a[:2] == ["project", "field-list"]:
    out({"fields": [{"name": "Status", "id": "F_ST", "options": [{"name": n, "id": "O_" + n} for n in ("해야 할 일", "진행 중", "확인 대기", "보류", "완료")]},
                    {"name": "기한", "id": "F_DUE"}]})
if a[:2] == ["project", "view"]: out({"id": "PVT_1"})
if a[:2] == ["project", "item-add"]:
    url = a[a.index("--url") + 1]; st["items"].setdefault(url, "ITEM_%d" % (len(st["items"]) + 1)); out({"id": st["items"][url]})
if a[:2] == ["project", "item-edit"]: out({})
if a[0] == "api" and a[1] == "graphql":
    q = a[3]
    if "parent{url}" in q:
        out({"data": {"repository": {"issue": {"parent": {"url": "https://github.com/me/r/issues/1"}}}}})
    node = {"projectV2": {"items": {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": [
        {"content": {"url": "https://github.com/me/r/issues/9"}, "fieldValues": {"nodes": [{"name": "보류", "field": {"name": "Status"}}]}},
        {"content": {"url": "https://github.com/me/r/issues/5"}, "fieldValues": {"nodes": [{},
            {"name": "진행 중", "field": {"name": "Status"}}, {"date": "2026-11-30", "field": {"name": "기한"}}]}}]}}}
    out({"data": {"viewer": node} if "viewer" in q else {"repositoryOwner": node}})
if a[0] == "api":
    if a[1] == "-X":
        path = a[3]; cid = int(a[5].split("=")[1]); st["subs"].setdefault(path.rsplit("/", 1)[0], []).append(cid); out({})
    path = a[1]
    if path.endswith("/sub_issues"):
        out([{"id": i, "html_url": "https://github.com/me/r/issues/%d" % (i - 1000)} for i in st["subs"].get(path.rsplit("/", 1)[0], [])])
    n = int(path.rsplit("/", 1)[1]); out({"id": 1000 + n})
if a[:2] == ["issue", "view"]:
    out({"assignees": [{"login": "me"}], "labels": [{"name": "task"}], "body": "본문", "url": "u"})
print("미지원: " + " ".join(a), file=sys.stderr); sys.exit(1)
