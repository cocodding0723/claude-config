#!/usr/bin/env python3
"""기능 보고·압축 복원 읽기 전용 확인. Slack 호출·장부 수정 없음."""
import argparse
from collections import Counter
import datetime as dt
import json
import os
from pathlib import Path
import re
import sys
import time

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "board/lib"))
import board_flow
import board_said as said
import board_mirror
import feature_board as board
import hub_report


def json_file(path):
    data = json.loads(path.read_text()) if path.exists() else {}
    if not isinstance(data, dict):
        raise ValueError(f"상태 JSON 객체 아님: {path}")
    return data


def sessions(paths, since, now):
    boundaries = []
    seen = set()
    for path in paths:
        records = []
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    row = json.loads(line)
                except ValueError:
                    if line.endswith("\n"):
                        raise ValueError(f"세션 JSON 오류: {path}")
                    continue
                if isinstance(row, dict) and not row.get("isSidechain"):
                    records.append(row)
        for index, row in enumerate(records):
            at = said.stamp(row.get("timestamp"))
            key = row.get("uuid") or (str(Path(path).resolve()), at)
            if row.get("subtype") != "compact_boundary" or not since <= at <= now or key in seen:
                continue
            seen.add(key)
            first = next((r for r in records[index + 1:] if r.get("type") == "assistant"
                          and said.stamp(r.get("timestamp")) >= at), None)
            usage = (first or {}).get("message", {}).get("usage", {})
            tokens = sum(usage.get(k, 0) for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")) if usage else None
            boundaries.append(dict(at=at, tokens=tokens, session=Path(path).name))
    return sorted(boundaries, key=lambda r: r["at"])


def stage(text):
    names = "|".join(s.name for s in board_flow.STAGES)
    match = re.match(r"^(?:단계:\s*)?(\d+)\s+(" + names + r")\b", text)
    return int(match[1]) if match else None


def restored(boundaries, comments, now, typed=()):
    results = []
    for boundary in boundaries:
        at = boundary["at"]
        before = [r["text"] for r in comments if said.stamp(r.get("created_at")) < at]
        after = [r["text"] for r in comments if at <= said.stamp(r.get("created_at")) <= min(now, at + 1800)]
        book = said.ledger(before)
        pending = said.unsettled(book)
        fresh = said.unrecorded([(a, t) for a, t in typed if a < at], book, skip=board_flow.is_control)
        found, sources = set(), set()
        for text in after:
            recorded = said.SAID_RE.match(text.strip())
            if recorded:
                sources.add(int(recorded[3]))
            match = recorded or said.DONE_RE.match(text.strip())
            if match:
                found.update(int(n) for n in re.findall(r"\d+", match[1]))
        current = next((stage(t) for t in reversed(before) if stage(t) is not None), None)
        stage_ok = current is not None and any(stage(t) is not None and stage(t) >= current for t in after)
        missing = [f"지시 #{n}" for n in pending if n not in found]
        missing.extend(f"미기록 지시@{int(a)}" for a, _ in fresh if int(a) not in sources)
        if not stage_ok:
            missing.append("단계" if current is not None else "압축 전 단계 확인 불가")
        results.append(dict(**boundary, missing=missing, pending=now < at + 1800))
    return results


def inspect(hub, reader, home, since, now):
    if not hub.startswith("feat-") or not hub_report.hub_status.SLUG_RE.fullmatch(hub):
        raise ValueError("기능 허브 이름 오류 — feat-<slug> 지정")
    cards = reader.query("list", "--all", "--limit", "0")
    roots = [c for c in cards if c.get("issue_type") == "epic" and "feature:" + hub[5:] in [l.lower() for l in c.get("labels", [])]]
    if not roots:
        raise ValueError("기능 카드 없음 — 허브 이름 확인")
    root = sorted(roots, key=lambda c: (c.get("status") != "closed", c.get("created_at", "")))[-1]
    policy = Path(__file__).resolve().parents[1] / "board_mirror.tsv"
    mirror = board_mirror.Mirror(policy, dry=True)
    related = mirror.descendants(root, cards)
    records = [dict(r, issue_id=c["id"]) for c in related for r in reader.query("comments", c["id"])]
    records.sort(key=lambda r: said.stamp(r.get("created_at")))
    children = [c for c in related if c.get("parent") == root["id"] or any(
        d.get("type") == "parent-child" and d.get("depends_on_id") == root["id"] for d in c.get("dependencies", []))]
    direct = {root["id"], *(c["id"] for c in children)}
    text = board.render(root, children, [r for r in records if r["issue_id"] in direct], reader.stages())
    sender = json_file(home / "report-sender.state.json")
    topic = sender.get("topics", {}).get("card:" + root["id"], {})
    summary = topic.get("fmt", "").rsplit(":", 1)[-1]
    connected = bool(re.fullmatch(r"[0-9a-f]{64}", summary))
    equal = summary == board.summary(text)
    queue = hub_report.read_outbox(str(home / hub_report.OUTBOX_NAME))
    eligible = [r for c in related for r in mirror.filter_records(root, c["id"],
                [r for r in records if r["issue_id"] == c["id"]], queue)
                if since <= said.stamp(r.get("created_at")) <= now]
    pairs = {(r["issue_id"], str(r["id"])) for r in eligible}
    mirror_state = json_file(home / "board-mirror.state.json")
    def tracked(name):
        return {(cid, str(i)) for cid, ids in mirror_state.get(name, {}).items() for i in ids} & pairs
    baseline, skipped = tracked("_baseline_ids"), tracked("_skipped_ids")
    counts, sent = Counter(), Counter()
    for q in queue:
        if q.get("hub") != hub or q.get("at", 0) > now:
            continue
        keys = [(q.get("mirror_card", q.get("card")), str(i)) for i in q.get("mirror_ids", [])]
        counts.update(keys)
        delivered = sender.get("reports", {}).get(q["id"], {}).get("sent", 0)
        if delivered and delivered <= now:
            sent.update(keys)
    # 이전 장부에는 기준선·건너뜀 구분이 없음. 알려진 기록을 누락으로 단정하지 않음
    unclassified = tracked("_seen") - counts.keys() - baseline - skipped
    grace = {(r["issue_id"], str(r["id"])) for r in eligible if said.stamp(r.get("created_at")) > now - 240} - baseline - skipped - unclassified
    expected = pairs - baseline - skipped - unclassified - grace
    missing = sorted(expected - sent.keys())
    duplicates = sorted(key for key in expected if sent[key] > 1)
    ws = board_flow.coordinator_ws(root)
    paths = said.transcripts(ws) if ws else []
    boundaries = sessions(paths, since, now)
    root_comments = [r for r in records if r["issue_id"] == root["id"]]
    recovery = restored(boundaries, root_comments, now,
                        said.typed(ws, said.stamp(root.get("created_at"))) if ws else [])
    missed = sum(len(r["missing"]) for r in recovery if not r["pending"])
    unknown_tokens = sum(r["tokens"] is None and not r["pending"] for r in recovery)
    rows = [dict(item="현황판 = 카드 상태", status="미연결" if not connected else "통과" if equal else "실패",
                 detail="전송기 현황판 요약값 없음" if not connected else f"현재 {board.summary(text)} / 전송 {summary}"),
            dict(item="옮긴 기록 수 = 해당 기록 수", status="실패" if missing or duplicates else "통과",
                 detail=f"대기열 {len(expected & counts.keys())} / 보냄 {len(expected & sent.keys())} / 대상 {len(expected)}건, 중복 {len(duplicates)}건; 기준선 {len(baseline)}, 유예 {len(grace)}"),
            dict(item="압축 뒤 누락", status="확인 불가" if not paths else "실패" if missed else "대기" if any(r["pending"] for r in recovery) else "통과",
                 detail=f"누락 {missed}건, 30분 관찰 중 {sum(r['pending'] for r in recovery)}회"),
            dict(item="압축 횟수·직후 입력 토큰", status="확인 불가" if not paths else "실패" if unknown_tokens else "대기" if any(r["tokens"] is None for r in boundaries) else "통과",
                 detail=f"{len(boundaries)}회: " + ", ".join(str(r["tokens"]) if r["tokens"] is not None else "응답 미관찰" for r in boundaries)),
            dict(item="의도적 건너뜀·이전 장부", status="주의" if skipped or unclassified else "통과",
                 detail=f"3회 건너뜀 {len(skipped)}건, 이전 장부 분류 불가 {len(unclassified)}건")]
    return dict(hub=hub, card=root["id"], since=since, until=now, rows=rows,
                missing_records=[dict(card=c, id=i) for c, i in missing],
                duplicate_records=[dict(card=c, id=i) for c, i in duplicates],
                baseline_records=sorted(baseline), skipped_records=sorted(skipped),
                grace_records=sorted(grace), unclassified_records=sorted(unclassified),
                compactions=recovery, session_paths=paths)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("hub")
    parser.add_argument("--minutes", type=float, default=60)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--now", help="기준 시각 ISO")
    parser.add_argument("--state-dir", type=Path, default=Path(os.environ.get("LRM_HUB_DIR") or "~/.config/lrm-hubs").expanduser())
    parser.add_argument("--board")
    parser.add_argument("--bd")
    args = parser.parse_args()
    try:
        now = said.stamp(args.now) if args.now else time.time()
        if args.minutes <= 0 or now <= 0:
            raise ValueError("기간·기준 시각 오류 — 양수 분·ISO 시각 지정")
        result = inspect(args.hub, board.BdReader(args.board, args.bd), args.state_dir, now - args.minutes * 60, now)
        if args.json:
            print(json.dumps(result, ensure_ascii=False))
        else:
            print(f"{args.hub} 최근 {args.minutes:g}분\n판정 | 항목 | 결과")
            for row in result["rows"]:
                mark = "✗" if row["status"] in ("실패", "확인 불가") else "✓" if row["status"] == "통과" else "…"
                print(f"{mark} {row['status']} | {row['item']} | {row['detail']}")
            for record in result["missing_records"]:
                print(f"✗ 빠진 기록: {record['card']} {record['id']}")
            for compact in result["compactions"]:
                print(f"압축 {dt.datetime.fromtimestamp(compact['at'], dt.timezone.utc).isoformat()}: "
                      + ("관찰 중 " if compact["pending"] else "") + (", ".join(compact["missing"]) or "누락 없음"))
        return 1 if any(r["status"] in ("실패", "확인 불가") for r in result["rows"]) else 0
    except Exception as exc:
        print(f"감사 자료 조회 실패: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
