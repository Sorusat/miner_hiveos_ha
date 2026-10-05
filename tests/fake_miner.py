"""Local test server: emulates a HiveOS miner with HTTP digest auth.

Used to prove the client authenticates correctly, including the regression
where Home Assistant's shared session silently dropped the digest header.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

REALM = "test"
USER = "root"
PASSWORD = "root"

FULL_STATUS: dict[str, Any] = {
    "summary": {
        "elapsed": "11",
        "ghs5s": "9697.69",
        "ghsav": "56624.85",
        "accepted": "12",
        "rejected": "2",
    },
    "pools": [
        {"url": "stratum+tcp://pool.example:443", "status": "Alive"},
        {"url": "*", "status": "Alive"},
    ],
    "devs": [
        {
            "index": "1",
            "temp": "44",
            "freq": (
                "fan_num=0,temp1=44,temp2=45,temp3=39,"
                "temp_chip1=49-53-49-53,temp_chip2=50-53,temp_max=45,"
                "total_power=1599,miner_version=49.0.1.3"
            ),
        },
        {"index": "2", "temp": "45", "freq": "temp1=44,temp2=45,total_power=1599"},
        {"index": "3", "temp": "39", "freq": "temp1=44,temp2=45,total_power=1599"},
    ],
}

ZEROED_STATUS: dict[str, Any] = {
    "summary": {
        "elapsed": "0",
        "ghs5s": "0",
        "ghsav": "0",
        "accepted": "0",
        "rejected": "0",
        "diffs": "",
    },
    "pools": [],
    "devs": [],
}

# Mutable state the test flips between requests.
STATE: dict[str, Any] = {"status": FULL_STATUS, "log": "bmminer ready", "calls": []}

CGI_ACTIONS = {
    "/cgi-bin/get_miner_status.cgi",
    "/cgi-bin/start_miner.cgi",
    "/cgi-bin/stop_miner.cgi",
    "/cgi-bin/resume_miner.cgi",
}


def _parse_digest(header: str) -> dict[str, str] | None:
    if not header.lower().startswith("digest "):
        return None
    out: dict[str, str] = {}
    for part in header[7:].split(","):
        if "=" in part:
            key, _, value = part.partition("=")
            out[key.strip()] = value.strip().strip('"')
    return out


def _expected_response(params: dict[str, str]) -> str:
    """Recompute the digest response the client should have sent.

    HA1 = user:realm:password, HA2 = method:uri, which is why the method
    matters here: hashing ':uri' instead makes every request look forged.
    """
    ha1 = hashlib.md5(f"{USER}:{REALM}:{PASSWORD}".encode()).hexdigest()
    ha2 = hashlib.md5(f"GET:{params.get('uri', '')}".encode()).hexdigest()
    if params.get("qop"):
        return hashlib.md5(
            f"{ha1}:{params['nonce']}:{params['nc']}:{params['cnonce']}:"
            f"auth:{ha2}".encode()
        ).hexdigest()
    return hashlib.md5(f"{ha1}:{params['nonce']}:{ha2}".encode()).hexdigest()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args: Any) -> None:  # silence test output
        return

    def _challenge(self) -> None:
        nonce = "0123456789abcdef"
        body = b"401 Unauthorized"
        self.send_response(401)
        self.send_header("WWW-Authenticate", f'Digest realm="{REALM}", nonce="{nonce}", qop="auth"')
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorised(self) -> bool:
        header = self.headers.get("Authorization", "")
        if not header:
            return False
        params = _parse_digest(header)
        if not params:
            return False
        if params.get("username") != USER:
            return False
        return params.get("response") == _expected_response(params)

    def _json(self, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _text(self, payload: str) -> None:
        body = payload.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        STATE["calls"].append((self.path, bool(self.headers.get("Authorization"))))

        if self.path not in CGI_ACTIONS:
            body = b"404 Not Found"
            self.send_response(404)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if not self._authorised():
            self._challenge()
            return

        if self.path.endswith("get_miner_status.cgi"):
            self._json(STATE["status"])
        elif self.path.endswith("start_miner.cgi"):
            STATE["status"] = ZEROED_STATUS
            STATE["log"] = "Starting... run time"
            self._text("OK")
        elif self.path.endswith("stop_miner.cgi"):
            STATE["status"] = ZEROED_STATUS
            STATE["log"] = "INFO SUSPENDED by api"
            self._text("OK")
        else:
            STATE["status"] = ZEROED_STATUS
            STATE["log"] = "Starting... run time"
            self._text("OK")


class FakeMiner:
    """Runs Handler in a background thread and reports its host/port.

    ThreadingHTTPServer matters here: aiohttp keeps the connection alive, so a
    single-threaded server would block on the next request forever.
    """

    def __init__(self) -> None:
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def host(self) -> str:
        return f"127.0.0.1:{self._server.server_address[1]}"

    def start(self) -> "FakeMiner":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    def __enter__(self) -> "FakeMiner":
        return self.start()

    def __exit__(self, *exc: Any) -> None:
        self.stop()