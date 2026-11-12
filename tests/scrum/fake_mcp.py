#!/usr/bin/env python3
"""가짜 MCP 서버(stdio). writeProbe 가 호출되면 argv[1] 파일에 CALLED 를 씀. 쓰기 도구 격리 시험용"""
import json
import sys
from pathlib import Path

for line in sys.stdin:
    try:
        d = json.loads(line)
    except ValueError:
        continue
    method, ident = d.get("method"), d.get("id")
    if ident is None:
        continue
    if method == "initialize":
        r = {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}, "serverInfo": {"name": "fake", "version": "1"}}
    elif method == "tools/list":
        r = {"tools": [{"name": "writeProbe", "description": "시험용. 표식 파일만 씀", "inputSchema": {"type": "object", "properties": {}}}]}
    elif method == "tools/call":
        Path(sys.argv[1]).write_text("CALLED")
        r = {"content": [{"type": "text", "text": "ok"}]}
    else:
        r = {}
    print(json.dumps({"jsonrpc": "2.0", "id": ident, "result": r}), flush=True)
