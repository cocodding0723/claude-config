#!/usr/bin/env python3
"""코디네이터 쓰기 범위 판정(PreToolUse Bash 훅). stdin: hook JSON. 출력: 거부면 hook JSON 한 줄, 통과면 빈 출력(판별 불가 경고는 stderr).
기능 코디네이터는 작업판(~/orca/board)에서 자기 에픽과 그 하위 카드만 쓴다. 다른 에픽 카드 쓰기는 거부, 읽기는 늘 통과.
- 대상 세션: 허브(폴더 coord-*) 세션만. ORCA_ROLE 이 있으면 coordinator·hub 일 때만. 메인·작업자(자기 카드 코멘트 포함)는 대상 아님.
- 대상 명령: bd 의 쓰기(update·close·reopen·delete·edit·note·comment(s) add·label add/remove·tag·dep add/remove·link·assign·priority 등)와
  create(--parent 필수, 자기 에픽 아래만). 작업판 폴더 지정(-C·cd)이 작업판이 아니면 보지 않음.
- 자기 에픽: label hub:<허브 폴더|slug> · feature:<slug>(feature_open.sh 가 남김, 허브 coord-feat-<slug>-hub) · repo:<경로>/<허브 폴더> 중 하나인 epic,
  또는 hub:<허브> 가 붙은 카드. 카드의 parent 사슬이 그 안에 있으면 자기 범위.
- 오탐이 가장 큰 비용이라 불확실하면 통과: 자기 에픽 판별 불가·작업판 조회 실패·작업판에 없는 id 는 통과(+경고).
- 훅이 안 보는 경로: 스크립트 안의 bd 호출(board_notify.py 등)·변수로 만든 id.
종료 코드: 항상 0(판정은 stdout JSON)."""
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)
import main_role  # noqa: E402
import main_send_guard  # noqa: E402
import ws_token_guard as wtg  # noqa: E402

# ---- 설정 ----
DEFAULT_BOARD = "~/orca/board"
BOARD_ENV = "LRM_BOARD"
COORD_ROLES = {"coordinator", "hub"}
PREFILTER = re.compile(r"\bbd\b")  # bd 가 없는 명령은 git·bd 조회 없이 바로 통과
ID_RE = re.compile(r"^[a-z][a-z0-9-]*-[a-z0-9]+(\.\d+)*$")  # 접두에 - 가 든 작업판도 허용
SLUG_STRIP = re.compile(r"^coord-|-hub$")
FEAT_HUB_RE = re.compile(r"^coord-feat-(.+)-hub$")
LIST_TIMEOUT = 20
# 서브명령 → 첫 위치 인자만 대상 카드(나머지는 본문·값) / 위치 인자 전부가 대상 카드
FIRST_ONLY = {"note", "comment", "priority", "assign", "edit", "unclaim", "heartbeat", "set-state", "promote", "tag", "supersede", "duplicate", "link"}
ALL_IDS = {"update", "close", "reopen", "delete"}
# 값을 받는 플래그. 모르는 플래그는 값 없는 것으로 봐 id 를 놓치는 쪽(통과)으로 기움
VALUE_FLAGS = {
    "-C", "--directory", "--db", "--database", "--actor", "--dolt-auto-commit", "--mem-profile",
    "-a", "--assignee", "-d", "--description", "--design", "--design-file", "--due", "-e", "--estimate", "--external-ref",
    "-l", "--labels", "--add-label", "--remove-label", "--set-labels", "--metadata", "--set-metadata", "--unset-metadata",
    "--notes", "--append-notes", "--parent", "-p", "--priority", "-s", "--status", "-t", "--type", "--title", "--acceptance",
    "--body-file", "--defer", "--deps", "--id", "--if-assignee", "--if-status", "--spec-id", "--session", "-r", "--reason",
    "--reason-file", "--context", "--repo", "--skills", "--storage-class", "--waits-for", "--waits-for-gate", "--wisp-type",
    "--mol-type", "--await-id", "--blocks", "--graph",
}
SKIP_CREATE = {"--dry-run", "--graph", "--file", "-f"}  # 카드를 하나만 만들지 않거나 만들지 않는 형태
REPLY_TO = "~/.config/lrm-hubs/board_notify.py post --kind other --source <허브> '<본문>'"


def deny(reason):
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": reason}}, ensure_ascii=False))


def warn(msg):
    print(f"epic-scope-guard: 경고 — {msg}. 통과 처리", file=sys.stderr)


def board_dir():
    return os.path.realpath(wtg.expand(os.environ.get(BOARD_ENV) or DEFAULT_BOARD))


def hub_name(ev):
    """허브 세션이면 폴더 이름(coord-…), 아니면 None. 허브 폴더는 git 최상위 폴더 → 못 찾으면 cwd 경로 구성 요소"""
    role = os.environ.get("ORCA_ROLE", "")
    if role and role not in COORD_ROLES:
        return None
    cwd = ev.get("cwd") or ""
    if not role and main_role.is_main(ev):
        return None
    if not os.path.isdir(cwd):
        return None
    r = subprocess.run(["git", "-C", cwd, "rev-parse", "--show-toplevel"], capture_output=True, text=True, timeout=5)
    top = r.stdout.strip() if r.returncode == 0 else ""
    for p in ([os.path.basename(top)] if top else []) + cwd.split(os.sep)[::-1]:
        if wtg.HUB_DIR_RE.match(p):
            return p
    return None


def load_cards():
    r = subprocess.run(["bd", "-C", board_dir(), "list", "--all", "--limit", "0", "--json"], capture_output=True, text=True, timeout=LIST_TIMEOUT)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip().splitlines()[-1:] or "bd list 실패")
    return {c["id"]: c for c in json.loads(r.stdout)}


def own_roots(hub, cards):
    """자기 에픽 id 와 hub 라벨이 붙은 카드 id"""
    slug = SLUG_STRIP.sub("", hub)
    feat = FEAT_HUB_RE.match(hub)
    want = {f"hub:{hub}", f"hub:{slug}"} | ({f"feature:{feat.group(1)}"} if feat else set())
    out = {"epics": set(), "cards": set()}
    for c in cards.values():
        labels = set(c.get("labels") or [])
        epic = c.get("issue_type") == "epic"
        if labels & want and not (epic and labels == labels & {"kind:epic"}):
            out["epics" if epic else "cards"].add(c["id"])
        elif epic and any(l.startswith("repo:") and l[5:].rstrip("/").endswith("/" + hub) for l in labels):
            out["epics"].add(c["id"])
    return out


def in_scope(card_id, cards, roots):
    own, seen = roots["epics"] | roots["cards"], set()
    while card_id and card_id not in seen:
        if card_id in own:
            return True
        seen.add(card_id)
        c = cards.get(card_id) or {}
        card_id = c.get("parent") or (card_id.rsplit(".", 1)[0] if "." in card_id else "")
    return False


def parse_bd(seg, i):
    """bd 단계 → (-C 값 또는 None, 위치 인자, 플래그→값). 값 없는 플래그의 값은 True"""
    chdir, pos, flags, k, args = None, [], {}, 0, seg[i + 1:]
    while k < len(args):
        a = args[k]
        if a.startswith("--") and "=" in a:
            name, _, v = a.partition("=")
            flags[name] = v
        elif a.startswith("-") and a != "-":
            if a in VALUE_FLAGS and k + 1 < len(args):
                flags[a] = args[k + 1]
                k += 1
            else:
                flags[a] = True
            if a in ("-C", "--directory"):
                chdir = flags[a]
        else:
            pos.append(a)
        k += 1
    if "--directory" in flags and chdir is None:
        chdir = flags["--directory"]
    return chdir, pos, flags


def targets(pos, flags):
    """쓰기 명령이 건드리는 카드 id 후보와 create 여부. 읽기·모르는 명령은 ([], False)"""
    if not pos:
        return [], False
    sub, rest = pos[0], pos[1:]
    ids = []
    if sub in ("create", "q"):
        return [], True
    if sub in ALL_IDS:
        ids = rest
    elif sub in FIRST_ONLY:
        ids = rest[:1]
    elif sub == "comments" and rest[:1] == ["add"]:
        ids = rest[1:2]
    elif sub in ("label", "dep") and rest[:1] in (["add"], ["remove"], ["relate"], ["unrelate"]):
        ids = rest[1:-1] if sub == "label" else rest[1:2]
    elif sub == "dep" and "--blocks" in flags and isinstance(flags["--blocks"], str):
        ids = [flags["--blocks"]]
    if sub == "update" and isinstance(flags.get("--parent"), str):
        ids = ids + [flags["--parent"]]  # 재부모화 대상도 자기 범위여야 함
    return [x for x in ids if ID_RE.match(x)], False


def reason_out(card, hub, epics):
    return (f"다른 에픽 카드는 쓰지 않음({card} 는 {hub} 가 맡은 에픽 {', '.join(sorted(epics)) or '-'} 아래가 아님) — "
            f"그 에픽 코디네이터에게 {REPLY_TO} 로 요청하거나 메인에 요청. 읽기(show·list·comments)는 가능")


def judge(ev):
    if (ev.get("tool_name") or "Bash") != "Bash":
        return None
    cmd = (ev.get("tool_input") or {}).get("command") or ""
    if not PREFILTER.search(cmd):
        return None
    segs = main_send_guard.segments(wtg.drop_heredocs(cmd))
    hub = cards = roots = None
    cd_board = False
    for seg in segs:
        i = wtg.head(seg)
        if i >= len(seg):
            continue
        if wtg.base(seg[i]) == "cd" and len(seg) > i + 1:
            cd_board = os.path.realpath(wtg.expand(seg[i + 1])) == board_dir()
            continue
        if wtg.base(seg[i]) != "bd":
            continue
        chdir, pos, flags = parse_bd(seg, i)
        if chdir is not None:
            if os.path.realpath(wtg.expand(chdir)) != board_dir():
                continue  # 작업판이 아닌 폴더(변수 경로 포함)는 보지 않음
        elif not cd_board:
            continue
        ids, is_create = targets(pos, flags)
        if is_create and any(f in flags for f in SKIP_CREATE):
            continue
        if not ids and not is_create:
            continue
        if hub is None:
            hub = hub_name(ev) or ""
        if not hub:
            return None  # 메인·작업자·허브 밖 세션
        if cards is None:
            try:
                cards = load_cards()
            except Exception as e:
                warn(f"작업판 조회 실패({e})")
                return None
            roots = own_roots(hub, cards)
            if not roots["epics"] and not roots["cards"]:
                warn(f"{hub} 가 맡은 에픽을 찾지 못함(label hub:·feature:·repo: 없음)")
                return None
        if is_create:
            parent = flags.get("--parent")
            if not isinstance(parent, str) or not parent:
                return (f"새 카드는 자기 에픽 아래에만 만듦 — --parent <{hub} 에픽·하위 카드 id> 지정 필요(맡은 에픽 {', '.join(sorted(roots['epics'])) or '-'}). "
                        f"다른 에픽 아래 카드는 그 코디네이터에게 {REPLY_TO} 로 요청하거나 메인에 요청")
            ids = [parent] if ID_RE.match(parent) else []
        for cid in ids:
            if cid not in cards:
                continue  # 작업판에 없는 id(플래그 값·오타)는 bd 가 알아서 오류
            if not in_scope(cid, cards, roots):
                return reason_out(cid, hub, roots["epics"])
    return None


def main():
    try:
        reason = judge(json.load(sys.stdin))
    except Exception as e:  # 훅 오류가 작업을 막지 않게 통과 처리
        print(f"epic-scope-guard: 판정 실패 — 통과 처리 ({e})", file=sys.stderr)
        return
    if reason:
        deny(reason)


if __name__ == "__main__":
    main()
