"""작업 크기(small/normal/large) 자동 판정. 손댈 파일 경로만 보고 정함. 기준값은 board/size_rules.tsv 한 곳.
순수 함수라 board-size 와 시험이 같은 판정을 씀. 크기 표시를 바꾸는 일은 board-stage 몫."""
import fnmatch
import os
from dataclasses import dataclass, field

SMALL, NORMAL, LARGE = "small", "normal", "large"
RULES_PATH = os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "size_rules.tsv")


@dataclass
class Rules:
    small_max_files: int = 0
    large_min_files: int = 0
    large_min_modules: int = 0
    module_skip: list = field(default_factory=list)
    asset_ext: list = field(default_factory=list)
    asset_path: list = field(default_factory=list)
    risk_path: list = field(default_factory=list)


def load_rules(path=RULES_PATH) -> Rules:
    r = Rules()
    with open(path, encoding="utf-8") as f:
        for line in f:
            if not line.strip() or line.startswith("#"):
                continue
            kind, value = (line.rstrip("\n").split("\t") + [""])[:2]
            cur = getattr(r, kind, None)
            if cur is None:
                raise ValueError(f"size_rules.tsv: 모르는 종류 '{kind}'")
            if isinstance(cur, list):
                cur.append(value.lower())
            else:
                setattr(r, kind, int(value))
    return r


def _module(path, skip):
    parts = [p for p in path.strip("/").split("/") if p not in ("", ".")]
    dirs = parts if path.endswith("/") else parts[:-1]
    for d in dirs:
        if d.lower() not in skip:
            return d
    return "."


def _hit(path, patterns):
    return next((p for p in patterns if fnmatch.fnmatchcase(path, p)), "")


def judge(paths, rules: Rules):
    """(크기, 이유 목록). 위험 영역은 작음을 막을 뿐 큼으로 올리지는 않음"""
    paths = list(dict.fromkeys(p for p in paths if p))
    low = [p.lower() for p in paths]
    reasons = []
    modules = sorted({_module(p, rules.module_skip) for p in paths})
    if len(paths) >= rules.large_min_files:
        reasons.append(f"파일 {len(paths)}개(≥{rules.large_min_files})")
    if len(modules) >= rules.large_min_modules:
        reasons.append(f"모듈 {len(modules)}개(≥{rules.large_min_modules}): {', '.join(modules)}")
    if reasons:
        return LARGE, reasons

    risks = sorted({h for p in low if (h := _hit(p, rules.risk_path))})
    reasons += [f"위험 영역: {h}" for h in risks]
    for p, l in zip(paths, low):
        if l.endswith("/"):
            reasons.append(f"범위가 넓음: {p}")
        elif os.path.splitext(l)[1] in rules.asset_ext:
            reasons.append(f"화면·데이터 에셋: {p}")
        elif h := _hit(l, rules.asset_path):
            reasons.append(f"화면·데이터 에셋({h}): {p}")
    if not paths:
        reasons.append("손댈 파일 없음")
    elif len(paths) > rules.small_max_files:
        reasons.append(f"파일 {len(paths)}개(>{rules.small_max_files})")
    if reasons:
        return NORMAL, reasons
    return SMALL, [f"파일 {len(paths)}개, 에셋·위험 영역 없음"]
