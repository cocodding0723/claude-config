#!/usr/bin/env python3
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
import shutil
import subprocess
import sys
import time
from uuid import UUID

import hud
from hud_view import use_color
from project_view import render


def query(executable, argv, field):
    if argv[0] == "orchestration" and argv[1] != "run-list":
        return hud.query(executable, argv[1:])
    process = subprocess.run([executable, *argv, "--json"], capture_output=True,
                             text=True, timeout=10, check=False)
    try:
        envelope = json.loads(process.stdout)
        result = envelope["result"]
        if process.returncode or envelope.get("ok") is not True:
            raise ValueError
        if not isinstance(result[field], list) or not all(isinstance(r, dict) for r in result[field]):
            raise ValueError
        return result
    except (ValueError, KeyError, TypeError):
        raise RuntimeError(f"{argv[0]} {argv[1]}: 응답 형식 오류") from None


def pages(executable, argv, field, deadline=None):
    rows, seen, errors, result, notices, omitted = {}, set(), [], {}, [], set()
    for _ in range(256):
        try:
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError("refresh deadline")
            result = query(executable, argv, field)
            notices.extend(str(w) for w in result.get("warnings", []))
            omitted.update((result.get("hostScope") or {}).get("omittedHostIds", []))
            for row in result[field]:
                key = row.get("dispatchId") or row.get("id")
                if not isinstance(key, str) or not key:
                    raise ValueError
                rows[key] = row
            page = result.get("page", {})
            cursor = result.get("nextCursor") if field == "runs" else page.get("nextCursor")
            if cursor is not None and not isinstance(cursor, str):
                raise ValueError
            more = bool(cursor) if field == "runs" else page.get("hasMore", False)
            if not more:
                break
            if not isinstance(cursor, str) or not cursor or cursor in seen:
                raise ValueError
            seen.add(cursor)
            argv = argv[:argv.index("--cursor")] if "--cursor" in argv else argv
            argv = [*argv, "--cursor", cursor]
        except (RuntimeError, OSError, subprocess.TimeoutExpired, ValueError, TypeError, AttributeError) as error:
            errors.append(f"{field}: {'조회 기한 초과' if isinstance(error, TimeoutError) else '페이지 조회 불가'}·일부 결과")
            break
    else:
        errors.append(f"{field}: 페이지 상한·일부 결과")
    return {**result, field: list(rows.values()), "_errors": errors,
            "warnings": list(dict.fromkeys(notices)),
            "hostScope": {"omittedHostIds": sorted(omitted)}}


def mappings(projects, worktrees):
    repos, spaces = {}, {}
    for project in projects:
        for repo in project.get("sourceRepoIds", []):
            repos.setdefault(repo, set()).add(project["id"])
    for worktree in worktrees:
        ids = set(repos.get(worktree.get("repoId"), set()))
        if worktree.get("projectId"):
            ids.add(worktree["projectId"])
        spaces.setdefault((host_id(worktree), worktree.get("id")), set()).update(ids)
    return repos, spaces


def host_id(row):
    return row.get("executionHostId") or row.get("hostId") or (row.get("identity") or {}).get("executionHostId")


def workspace(identity, repos, spaces, host=None):
    if not isinstance(identity, str) or not identity or not host:
        return set()
    if (host, identity) in spaces:
        return spaces[host, identity]
    if host != "local" or any(key[1] == identity for key in spaces):
        return set()
    repo, separator, path = identity.partition("::")
    return repos.get(repo, set()) if separator and path else set()


def affiliation(worker, terminals, repos, spaces):
    resource, projection = worker.get("resource") or {}, worker.get("projection") or {}
    host = (projection.get("host") or {}).get("id")
    claims = {h for h in (host, host_id(worker), host_id(resource), host_id(projection.get("workspace") or {})) if h}
    if not host or len(claims) > 1:
        return set(), True
    terminal = terminals.get((host, worker.get("agentTerminalHandle")), {})
    references = [resource.get("worktreeId"), (projection.get("workspace") or {}).get("id"),
                  terminal.get("worktreeId")]
    groups = [workspace(ref, repos, spaces, host) for ref in references if ref]
    return set().union(*groups), not groups or any(not g for g in groups)


def membership(run, workers, terminals, repos, spaces, project):
    coordinator = terminals.get(("local", run.get("coordinator_handle")), {})
    groups = [workspace(coordinator.get("worktreeId"), repos, spaces, host_id(coordinator))]
    unknown, worker_unknown = not groups[0], False
    for worker in workers:
        group, missing = affiliation(worker, terminals, repos, spaces)
        groups.append(group)
        unknown |= missing
        worker_unknown |= missing
    identities = set().union(*groups)
    conflict = any(len(group) > 1 for group in groups)
    kind = ("conflict" if conflict else "mixed" if len(identities) > 1 else
            "project" if project in identities else "external" if identities else "unknown")
    return {"kind": kind, "included": project in identities, "uncertain": unknown,
            "restricted": worker_unknown or kind in ("mixed", "conflict")}


class ProjectHud:
    def __init__(self, executable, repo, project, refresh_timeout=45):
        self.executable, self.repo, self.project = executable, repo, project
        self.cache, self.discovery, self.runs, self.discovered_at = {}, 0, [], 0
        self.refresh_timeout, self.discovery_attempted_at, self.discovery_failed = refresh_timeout, 0, False

    def load(self, key, argv, field, ttl=0, deadline=None):
        old = self.cache.get(key)
        if old and time.monotonic() < old["expires"]:
            return old
        try:
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError("refresh deadline")
            data = (pages(self.executable, argv, field, deadline) if field in ("runs", "workers")
                    else query(self.executable, argv, field))
            errors = data.pop("_errors", [])
        except (RuntimeError, OSError, subprocess.TimeoutExpired) as error:
            data, errors = {}, [f"{key}: {'조회 기한 초과' if isinstance(error, TimeoutError) else '조회 불가'}"]
        if errors and old:
            stamp = time.strftime("%H:%M:%S", time.localtime(old["at"])) if old["at"] else "없음"
            errors.append(f"{key}: stale/cache 마지막 성공 {stamp}")
        record = {"data": old["data"] if errors and old else data,
                  "at": old["at"] if errors and old else 0 if errors else time.time(),
                  "errors": errors, "expires": time.monotonic() + (5 if errors else ttl)}
        self.cache[key] = record
        return record

    def refresh(self):
        deadline = time.monotonic() + self.refresh_timeout
        with ThreadPoolExecutor(max_workers=8) as pool:
            if time.monotonic() >= self.discovery:
                requests = [
                    ("projects", ["project", "list"], "projects"),
                    ("spaces", ["worktree", "list", "--repo", self.repo, "--limit", "1000"], "worktrees"),
                    ("terminals", ["terminal", "list", "--limit", "1000"], "terminals"),
                    ("runs", ["orchestration", "run-list", "--limit", "100"], "runs")]
                list(pool.map(lambda args: self.load(*args, deadline=deadline), requests))
                self.runs = self.cache["runs"]["data"].get("runs", [])
                list(pool.map(lambda run: self.load("workers:" + run["id"],
                    ["orchestration", "worker-list", "--run", run["id"], "--include-remote"],
                    "workers", 5, deadline=deadline), self.runs))
                self.discovery = time.monotonic() + 30
                self.discovery_attempted_at = time.time()
                scan_keys = ["projects", "spaces", "terminals", "runs", *("workers:" + r["id"] for r in self.runs)]
                self.discovery_failed = any(self.cache[k]["errors"] or self.cache[k]["data"].get("truncated") for k in scan_keys)
                if not self.discovery_failed:
                    self.discovered_at = self.discovery_attempted_at
            repos, spaces = mappings(self.cache["projects"]["data"].get("projects", []),
                                     self.cache["spaces"]["data"].get("worktrees", []))
            terminals = {(host_id(t), t["handle"]): t for t in self.cache["terminals"]["data"].get("terminals", [])}
            entries, requests = [], []
            for run in self.runs:
                record = self.cache["workers:" + run["id"]]
                workers = record["data"].get("workers", [])
                scope = membership(run, workers, terminals, repos, spaces, self.project)
                entry = {"id": run["id"], "run": run, "scope": scope}
                entries.append(entry)
                if scope["included"]:
                    previous = self.cache.get("tasks:" + run["id"], {}).get("data", {}).get("tasks", [])
                    ttl = 30 if previous and all(t.get("status") == "completed" for t in previous) else 5
                    for name, command in (("run", "run-show"), ("tasks", "task-list"),
                                          ("gates", "gate-list"), ("workers", "worker-list")):
                        argv = ["orchestration", command, "--id" if name == "run" else "--run", run["id"]]
                        argv += ["--include-remote"] if name == "workers" else ["--brief"] if name == "tasks" else []
                        requests.append((name + ":" + run["id"], argv, name, ttl))
            list(pool.map(lambda args: self.load(*args, deadline=deadline), requests))
        warnings = []
        for key in ("projects", "spaces", "terminals", "runs"):
            record = self.cache[key]
            warnings.extend(record["errors"])
            data = record["data"]
            if data.get("truncated"):
                warnings.append(f"{key}: 일부 결과·상한1000")
            omitted = (data.get("hostScope") or {}).get("omittedHostIds", [])
            if omitted:
                warnings.append(f"{key}: 누락호스트 " + ", ".join(omitted))
        for entry in entries:
            run_id = entry["id"]
            entry["records"] = {name: self.cache[name + ":" + run_id] for name in
                                ("run", "tasks", "gates", "workers") if name + ":" + run_id in self.cache}
            entry["errors"] = [e for record in entry["records"].values() for e in record["errors"]]
            worker_data = entry["records"]["workers"]["data"]
            warnings.extend(f"{run_id}: {w}" for w in worker_data.get("warnings", []))
            if (worker_data.get("hostScope") or {}).get("omittedHostIds"):
                warnings.append(f"{run_id}: 누락호스트 " + ", ".join(worker_data["hostScope"]["omittedHostIds"]))
            workers = worker_data.get("workers", [])
            entry["scope"] = membership(entry["run"], workers, terminals, repos, spaces, self.project)
            tasks_record = entry["records"].get("tasks", {})
            tasks = tasks_record.get("data", {}).get("tasks", [])
            current = {(t.get("id"), t.get("dispatch_id")) for t in tasks if t.get("dispatch_id")}
            allowed = {w.get("taskId") for w in workers if affiliation(w, terminals, repos, spaces) == ({self.project}, False)
                       and (w.get("taskId"), w.get("dispatchId")) in current}
            entry["tasks"] = [t for t in tasks if not entry["scope"]["restricted"] or t.get("id") in allowed]
            entry["excluded"] = len(tasks) - len(entry["tasks"])
        if repos.get(self.repo[3:]) != {self.project}:
            warnings.append("repo/project 정확 매핑 확인 불가·충돌")
        return {"project": self.project, "entries": entries, "warnings": warnings,
                "discovered_at": self.discovered_at, "discovery_attempted_at": self.discovery_attempted_at,
                "discovery_failed": self.discovery_failed}


def main():
    parser = argparse.ArgumentParser(description="Orca 프로젝트 전체 읽기 전용 HUD")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--orca", default="/usr/local/bin/orca")
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--details", action="store_true")
    parser.add_argument("--color", choices=("auto", "always", "never"), default="auto")
    args = parser.parse_args()
    try:
        if not args.repo.startswith("id:") or str(UUID(args.repo[3:])) != args.repo[3:]:
            raise ValueError
    except ValueError:
        parser.error("--repo 형식: id:<uuid>")
    collector = ProjectHud(args.orca, args.repo, args.project)
    interactive = args.watch and sys.stdout.isatty()
    if interactive:
        print("\x1b[?1049h", end="", flush=True)
    try:
        while True:
            started = time.monotonic()
            snapshot = collector.refresh()
            size = shutil.get_terminal_size((110, 40))
            output = render(snapshot, time.time(), size.columns,
                            size.lines if interactive or not args.details else None,
                            use_color(args.color, sys.stdout.isatty(), os.environ.get("NO_COLOR")), args.details)
            print(("\x1b[H\x1b[2J" if interactive else "") + output, flush=True)
            if not args.watch:
                return int(bool(snapshot["warnings"] or any(e["errors"] for e in snapshot["entries"])))
            time.sleep(max(0, 5 - (time.monotonic() - started)))
    except KeyboardInterrupt:
        return 0
    finally:
        if interactive:
            print("\x1b[?1049l", end="", flush=True)


if __name__ == "__main__":
    sys.exit(main())
