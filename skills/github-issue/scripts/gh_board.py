#!/usr/bin/env python3
"""GitHub Issue ↔ Project 보드 연결 도구. 이슈 생성(gh issue create)은 에이전트가 하고 이 도구는 보드·상위만 맡음.

사용:
  gh_board.py config                                   설정·보드 칸 확인(읽기 전용)
  gh_board.py add <이슈 URL> --status S [--due 날짜] [--parent <상위 이슈 URL>]
                                                       보드에 올리고 상태·기한 지정, 상위의 하위 이슈로 연결
  gh_board.py status <이슈 URL> <상태>                 보드 상태 변경. '완료' 는 사람만 → 거부
  gh_board.py get <이슈 URL>                           발급 뒤 점검용 JSON(issue_check.py post 입력 한 항목)
설정: ~/.config/claude-config/github-issue.json (GITHUB_ISSUE_CONFIG 로 변경). 양식은 references/config.example.json.
종료 코드: 0 성공, 1 gh 실패, 2 설정·인자 오류. 같은 인자로 다시 불러도 결과가 같음(보드 추가·하위 연결은 이미 있으면 건너뜀).
"""
import argparse
import json
import os
import re
import subprocess
import sys

CONFIG = os.environ.get("GITHUB_ISSUE_CONFIG", os.path.expanduser("~/.config/claude-config/github-issue.json"))
GH = os.environ.get("GITHUB_ISSUE_GH", "gh")
URL_RE = re.compile(r"^https://github\.com/([\w.-]+)/([\w.-]+)/issues/(\d+)$")
HUMAN_ONLY = "완료"
EPIC_LABEL = "epic"


class Fail(Exception):
    def __init__(self, msg, code=1):
        super().__init__(msg)
        self.code = code


def gh(*args, parse=True):
    r = subprocess.run([GH, *args], capture_output=True, text=True)
    if r.returncode != 0:
        raise Fail(f"gh {' '.join(args[:3])} 실패: {r.stderr.strip()[:300]}")
    return json.loads(r.stdout) if parse and r.stdout.strip() else r.stdout


def load_config():
    try:
        with open(CONFIG, encoding="utf-8") as f:
            c = json.load(f)
    except (OSError, ValueError) as e:
        raise Fail(f"설정 읽기 실패({CONFIG}): {e} — references/config.example.json 을 복사해 채움", 2)
    for k in ("owner", "number", "status_field"):
        if not c.get(k):
            raise Fail(f"설정에 {k} 없음({CONFIG})", 2)
    return c


def parse_url(url):
    m = URL_RE.match(url or "")
    if not m:
        raise Fail(f"이슈 URL 형식 오류: {url} — https://github.com/OWNER/REPO/issues/번호", 2)
    return m.group(1), m.group(2), int(m.group(3))


def fields(c):
    out = {}
    for f in gh("project", "field-list", str(c["number"]), "--owner", c["owner"], "--format", "json")["fields"]:
        out[f["name"]] = f
    return out


def project_id(c):
    return gh("project", "view", str(c["number"]), "--owner", c["owner"], "--format", "json")["id"]


def item_of(c, url):
    """보드에 이미 있으면 그 항목 ID, 없으면 추가해 ID 반환"""
    d = gh("project", "item-add", str(c["number"]), "--owner", c["owner"], "--url", url, "--format", "json")
    return d["id"]


def set_status(c, fs, pid, item, name):
    sf = fs.get(c["status_field"])
    if not sf:
        raise Fail(f"보드에 상태 칸 '{c['status_field']}' 없음", 2)
    opt = next((o for o in sf.get("options", []) if o["name"] == name), None)
    if not opt:
        raise Fail(f"상태 '{name}' 없음. 보드 선택지: {', '.join(o['name'] for o in sf.get('options', []))}", 2)
    gh("project", "item-edit", "--id", item, "--project-id", pid, "--field-id", sf["id"],
       "--single-select-option-id", opt["id"], parse=False)


def set_due(c, fs, pid, item, due):
    df = fs.get(c.get("due_field") or "")
    if not df:
        raise Fail(f"보드에 기한 칸 '{c.get('due_field')}' 없음 — 설정 due_field 확인", 2)
    gh("project", "item-edit", "--id", item, "--project-id", pid, "--field-id", df["id"], "--date", due, parse=False)


def link_parent(child_url, parent_url):
    o, r, n = parse_url(child_url)
    po, pr, pn = parse_url(parent_url)
    cid = gh("api", f"repos/{o}/{r}/issues/{n}")["id"]
    subs = gh("api", f"repos/{po}/{pr}/issues/{pn}/sub_issues")
    if any(s.get("id") == cid for s in subs):
        return "이미 연결됨"
    gh("api", "-X", "POST", f"repos/{po}/{pr}/issues/{pn}/sub_issues", "-F", f"sub_issue_id={cid}")
    return "연결함"


def board_values(c, url):
    """보드에서 이 이슈의 상태·기한 조회. 보드에 없으면 (None, None)"""
    o, r, n = parse_url(url)
    q = """query($o:String!,$r:String!,$n:Int!){repository(owner:$o,name:$r){issue(number:$n){
      parent{url} projectItems(first:20){nodes{project{number owner{... on User{login} ... on Organization{login}}}
      fieldValues(first:30){nodes{... on ProjectV2ItemFieldSingleSelectValue{name field{... on ProjectV2FieldCommon{name}}}
      ... on ProjectV2ItemFieldDateValue{date field{... on ProjectV2FieldCommon{name}}}}}}}}}}"""
    d = gh("api", "graphql", "-f", f"query={q}", "-f", f"o={o}", "-f", f"r={r}", "-F", f"n={n}")
    issue = d["data"]["repository"]["issue"]
    owner = c["owner"].lstrip("@")
    status = due = None
    for it in issue["projectItems"]["nodes"]:
        p = it["project"]
        if p["number"] != int(c["number"]) or (owner != "me" and p["owner"]["login"] != owner):
            continue
        for v in it["fieldValues"]["nodes"]:
            name = (v.get("field") or {}).get("name")
            if name == c["status_field"]:
                status = v.get("name")
            elif name == c.get("due_field"):
                due = v.get("date")
    return status, due, (issue.get("parent") or {}).get("url")


def cmd_get(c, url):
    o, r, n = parse_url(url)
    d = gh("issue", "view", str(n), "--repo", f"{o}/{r}", "--json", "assignees,labels,body,url")
    status, due, parent = board_values(c, url)
    labels = [x["name"] for x in d.get("labels", [])]
    out = {"key": f"{o}/{r}#{n}", "type": "에픽" if EPIC_LABEL in labels else "",
           "parent": parent_key(parent), "status": status, "due": due,
           "assignee": ",".join(a["login"] for a in d.get("assignees", [])), "description": d.get("body", "")}
    if out["type"] == "에픽":
        subs = gh("api", f"repos/{o}/{r}/issues/{n}/sub_issues")
        out["children"] = [board_values(c, s["html_url"])[0] for s in subs]
    print(json.dumps(out, ensure_ascii=False))


def parent_key(url):
    if not url:
        return None
    o, r, n = parse_url(url)
    return f"{o}/{r}#{n}"


def main(argv):
    p = argparse.ArgumentParser(prog="gh_board.py", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("config")
    a = sub.add_parser("add")
    a.add_argument("url")
    a.add_argument("--status", required=True)
    a.add_argument("--due")
    a.add_argument("--parent")
    s = sub.add_parser("status")
    s.add_argument("url")
    s.add_argument("name")
    g = sub.add_parser("get")
    g.add_argument("url")
    args = p.parse_args(argv)
    try:
        c = load_config()
        if args.cmd == "config":
            fs = fields(c)
            print(json.dumps({"config": CONFIG, "owner": c["owner"], "number": c["number"],
                              "status": [o["name"] for o in fs.get(c["status_field"], {}).get("options", [])],
                              "due_field": c.get("due_field") if c.get("due_field") in fs else None}, ensure_ascii=False))
            return 0
        if args.cmd == "get":
            cmd_get(c, args.url)
            return 0
        name = args.status if args.cmd == "add" else args.name
        if name == HUMAN_ONLY:
            raise Fail("'완료' 는 사람만 전환. 병합 근거가 있으면 '확인 대기'", 2)
        parse_url(args.url)
        if args.cmd == "add" and args.parent:
            parse_url(args.parent)
        if args.cmd == "add" and args.due and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", args.due):
            raise Fail("--due 형식 오류: YYYY-MM-DD", 2)
        fs, pid = fields(c), project_id(c)
        item = item_of(c, args.url)
        set_status(c, fs, pid, item, name)
        msg = [f"보드 #{c['number']} 상태 '{name}'"]
        if args.cmd == "add":
            if args.due:
                set_due(c, fs, pid, item, args.due)
                msg.append(f"기한 {args.due}")
            if args.parent:
                msg.append("상위 " + link_parent(args.url, args.parent))
        print("ok " + " · ".join(msg))
        return 0
    except Fail as e:
        print(e, file=sys.stderr)
        return e.code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
