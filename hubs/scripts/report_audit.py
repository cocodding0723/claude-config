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
                if not row.get("isSidechain"):
                    records.append(row)
        seen = set()
        for index, row in enumerate(records):
            at = said.stamp(row.get("timestamp"))
            if row.get("subtype") != "compact_boundary" or not since <= at <= now or at in seen:
                continue
            seen.add(at)
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
    roots = [c for c in cards if c.get("issue_type") == "epic" and "feature:" + hub[5:] in c.get("labels", [])]
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
    topic = json_file(home / "report-sender.state.json").get("topics", {}).get("card:" + root["id"], {})
    summary = topic.get("fmt", "").rsplit(":", 1)[-1]
    connected = bool(re.fullmatch(r"[0-9a-f]{64}", summary))
    equal = summary == board.summary(text)
    expected = {(r["issue_id"], str(r["id"])) for r in records if since <= said.stamp(r.get("created_at")) <= now
                and r.get("text", "").strip() and not re.search(mirror.cfg["skip_pattern"], r["text"].strip())}
    queue = hub_report.read_outbox(str(home / hub_report.OUTBOX_NAME))
    counts = Counter((q.get("mirror_card", q.get("card")), str(i)) for q in queue if q.get("hub") == hub
                     and q.get("at", 0) <= now for i in q.get("mirror_ids", []))
    missing = sorted(expected - counts.keys())
    duplicates = sorted(key for key in expected if counts[key] > 1)
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
                 detail=f"{len(expected) - len(missing)}/{len(expected)}건, 중복 {len(duplicates)}건"),
            dict(item="압축 뒤 누락", status="확인 불가" if not paths else "실패" if missed else "대기" if any(r["pending"] for r in recovery) else "통과",
                 detail=f"누락 {missed}건, 30분 관찰 중 {sum(r['pending'] for r in recovery)}회"),
            dict(item="압축 횟수·직후 입력 토큰", status="확인 불가" if not paths else "실패" if unknown_tokens else "대기" if any(r["tokens"] is None for r in boundaries) else "통과",
                 detail=f"{len(boundaries)}회: " + ", ".join(str(r["tokens"]) if r["tokens"] is not None else "응답 미관찰" for r in boundaries))]
    return dict(hub=hub, card=root["id"], since=since, until=now, rows=rows,
                missing_records=[dict(card=c, id=i) for c, i in missing],
                duplicate_records=[dict(card=c, id=i) for c, i in duplicates],
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
    except (OSError, ValueError, RuntimeError, ImportError) as exc:
        print(f"감사 자료 조회 실패: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
