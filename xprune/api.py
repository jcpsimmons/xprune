"""Minimal X API v2 client: OAuth 1.0a user context plus an app bearer token. Standard library only."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

API_ROOT = "https://api.x.com/2"
ENV_VARS = ("X_CONSUMER_KEY", "X_CONSUMER_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_TOKEN_SECRET", "X_BEARER_TOKEN")


class XAPIError(RuntimeError):
    def __init__(self, status: int, body: object):
        super().__init__(f"X API {status}: {json.dumps(body)[:500]}")
        self.status = status
        self.body = body


def quote(value: object) -> str:
    return urllib.parse.quote(str(value), safe="~")


def oauth1_header(method: str, url: str, consumer_key: str, consumer_secret: str, token: str,
                  token_secret: str, nonce: str | None = None, timestamp: int | None = None) -> str:
    """Build an OAuth 1.0a HMAC-SHA1 Authorization header. Query parameters are signed; JSON bodies are not."""
    base_url, _, query = url.partition("?")
    oauth = {
        "oauth_consumer_key": consumer_key,
        "oauth_nonce": nonce or secrets.token_hex(16),
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": str(timestamp or int(time.time())),
        "oauth_token": token,
        "oauth_version": "1.0",
    }
    params = urllib.parse.parse_qsl(query, keep_blank_values=True) + list(oauth.items())
    param_str = "&".join(f"{k}={v}" for k, v in sorted((quote(k), quote(v)) for k, v in params))
    base = "&".join([method.upper(), quote(base_url), quote(param_str)])
    key = f"{quote(consumer_secret)}&{quote(token_secret)}".encode()
    oauth["oauth_signature"] = base64.b64encode(hmac.new(key, base.encode(), hashlib.sha1).digest()).decode()
    return "OAuth " + ", ".join(f'{quote(k)}="{quote(v)}"' for k, v in sorted(oauth.items()))


def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


class Client:
    def __init__(self, consumer_key: str, consumer_secret: str, access_token: str, access_token_secret: str,
                 bearer_token: str, log=_log):
        self.consumer_key = consumer_key
        self.consumer_secret = consumer_secret
        self.access_token = access_token
        self.access_token_secret = access_token_secret
        self.bearer_token = bearer_token
        self.log = log

    @classmethod
    def from_env(cls, log=_log) -> "Client":
        missing = [name for name in ENV_VARS if not os.environ.get(name)]
        if missing:
            raise SystemExit("missing environment variables: " + ", ".join(missing) + " (see README, Setup)")
        return cls(*(os.environ[name] for name in ENV_VARS), log=log)

    def request(self, method: str, path: str, params: dict | None = None, body: dict | None = None,
                *, app: bool = False):
        """Return (status, headers, json). Sleeps through 429s until the window resets; retries 5xx and network errors."""
        url = API_ROOT + path + ("?" + urllib.parse.urlencode(params) if params else "")
        label = f"{method} {re.sub(r'/[0-9]+', '/:id', path)}"
        retries = 0
        while True:
            if app:
                auth = "Bearer " + self.bearer_token
            else:
                auth = oauth1_header(method, url, self.consumer_key, self.consumer_secret,
                                     self.access_token, self.access_token_secret)
            headers = {"Authorization": auth, "User-Agent": "xprune"}
            data = None
            if body is not None:
                data = json.dumps(body).encode()
                headers["Content-Type"] = "application/json"
            req = urllib.request.Request(url, data=data, method=method, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    raw = resp.read()
                    return resp.status, resp.headers, (json.loads(raw) if raw else {})
            except urllib.error.HTTPError as e:
                raw = e.read()
                try:
                    payload = json.loads(raw)
                except ValueError:
                    payload = {"raw": raw[:500].decode(errors="replace")}
                if e.code == 429:
                    reset = e.headers.get("x-rate-limit-reset")
                    wait = max(int(reset) - time.time(), 1) + 2 if reset else 60
                    self.log(f"rate limited on {label}; sleeping {int(wait)}s")
                    time.sleep(wait)
                    continue
                if e.code >= 500 and retries < 4:
                    retries += 1
                    time.sleep(5 * retries)
                    continue
                return e.code, e.headers, payload
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                if retries >= 4:
                    raise
                retries += 1
                self.log(f"network error on {label}: {e}; retrying")
                time.sleep(5 * retries)

    def get(self, path: str, params: dict | None = None, *, app: bool = False) -> dict:
        status, _, body = self.request("GET", path, params, app=app)
        if status != 200:
            raise XAPIError(status, body)
        return body

    def me(self) -> tuple[dict, str]:
        """Return (user, access_level). access_level is X's x-access-level header, e.g. 'read' or 'read-write'."""
        status, headers, body = self.request("GET", "/users/me", {"user.fields": "created_at,public_metrics"})
        if status != 200:
            raise XAPIError(status, body)
        return body["data"], headers.get("x-access-level", "")
