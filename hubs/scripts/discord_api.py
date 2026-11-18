"""Discord 봇 REST 공용 호출. 디스코드 구현(discord_notify·discord_poll·discord_pending)이 함께 씀.

토큰은 discord-bot.env(DISCORD_BOT_TOKEN, DISCORD_USER_ID)에서만 읽고 출력하지 않음.
DISCORD_API_BASE 로 주소 재정의(시험용 로컬 스텁).
반응 이모지는 슬랙 이름으로 바꿔 기록함. 폴러·메인·패널의 해석 표(white_check_mark·x·one…)를 그대로 쓰기 위함.
"""
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

import tls_ctx

API = os.environ.get("DISCORD_API_BASE", "https://discord.com/api/v10")
RETRIES = 3
RETRY_CAP = 20.0  # 429 한 번당 대기 상한(초)

# 유니코드 이모지 ↔ 슬랙 이름. 표에 없는 이모지는 유니코드 그대로 기록
EMOJI_NAME = {
    "✅": "white_check_mark", "❌": "x", "🛠️": "hammer_and_wrench", "🛠": "hammer_and_wrench",
    "1️⃣": "one", "2️⃣": "two", "3️⃣": "three", "4️⃣": "four",
}
NAME_EMOJI = {v: k for k, v in EMOJI_NAME.items() if k != "🛠"}


class ApiError(Exception):
    pass


def load_env(hub_dir):
    """discord-bot.env 를 읽어 (토큰, 사용자 ID). 없거나 비면 ApiError(원인·조치)"""
    path = os.path.join(hub_dir, "discord-bot.env")
    vals = {}
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                k, _, v = line.strip().partition("=")
                if k and not k.startswith("#"):
                    vals[k] = v.strip()
    except OSError:
        raise ApiError(f"{path} 없음 — DISCORD_BOT_TOKEN=·DISCORD_USER_ID= 를 채워 만든 뒤 chmod 600")
    tok, user = vals.get("DISCORD_BOT_TOKEN", ""), vals.get("DISCORD_USER_ID", "")
    if not tok or not user:
        raise ApiError(f"{path} 에 DISCORD_BOT_TOKEN·DISCORD_USER_ID 중 빈 값 — 채운 뒤 다시 실행")
    return tok, user


class Client:
    def __init__(self, token):
        self.token = token
        self.ctx = tls_ctx.ssl_context()

    def call(self, method, path, body=None, files=None, query=None):
        url = API + path + ("?" + urllib.parse.urlencode(query) if query else "")
        headers = {"Authorization": "Bot " + self.token, "User-Agent": "lrm-hubs (local, 1)"}
        data = None
        if files:
            data, ctype = _multipart(body or {}, files)
            headers["Content-Type"] = ctype
        elif body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        for attempt in range(RETRIES + 1):
            req = urllib.request.Request(url, data=data, method=method, headers=headers)
            try:
                with urllib.request.urlopen(req, context=self.ctx, timeout=30) as r:
                    raw = r.read()
                    return json.loads(raw) if raw else {}
            except urllib.error.HTTPError as e:
                raw = e.read()
                if e.code == 429 and attempt < RETRIES:
                    try:
                        wait = float(json.loads(raw).get("retry_after", 1))
                    except ValueError:
                        wait = 1.0
                    time.sleep(min(wait, RETRY_CAP))
                    continue
                try:
                    msg = json.loads(raw).get("message", "")
                except ValueError:
                    msg = ""
                raise ApiError(f"{method} {path} → HTTP {e.code} {msg}".strip())
            except urllib.error.URLError as e:
                raise ApiError(f"{method} {path} → 연결 실패 {e.reason}")
        raise ApiError(f"{method} {path} → 429 재시도 초과")

    def dm_channel(self, user_id):
        return self.call("POST", "/users/@me/channels", {"recipient_id": user_id})["id"]

    def me(self):
        return self.call("GET", "/users/@me")["id"]

    def post(self, ch, content, reply_to=None, files=None):
        body = {"content": content, "allowed_mentions": {"parse": []}}
        if reply_to:
            body["message_reference"] = {"message_id": reply_to, "fail_if_not_exists": False}
        if files:
            return self.call("POST", f"/channels/{ch}/messages", {"payload_json": json.dumps(body)}, files=files)
        return self.call("POST", f"/channels/{ch}/messages", body)

    def edit(self, ch, mid, content):
        return self.call("PATCH", f"/channels/{ch}/messages/{mid}", {"content": content})

    def delete(self, ch, mid):
        return self.call("DELETE", f"/channels/{ch}/messages/{mid}")

    def react(self, ch, mid, emoji):
        e = urllib.parse.quote(emoji)
        return self.call("PUT", f"/channels/{ch}/messages/{mid}/reactions/{e}/@me")

    def messages(self, ch, after=None, limit=100):
        q = {"limit": limit}
        if after:
            q["after"] = after
        return self.call("GET", f"/channels/{ch}/messages", query=q)

    def reactors(self, ch, mid, emoji):
        e = urllib.parse.quote(emoji)
        return [u["id"] for u in self.call("GET", f"/channels/{ch}/messages/{mid}/reactions/{e}", query={"limit": 100})]


def link(ch, mid):
    return f"https://discord.com/channels/@me/{ch}/{mid}"


def snowflake_at(sec):
    """해당 시각(초) 이후 메시지만 받는 after 값. 디스코드 기원 2015-01-01"""
    return str(max(0, int((sec * 1000 - 1420070400000)) << 22))


def snowflake_sec(sid):
    return ((int(sid) >> 22) + 1420070400000) / 1000


def _multipart(fields, files):
    b = "lrm" + uuid.uuid4().hex
    out = []
    for k, v in fields.items():
        out += [f"--{b}", f'Content-Disposition: form-data; name="{k}"', "", v]
    parts = "\r\n".join(out).encode() + b"\r\n" if out else b""
    for i, path in enumerate(files):
        name = os.path.basename(path).replace('"', "_")
        with open(path, "rb") as f:
            blob = f.read()
        parts += (f"--{b}\r\nContent-Disposition: form-data; name=\"files[{i}]\"; filename=\"{name}\"\r\n"
                  "Content-Type: application/octet-stream\r\n\r\n").encode() + blob + b"\r\n"
    return parts + f"--{b}--\r\n".encode(), "multipart/form-data; boundary=" + b
