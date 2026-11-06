#!/usr/bin/env python3
"""doc-kit 프로젝트별 설정 읽기.

사용:
  doc_config.py show [--project 이름] [--config 경로] [--repo 폴더] [--json]
  doc_config.py get 키... [같은 옵션] [--require]

프로젝트 선택 순서: --project(작업 지시) → DOC_KIT_PROJECT → origin 원격 주소 → 폴더 이름 → [default].
설정 파일 순서: --config → DOC_KIT_CONFIG → ~/.config/doc-kit/projects.toml. 파일이 없으면 기본값.
종료 코드: 0 정상, 2 설정 파일 문법 오류, 3 --require 키가 비어 있음.
"""
import argparse
import json
import os
import re
import subprocess
import sys

DEFAULTS = {
    "audience": "개발자",
    "out_dir": "docs/out",
    "name_rule": "{날짜}-{주제}",
}
KEYS = ("audience", "recipients", "channel", "out_dir", "name_rule",
        "ticket_pattern", "base_branch", "docs_dir", "precedent")
LIST_KEYS = ("match", "recipients", "precedent")
DEFAULT_PATH = os.path.join(os.path.expanduser("~"), ".config", "doc-kit", "projects.toml")


class ConfigError(Exception):
    pass


def _strip_comment(line):
    # 따옴표 밖의 # 이후 제거
    out, quote = [], None
    for ch in line:
        if quote:
            out.append(ch)
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
            out.append(ch)
        elif ch == "#":
            break
        else:
            out.append(ch)
    return "".join(out).strip()


def _parse_value(text, lineno):
    text = text.strip()
    if text.startswith("["):
        if not text.endswith("]"):
            raise ConfigError(f"{lineno}행: 배열이 닫히지 않음")
        items = re.findall(r'"((?:[^"\\]|\\.)*)"|\'([^\']*)\'', text[1:-1])
        return [(a if a or not b else b).replace('\\"', '"') for a, b in items]
    if len(text) >= 2 and text[0] == '"' and text[-1] == '"':
        return text[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    if len(text) >= 2 and text[0] == "'" and text[-1] == "'":
        return text[1:-1]
    raise ConfigError(f"{lineno}행: 지원하지 않는 값 형식")


def parse_mini(text):
    """3.11 미만용 간이 읽기. 이 틀이 쓰는 [섹션]·키 = 문자열·문자열 배열만 지원."""
    data = {}
    cur = data
    pending = None
    for no, raw in enumerate(text.splitlines(), 1):
        line = _strip_comment(raw)
        if pending is not None:
            pending[1] += " " + line
            if line.endswith("]"):
                cur[pending[0]] = _parse_value(pending[1], no)
                pending = None
            continue
        if not line:
            continue
        m = re.match(r"^\[\s*([^\]]+?)\s*\]$", line)
        if m:
            cur = data
            for part in re.findall(r'"[^"]*"|[^.\s]+', m.group(1)):
                cur = cur.setdefault(part.strip('"'), {})
            continue
        m = re.match(r"^([A-Za-z0-9_\-]+)\s*=\s*(.+)$", line)
        if not m:
            raise ConfigError(f"{no}행: 읽을 수 없는 줄")
        key, val = m.group(1), m.group(2)
        if val.startswith("[") and not val.endswith("]"):
            pending = [key, val]
            continue
        cur[key] = _parse_value(val, no)
    if pending is not None:
        raise ConfigError("배열이 닫히지 않은 채 파일이 끝남")
    return data


def load_file(path):
    with open(path, "rb") as f:
        raw = f.read()
    try:
        import tomllib
        return tomllib.loads(raw.decode("utf-8"))
    except ImportError:
        return parse_mini(raw.decode("utf-8"))
    except Exception as e:  # tomllib.TOMLDecodeError
        raise ConfigError(str(e))


def _git(repo, *args):
    try:
        r = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, timeout=10)
        return r.stdout.strip() if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def pick_project(projects, arg, repo):
    """(섹션 이름 또는 None, 출처) 반환."""
    if arg:
        return (arg, "arg") if arg in projects else (None, "arg-missing")
    env = os.environ.get("DOC_KIT_PROJECT", "")
    if env:
        return (env, "env") if env in projects else (None, "env-missing")
    origin = _git(repo, "remote", "get-url", "origin")
    root = _git(repo, "rev-parse", "--show-toplevel") or os.path.abspath(repo)
    folder = os.path.basename(root)
    for source, hay in (("origin", origin), ("dir", folder)):
        if not hay:
            continue
        for name, sec in projects.items():
            if any(m and m in hay for m in _as_list(sec.get("match"))):
                return name, source
    return None, "default"


def _as_list(v):
    if v is None:
        return []
    return list(v) if isinstance(v, (list, tuple)) else [v]


def resolve(project=None, config=None, repo="."):
    """적용값 dict 반환. 키 `_project`·`_source`·`_config` 에 판별 결과를 담는다."""
    path = config or os.environ.get("DOC_KIT_CONFIG") or DEFAULT_PATH
    out = dict(DEFAULTS)
    out["_config"] = path if os.path.isfile(path) else None
    if not out["_config"]:
        out["_project"], out["_source"] = "default", "no-config"
        return out
    data = load_file(path)
    out.update({k: v for k, v in data.get("default", {}).items() if k != "match"})
    projects = data.get("project", {})
    name, source = pick_project(projects, project, repo)
    out["_project"], out["_source"] = name or "default", source
    if name:
        out.update({k: v for k, v in projects[name].items() if k != "match"})
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="doc-kit 설정 읽기")
    ap.add_argument("cmd", choices=("show", "get"))
    ap.add_argument("keys", nargs="*")
    ap.add_argument("--project")
    ap.add_argument("--config")
    ap.add_argument("--repo", default=".")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--require", action="store_true")
    a = ap.parse_args(argv)
    try:
        cfg = resolve(a.project, a.config, a.repo)
    except (ConfigError, OSError) as e:
        print(f"설정 오류: {e}", file=sys.stderr)
        return 2
    if cfg["_source"] == "no-config":
        print("설정 없음 — 기본값", file=sys.stderr)
    elif cfg["_source"] in ("arg-missing", "env-missing"):
        print("프로젝트 섹션 없음 — 기본값", file=sys.stderr)
    if a.cmd == "show":
        if a.json:
            print(json.dumps(cfg, ensure_ascii=False, indent=2))
        else:
            for k in ("_project", "_source") + KEYS:
                v = cfg.get(k)
                if v in (None, "", []):
                    continue
                print(f"{k.lstrip('_')}={' / '.join(v) if isinstance(v, list) else v}")
        return 0
    rc = 0
    for k in a.keys:
        v = cfg.get(k)
        if v in (None, "", []):
            if a.require:
                print(f"{k} 값 없음", file=sys.stderr)
                rc = 3
            continue
        print("\n".join(v) if isinstance(v, list) else v)
    return rc


if __name__ == "__main__":
    sys.exit(main())
