#!/usr/bin/env python3
"""PR 게임 데이터를 검증하고 HTML 한 파일로 만든다. 검증에 실패하면 파일을 만들지 않는다."""
import argparse, base64, json, mimetypes, random, re, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KINDS = {"code", "structure", "impact"}
# learn: 배경 지식 없이 설명 카드를 읽으며 푸는 문항. check: PR 을 열어 봐야 풀리는 확인 문항.
LEVELS = {"learn", "check"}
NODE_STATES = {"same", "changed", "added", "removed"}
EDGE_STATES = {"same", "added", "removed"}
MEDIA_LIMIT = 8 * 1024 * 1024


def parse_diff(text):
    """파일별로 +/- 줄을 모은다. 앞뒤 공백은 스니펫 들여쓰기 축약을 허용하려고 무시한다."""
    files, cur = {}, None
    for line in text.splitlines():
        m = re.match(r'diff --git "?a/(.*?)"? "?b/(.*?)"?$', line)
        if m:
            cur = files.setdefault(m.group(2), set())
        elif cur is not None and line[:1] in "+-" and not line.startswith(("+++ ", "--- ")):
            cur.add(line[0] + line[1:].strip())
    return files


def ref_parts(ref):
    path, _, line = ref.rpartition(":")
    return (path, int(line)) if path and line.isdigit() else (ref, None)


class Checker:
    def __init__(self, data, diff, repo):
        self.d, self.diff, self.repo = data, diff, repo
        self.errors, self.notes = [], []

    def err(self, msg):
        self.errors.append(msg)

    def need(self, obj, keys, where):
        for k in keys:
            if obj.get(k) in (None, "", []):
                self.err(f"{where}: {k} 없음")

    def check_ref(self, ref, where):
        path, line = ref_parts(ref)
        if self.repo:
            head = self.d["pr"].get("head", "")
            got = subprocess.run(["git", "-C", self.repo, "show", f"{head}:{path}"], capture_output=True)
            if got.returncode:
                self.err(f"{where}: {head[:10]} 에 {path} 없음")
            elif line and line > got.stdout.count(b"\n") + 1:
                self.err(f"{where}: {path} 는 {line}행까지 없음")
        elif path not in self.diff:
            self.err(f"{where}: {path} 가 diff 에 없음. 주변 파일이면 --repo 로 확인한다")

    def check_snippet(self, q, where):
        lines = q.get("code")
        if not lines:
            return
        path = q.get("file")
        if path not in self.diff:
            self.err(f"{where}: 스니펫 file {path} 가 diff 에 없음")
            return
        for line in lines:
            if line[:1] in "+-" and line[1:].strip() and line[0] + line[1:].strip() not in self.diff[path]:
                self.err(f"{where}: diff 에 없는 줄: {line.strip()[:70]}")

    def check_question(self, q, i, node_ids):
        where = f"Q{i + 1}"
        self.need(q, ["kind", "ask", "opts", "miss", "why", "ref"], where)
        if q.get("kind") not in KINDS:
            self.err(f"{where}: kind 는 code·structure·impact 중 하나")
        opts, ans, miss = q.get("opts") or [], q.get("ans"), q.get("miss") or []
        if len(opts) != 4 or len(set(opts)) != 4:
            self.err(f"{where}: 서로 다른 보기 4개가 필요")
            return
        if not isinstance(ans, int) or not 0 <= ans < 4:
            self.err(f"{where}: ans 는 0~3")
            return
        if len(miss) != 4 or any(bool(m) == (k == ans) for k, m in enumerate(miss)):
            self.err(f"{where}: miss 는 오답마다 근거를 쓰고 정답 자리는 빈 문자열")
        wrong = [len(o) for k, o in enumerate(opts) if k != ans]
        if len(opts[ans]) > max(wrong) * 1.15:
            self.err(f"{where}: 정답이 가장 긴 오답보다 15% 넘게 길다({len(opts[ans])} > {max(wrong)})")
        if q.get("ref"):
            self.check_ref(q["ref"], where)
        self.check_snippet(q, where)
        if q.get("hide") and q["hide"] not in node_ids:
            self.err(f"{where}: hide {q['hide']} 는 graph 에 없음")
        if q.get("hide") and q.get("diagram") not in ("before", "after"):
            self.err(f"{where}: hide 는 diagram 과 함께 쓴다")
        if "level" in q and q["level"] not in LEVELS:
            self.err(f"{where}: level 은 learn·check 중 하나")
        if q.get("level") == "learn":
            self.need(q, ["learn"], where)
            for t in q.get("terms") or []:
                self.need(t, ["t", "d"], f"{where} 용어")

    def check_set(self):
        qs = self.d.get("q") or []
        if not any("level" in q for q in qs):
            self.check_group(qs, "", 5)
            return
        if not all("level" in q for q in qs):
            self.err("level 을 쓰면 모든 문항에 단다")
            return
        learn = [q for q in qs if q["level"] == "learn"]
        check = [q for q in qs if q["level"] == "check"]
        if len(qs) > 24:
            self.err(f"문항은 24개 이하(현재 {len(qs)})")
        if not self.d.get("brief", {}).get("background"):
            self.err("brief.background: 배우기 문항을 쓰면 처음 보는 말 풀이가 필요")
        self.check_group(check, "확인 ", 4)
        # 배우기 문항은 답이 설명 카드와 화면에 있어도 된다. 대신 보기 모양만으로 풀리면 안 된다.
        if len(learn) < 3:
            self.err(f"배우기 문항은 3개 이상(현재 {len(learn)})")
        self.check_shape(learn, "배우기 ")

    def check_shape(self, qs, label):
        n = len(qs)
        whys = sum(1 for q in qs if str(q.get("ask", "")).startswith("왜"))
        if whys * 2 > n:
            self.err(f"{label}'왜'로 시작하는 문항은 절반 이하(현재 {whys}/{n})")
        longest = 0
        for q in qs:
            o, a = q.get("opts") or [], q.get("ans")
            if len(o) == 4 and isinstance(a, int) and 0 <= a < 4 and len(o[a]) == max(map(len, o)):
                longest += 1
        if longest * 2 > n:
            self.err(f"{label}정답이 최장 보기인 문항은 절반 이하(현재 {longest}/{n})")

    def check_group(self, qs, label, least):
        n = len(qs)
        if n < least:
            self.err(f"{label}문항은 {least}개 이상(현재 {n})")
            return
        kinds = [q.get("kind") for q in qs]
        need_s = max(2, round(n / 3))
        if kinds.count("structure") < need_s:
            self.err(f"{label}구조 문항이 {need_s}개 이상 필요(현재 {kinds.count('structure')})")
        if kinds.count("impact") < 1 or kinds.count("code") < 1:
            self.err(f"{label}코드·영향 문항이 각각 1개 이상 필요")
        closed = sum(1 for q in qs if not q.get("code") and not q.get("diagram"))
        if closed * 2 < n:
            self.err(f"{label}코드·도식 없는 문항이 절반 이상 필요(현재 {closed}/{n})")
        self.check_shape(qs, label)

    def check_graph(self):
        g = self.d.get("graph") or {}
        nodes, edges = g.get("nodes") or [], g.get("edges") or []
        if len(nodes) < 3 or not edges:
            self.err("graph: 노드 3개와 간선 1개 이상 필요")
        ids = [n.get("id") for n in nodes]
        if len(ids) != len(set(ids)):
            self.err("graph: id 중복")
        for n in nodes:
            where = f"노드 {n.get('id')}"
            self.need(n, ["id", "label", "state"], where)
            if n.get("state") not in NODE_STATES:
                self.err(f"{where}: state 오류")
            if not isinstance(n.get("layer"), int):
                self.err(f"{where}: layer 는 정수")
            if n.get("state") == "removed":
                continue
            if n.get("ref"):
                self.check_ref(n["ref"], where)
            else:
                self.err(f"{where}: ref 없음")
        for e in edges:
            if e.get("from") not in ids or e.get("to") not in ids:
                self.err(f"간선 {e.get('from')}→{e.get('to')}: 없는 노드")
            if e.get("state") not in EDGE_STATES:
                self.err(f"간선 {e.get('from')}→{e.get('to')}: state 오류")
        return set(ids)

    def run(self):
        d = self.d
        self.need(d.get("pr") or {}, ["num", "repo", "title", "url", "head"], "pr")
        b = d.get("brief") or {}
        self.need(b, ["summary", "problem", "change", "unchanged", "reading"], "brief")
        for r in b.get("reading") or []:
            self.need(r, ["ref", "why"], "읽는 순서")
            if r.get("ref"):
                self.check_ref(r["ref"], "읽는 순서")
        for t in b.get("background") or []:
            self.need(t, ["t", "d"], "처음 보는 말")
        self.need(d, ["why"], "최상위")
        ids = self.check_graph()
        for i, q in enumerate(d.get("q") or []):
            self.check_question(q, i, ids)
        self.check_set()
        if not self.repo:
            self.notes.append("--repo 없이 실행: 파일 존재는 diff 로만, 행 번호는 확인하지 않았다")
        return not self.errors


def embed_media(data, base):
    """화면 파일은 여기서만 data URI 로 바꾼다. 받은 사람이 파일 하나로 열 수 있어야 한다."""
    media = data.get("media")
    if not media:
        return
    for key in ("before", "after"):
        if media.get(key):
            p = (base / media[key]).resolve()
            raw = p.read_bytes()
            if len(raw) > MEDIA_LIMIT:
                sys.exit(f"화면 파일이 8MB 를 넘는다: {p}")
            mime = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
            media[key] = f"data:{mime};base64," + base64.b64encode(raw).decode()


def missing_glyphs(data):
    """내장 폰트에 없는 한글은 시스템 폰트로 대체돼 글자마다 굵기가 달라진다."""
    have = set((ROOT / "assets/jua-hangul.txt").read_text(encoding="utf8"))
    text = json.dumps(data, ensure_ascii=False)
    return sorted({c for c in text if "\uac00" <= c <= "\ud7a3"} - have)


def render(data):
    tpl = (ROOT / "assets/engine.html").read_text(encoding="utf8")
    font = base64.b64encode((ROOT / "assets/jua.woff2").read_bytes()).decode()
    # 코드 조각의 </script> 가 데이터 블록을 끝내지 못하게 한다.
    blob = json.dumps(data, ensure_ascii=False).replace("<", "\\u003c")
    return tpl.replace("__FONT__", font).replace("__DATA__", blob)


def question_text(data, q, qid, order):
    out = [f"{qid}. {q['ask']}"]
    if q.get("level") == "learn":
        out += ["먼저 알아 둘 것: " + q["learn"]] + [f"- {t['t']}: {t['d']}" for t in q.get("terms") or []]
    if q.get("code"):
        out += ["```"] + q["code"] + ["```"]
    if q.get("diagram"):
        g, hide = data["graph"], q.get("hide")
        name = {n["id"]: ("?" if n["id"] == hide else n["label"]) for n in g["nodes"]}
        out += ["구조 도식:"] + [f"- {name[e['from']]} → {name[e['to']]}" for e in g["edges"]]
    out += [f"  {'ABCD'[k]}) {q['opts'][j]}" for k, j in enumerate(order)]
    return "\n".join(out)


def brief_text(data):
    b, g = data["brief"], data["graph"]
    name = {n["id"]: n["label"] for n in g["nodes"]}
    lines = ["요약: " + " / ".join(b["summary"])]
    if b.get("background"):
        lines += ["처음 보는 말:"] + [f"- {t['t']}: {t['d']}" for t in b["background"]]
    lines += ["문제: " + b["problem"], "동작: " + b["change"], "안 바뀜: " + " / ".join(b["unchanged"]), "구조:"]
    lines += [f"- {name[e['from']]} → {name[e['to']]} ({e['state']}) {e.get('label', '')}" for e in g["edges"]]
    lines += ["읽는 순서:"] + [f"- {r['ref']} {r['why']}" for r in b["reading"]]
    return "\n".join(lines)


def blind_set(data, out_dir, seed):
    """플레이어가 PR 없이 보는 것만 내보낸다. 정답표는 시험 대상이 못 보게 따로 둔다.
    확인 문항은 questions.md, 배우기 문항은 learn.md 로 나눈다. 재는 방향이 반대이기 때문이다."""
    rng = random.Random(seed)
    out_dir.mkdir(parents=True, exist_ok=True)
    blocks, key = {"check": [], "learn": []}, {}
    for i, q in enumerate(data["q"]):
        order = list(range(4))
        rng.shuffle(order)
        qid = f"Q{i + 1}"
        blocks[q.get("level", "check")].append(question_text(data, q, qid, order))
        key[qid] = "ABCD"[order.index(q["ans"])]
    (out_dir / "questions.md").write_text("\n\n".join(blocks["check"]), encoding="utf8")
    if blocks["learn"]:
        (out_dir / "learn.md").write_text("\n\n".join(blocks["learn"]), encoding="utf8")
    (out_dir / "brief.md").write_text(brief_text(data), encoding="utf8")
    return key


def score(key_path, answers_path, data):
    key = json.loads(Path(key_path).read_text())
    ans = json.loads(Path(answers_path).read_text())
    right = [q for q, v in key.items() if ans.get(q) == v]
    by, lv = {}, {}
    for i, q in enumerate(data["q"]):
        if f"Q{i + 1}" not in ans:
            continue
        for bucket, k in ((by, q["kind"]), (lv, q.get("level", "check"))):
            a = bucket.setdefault(k, [0, 0])
            a[1] += 1
            a[0] += f"Q{i + 1}" in right
    asked = [q for q in key if q in ans]
    print(json.dumps({"right": len(right), "total": len(asked), "by_kind": by, "by_level": lv,
                      "wrong": [q for q in asked if q not in right]}, ensure_ascii=False))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("data")
    ap.add_argument("--diff", help="gh pr diff 출력 파일")
    ap.add_argument("--repo", help="head 커밋이 있는 로컬 저장소. 경로·행 번호를 확인한다")
    ap.add_argument("--out", help="만들 HTML 경로")
    ap.add_argument("--blind", help="변별력 시험 입력을 쓸 폴더")
    ap.add_argument("--key", help="정답표를 쓸 파일(시험 폴더 밖)")
    ap.add_argument("--score", nargs=2, metavar=("KEY", "ANSWERS"), help="시험 답안 채점")
    a = ap.parse_args()

    data_path = Path(a.data)
    data = json.loads(data_path.read_text(encoding="utf8"))
    if a.score:
        score(a.score[0], a.score[1], data)
        return
    if not a.diff:
        sys.exit("--diff 가 필요하다")
    diff = parse_diff(Path(a.diff).read_text(encoding="utf8", errors="replace"))
    chk = Checker(data, diff, a.repo)
    ok = chk.run()
    for n in chk.notes:
        print("참고:", n)
    if not ok:
        print(f"검증 실패 {len(chk.errors)}건", file=sys.stderr)
        for e in chk.errors:
            print(" -", e, file=sys.stderr)
        sys.exit(1)
    if a.blind:
        if not a.key:
            sys.exit("--blind 에는 --key 가 필요하다")
        key = blind_set(data, Path(a.blind), data["pr"]["num"])
        Path(a.key).write_text(json.dumps(key), encoding="utf8")
    if a.out:
        gaps = missing_glyphs(data)
        if gaps:
            print("경고: 폰트에 없는 한글, 이 글자만 시스템 폰트로 보인다:", "".join(gaps))
        embed_media(data, data_path.parent)
        html = render(data)
        Path(a.out).write_text(html, encoding="utf8")
        print(f"{a.out} {len(html) // 1024}KB, {len(data['q'])}문항")
    print("검증 통과")


if __name__ == "__main__":
    main()
