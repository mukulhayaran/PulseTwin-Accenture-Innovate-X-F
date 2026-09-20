"""Stdlib HTTP against PulseTwin's own API. No third-party dependency.

The agent reaches the plant ONLY through this file, and only through the endpoints in
`genai/tools.py`'s registry. It holds no database handle: `serve/db.py::open_ops_only()`
returns a *writable* connection on the Postgres path, so a direct-DB tool layer would
hand the agent write capability and the "read-only" claim would rest on nobody having
typed an INSERT. Over HTTP the agent's entire vocabulary is ten function names.

`transport` is injectable so tests drive the real FastAPI app through `TestClient`
without a socket, which is what lets the whole tool surface be tested with no API key.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Protocol

DEFAULT_TIMEOUT_S = 20.0

# (status_code, parsed_body)
Response = tuple[int, Any]


class Transport(Protocol):
    def __call__(
        self, method: str, url: str, headers: dict[str, str], body: bytes | None
    ) -> Response: ...


def urllib_transport(
    method: str, url: str, headers: dict[str, str], body: bytes | None
) -> Response:
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=DEFAULT_TIMEOUT_S) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, {"detail": raw[:500]}
    except urllib.error.URLError as exc:
        return 0, {"detail": f"connection error: {exc.reason}"}


class PulseTwinAPI:
    """A role-scoped, actor-tagged client for PulseTwin's public API."""

    def __init__(
        self,
        base_url: str,
        role: str,
        run_id: str,
        transport: Callable[..., Response] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.role = role
        self.run_id = run_id
        self._transport = transport or urllib_transport

    def _headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            # The role the tools act AS. Capped at the triggering user's own role by
            # serve/routers/triage.py::_cap_role -- an agent must never be a privilege
            # escalation path to data the person who started it cannot read.
            "X-PulseTwin-Role": self.role,
            # Keeps agent actions distinguishable from human ones in audit_log forever.
            # POST /api/whatif writes an audit row; without this the IATF 16949 trail
            # would be ambiguous about who acted.
            "X-PulseTwin-Actor": f"genai_triage/{self.run_id}",
        }

    def get(self, path: str, params: dict[str, Any] | None = None) -> Response:
        qs = f"?{urllib.parse.urlencode(params)}" if params else ""
        return self._transport("GET", f"{self.base_url}{path}{qs}", self._headers(), None)

    def post(self, path: str, payload: dict[str, Any]) -> Response:
        return self._transport(
            "POST",
            f"{self.base_url}{path}",
            self._headers(),
            json.dumps(payload).encode("utf-8"),
        )


def testclient_transport(client: Any) -> Callable[..., Response]:
    """Adapt a starlette/fastapi TestClient to the Transport protocol."""

    def _call(method: str, url: str, headers: dict[str, str], body: bytes | None) -> Response:
        # TestClient takes a path, not an absolute URL.
        parsed = urllib.parse.urlsplit(url)
        path = parsed.path + (f"?{parsed.query}" if parsed.query else "")
        resp = client.request(method, path, headers=headers, content=body)
        try:
            return resp.status_code, resp.json()
        except ValueError:
            return resp.status_code, {"detail": resp.text[:500]}

    return _call
