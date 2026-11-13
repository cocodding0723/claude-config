#!/usr/bin/env python3
"""문서 수집기: 허브 문서(html)를 모아 최신순 목록·도식 유무·결정 번호·답 상태를 만듦. 읽기 전용, 네트워크·전송 없음.

수집 위치(DEFAULT_PATTERNS 한 곳): 정리기록 폴더, 허브 보고함(hub-inbox), 작업 공간의 docs/out. LRM_DOC_ROOTS 로 재정의(os.pathsep 구분 glob).
허브 이름: 작업 공간 폴더 coord-<slug>-hub 의 slug, 정리기록은 '정리기록', hub-inbox 는 하위 폴더 이름.
도식 유무: doc-kit check.py 의 diagram_info 를 불러 씀(규칙 복제 없음). 검사기가 없거나 옛 버전이면 diagrams=None(판정 불가).
결정 번호·답 상태: slack-sent.jsonl 에서 문서 파일 첨부 기록(path)을 찾아 그 스레드 부모 카드의 decision_id 를 잇고,
답 상태는 panel_collect.card_state 규칙(slack-inbox.jsonl 의 반응)을 그대로 씀. 이름이 안 맞는 문서는 결정 없음.
파일 파싱 결과는 (mtime, size) 키로 캐시(doc-index-cache.json)해 다음 회차에 바뀐 파일만 다시 읽음.
"""
import glob
import hashlib
import importlib.util
import json
import os
import re
import sys
import threading
import time
from dataclasses import asdict, dataclass, field

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))

HUB_DIR = os.environ.get("LRM_HUB_DIR") or os.path.expanduser("~/.config/lrm-hubs")
DEFAULT_PATTERNS = (
    "~/Documents/90_정리기록/**/*.html",
    "~/.config/lrm-hubs/hub-inbox/**/*.html",
    "~/orca/workspaces/*/*/docs/out/**/*.html",
    "~/orca/*/docs/out/**/*.html",
)
CHECKER = os.environ.get("LRM_DOC_CHECK") or os.path.expanduser("~/.claude/skills/doc-kit/scripts/check.py")
CACHE = os.path.join(HUB_DIR, "doc-index-cache.json")
SENT, INBOX, DONE = (os.path.join(HUB_DIR, n) for n in ("slack-sent.jsonl", "slack-inbox.jsonl", "slack-done.txt"))
PARSE_MAX = 8 << 20          # 이보다 큰 문서는 도식 판정·썸네일 생략(이미지 내장 문서)
TITLE_HEAD = 64 << 10
SVG_RE = re.compile(r"<svg\b.*?</svg>", re.S | re.I)
SHAPE_RE = re.compile(r"<(?:rect|path|line|circle|ellipse|polygon|polyline|text)\b", re.I)
SVG_MIN_SHAPES = 3           # check.py SVG_MIN_SHAPES 와 같은 기준(아이콘 제외)
TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I)
H1_RE = re.compile(r"<h1[^>]*>(.*?)</h1>", re.S | re.I)
TAG_RE = re.compile(r"<[^>]+>")
SLUG_RE = re.compile(r"^coord-(.+)-hub$")
STATE_TEXT = {"closed": "답함", "hold": "보류", "conflict": "충돌", None: "대기"}


@dataclass
class DocRecord:
    id: str
    path: str
    title: str
    mtime: float
    hub: str
    kind: str = ""
    diagrams: int | None = None       # None = 판정 불가
    no_diagram_reason: str | None = None
    thumb: bool = False
    decisions: list = field(default_factory=list)  # [{"id": "D-..", "state": "대기|답함|보류|충돌"}]

    def public(self) -> dict:
        d = asdict(self)
        d.pop("path")  # 화면에는 경로 대신 id 만 줌(임의 경로 요청 차단)
        return d


def doc_id(path: str) -> str:
    return hashlib.sha1(path.encode("utf-8")).hexdigest()[:12]


def patterns() -> list[str]:
    env = os.environ.get("LRM_DOC_ROOTS")
    raw = env.split(os.pathsep) if env else list(DEFAULT_PATTERNS)
    return [os.path.expanduser(p) for p in raw if p]


def hub_of(path: str) -> str:
    parts = path.split(os.sep)
    if "90_정리기록" in parts:
        return "정리기록"
    if "hub-inbox" in parts:
        i = parts.index("hub-inbox")
        return parts[i + 1] if i + 2 < len(parts) else "hub-inbox"
    if "docs" in parts:
        i = len(parts) - 1 - parts[::-1].index("docs")  # 마지막 docs 폴더 바로 위가 작업 공간
        ws = parts[i - 1] if i >= 1 else ""
        m = SLUG_RE.match(ws)
        return m.group(1) if m else ws or "기타"
    return "기타"


def find_files() -> list[str]:
    seen, out = set(), []
    for pat in patterns():
        for p in glob.glob(pat, recursive=True):
            rp = os.path.realpath(p)
            if rp in seen or not os.path.isfile(rp) or os.sep + "node_modules" + os.sep in rp:
                continue
            seen.add(rp)
            out.append(rp)
    return out


def load_checker():
    """도식 판정 함수. 못 찾거나 diagram_info 가 없는 옛 검사기면 None"""
    try:
        spec = importlib.util.spec_from_file_location("doc_kit_check", CHECKER)
        mod = importlib.util.module_from_spec(spec)
        sys.path.insert(0, os.path.dirname(CHECKER))
        try:
            spec.loader.exec_module(mod)
        finally:
            sys.path.pop(0)
        return getattr(mod, "diagram_info", None)
    except Exception:
        return None


def clean_text(s: str) -> str:
    return re.sub(r"\s+", " ", TAG_RE.sub("", s)).strip()


def title_of(head: str, path: str) -> str:
    for rx in (TITLE_RE, H1_RE):
        m = rx.search(head)
        if m and clean_text(m.group(1)):
            return clean_text(m.group(1))[:120]
    return os.path.splitext(os.path.basename(path))[0]


def first_diagram(html: str) -> str | None:
    """도형 3개 이상인 첫 인라인 SVG. 스크립트·이벤트 속성은 제거해 돌려줌(img 로만 쓰지만 이중 방어)"""
    for m in SVG_RE.finditer(html):
        svg = m.group(0)
        if len(SHAPE_RE.findall(svg)) >= SVG_MIN_SHAPES:
            svg = re.sub(r"<script\b.*?</script>", "", svg, flags=re.S | re.I)
            svg = re.sub(r"\son\w+\s*=\s*(\"[^\"]*\"|'[^']*')", "", svg, flags=re.I)
            if "xmlns=" not in svg[:200]:
                svg = svg.replace("<svg", '<svg xmlns="http://www.w3.org/2000/svg"', 1)
            return svg
    return None


def parse_file(path: str, st: os.stat_result, checker) -> dict:
    """파일 하나의 메타. 읽기 실패는 error 칸으로 알리고 목록에는 남김"""
    meta = {"title": os.path.splitext(os.path.basename(path))[0], "kind": "", "diagrams": None,
            "no_diagram_reason": None, "thumb": False}
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            html = f.read(PARSE_MAX + 1)
    except OSError as e:
        meta["error"] = e.strerror or "읽기 실패"
        return meta
    meta["title"] = title_of(html[:TITLE_HEAD], path)
    if len(html) > PARSE_MAX:
        return meta
    meta["thumb"] = first_diagram(html) is not None
    if checker:
        try:
            info = checker(path)
            meta.update(kind=info.get("kind", ""), diagrams=info.get("diagrams"), no_diagram_reason=info.get("no_diagram_reason"))
        except Exception:
            pass
    return meta


def jsonl(path: str) -> list[dict]:
    out = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if isinstance(d, dict):
                    out.append(d)
    except OSError:
        pass
    return out


def decision_map(paths: list[str]) -> dict[str, list[dict]]:
    """경로 → [{id, state}]. 첨부 기록의 파일 이름이 문서 이름과 같거나 '<접두사>-<문서 이름>' 이면 같은 문서로 봄(접수 때 복사본 이름)"""
    import panel_collect as pc  # 답 상태 판정 재사용. 불러오기만 하고 수집은 돌리지 않음
    sent, inbox = jsonl(SENT), jsonl(INBOX)
    cards = {s["ts"]: s for s in sent if isinstance(s.get("ts"), str) and s.get("form_kind") == "decide"
             and isinstance(s.get("decision_id"), str) and pc.DECISION_ID_RE.fullmatch(s["decision_id"])}
    reactions = {}
    for d in inbox:
        # 버튼 답(type=button)도 이모지 답과 같은 pick 기록. panel_collect 는 반응만 세므로 여기서 둘 다 넘김
        if d.get("type") in ("reaction", "button") and isinstance(d.get("parent_ts"), str):
            reactions.setdefault(d["parent_ts"], []).append(d)
    try:
        with open(DONE, encoding="utf-8") as f:
            done = set(f.read().split())  # 사용자가 직접 닫은 카드
    except OSError:
        done = set()
    by_name: dict[str, set] = {}
    for s in sent:
        if s.get("kind") != "file" or not s.get("path"):
            continue
        card = cards.get(s.get("thread_ts") or "")
        if card:
            by_name.setdefault(os.path.basename(s["path"]), set()).add(card["ts"])
    out = {}
    for p in paths:
        base, found = os.path.basename(p), {}
        for name, tss in by_name.items():
            if name == base or name.endswith("-" + base):
                for ts in tss:
                    c = cards[ts]
                    st = pc.card_state(c, reactions.get(ts, []))
                    found[c["decision_id"]] = STATE_TEXT["closed" if st is None and ts in done else st]
        out[p] = [{"id": k, "state": v} for k, v in sorted(found.items())]
    return out


class Index:
    """스레드 안전 목록. refresh() 가 바뀐 파일만 다시 읽음"""

    def __init__(self):
        self._lock = threading.Lock()
        self._docs: list[DocRecord] = []
        self._paths: dict[str, str] = {}
        self._cache = self._load_cache()
        self.scanned_at = 0.0

    def _load_cache(self) -> dict:
        try:
            with open(CACHE, encoding="utf-8") as f:
                d = json.load(f)
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save_cache(self):
        tmp = CACHE + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._cache, f, ensure_ascii=False)
            os.replace(tmp, CACHE)
        except OSError:
            pass  # 캐시는 속도용. 못 써도 다음 회차에 다시 읽을 뿐

    def refresh(self) -> int:
        checker = load_checker()
        recs, cache = [], {}
        for p in find_files():
            try:
                st = os.stat(p)
            except OSError:
                continue
            key = f"{st.st_mtime_ns}:{st.st_size}:{bool(checker)}"
            hit = self._cache.get(p)
            meta = hit["meta"] if hit and hit.get("key") == key else parse_file(p, st, checker)
            cache[p] = {"key": key, "meta": meta}
            recs.append(DocRecord(id=doc_id(p), path=p, title=meta["title"], mtime=st.st_mtime, hub=hub_of(p),
                                  kind=meta.get("kind", ""), diagrams=meta.get("diagrams"),
                                  no_diagram_reason=meta.get("no_diagram_reason"), thumb=meta.get("thumb", False)))
        try:
            dm = decision_map([r.path for r in recs])
        except Exception:
            dm = {}
        for r in recs:
            r.decisions = dm.get(r.path, [])
        recs.sort(key=lambda r: r.mtime, reverse=True)
        with self._lock:
            self._docs, self._paths, self._cache = recs, {r.id: r.path for r in recs}, cache
            self.scanned_at = time.time()
        self._save_cache()
        return len(recs)

    def snapshot(self) -> list[DocRecord]:
        with self._lock:
            return list(self._docs)

    def path_of(self, did: str) -> str | None:
        """id → 경로. 수집된 문서만 열림(임의 경로 거부)"""
        with self._lock:
            return self._paths.get(did)

    def thumb_of(self, did: str) -> str | None:
        p = self.path_of(did)
        if not p:
            return None
        try:
            with open(p, encoding="utf-8", errors="replace") as f:
                return first_diagram(f.read(PARSE_MAX))
        except OSError:
            return None
