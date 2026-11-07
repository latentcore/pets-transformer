"""Test double for an NGSI-LD broker - NOT a FIWARE component.

Implements only what `run.py fiware` calls (batch upsert, GET entities by type + simple q + scopeQ, GET entity by id,
keyValues rendering) so the publish/consume code path can be smoke-tested offline. The real run targets a FIWARE context broker.

    python tools/ngsi_ld_mock.py --selftest     # starts the mock on a free port, publishes, consumes, stops
"""
from __future__ import annotations

import json
import re
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

STORE: dict[str, dict] = {}


def _kv(e: dict) -> dict:
    out = {"id": e["id"], "type": e["type"]}
    if "scope" in e:
        out["scope"] = e["scope"]
    for k, v in e.items():
        if isinstance(v, dict) and v.get("type") == "Property":
            out[k] = v.get("value")
        elif isinstance(v, dict) and v.get("type") == "Relationship":
            out[k] = v.get("object")
    return out


def _match(e: dict, q: str) -> bool:
    m = re.fullmatch(r'(\w+)(==|>=|<=|>|<)(.+)', q or "")
    if not m:
        return True
    attr, op, raw = m.groups()
    v = _kv(e).get(attr)
    if raw.startswith('"'):
        return op == "==" and v == raw.strip('"')
    try:
        x, y = float(v), float(raw)
    except (TypeError, ValueError):
        return False
    return {"==": x == y, ">=": x >= y, "<=": x <= y, ">": x > y, "<": x < y}[op]


def _scope_one(s: str, pat: str) -> bool:
    ps, ss = pat.strip("/").split("/"), s.strip("/").split("/")
    if ps and ps[-1] == "#":  # the scope itself and everything below it
        ps = ps[:-1]
        if len(ss) < len(ps):
            return False
        ss = ss[:len(ps)]
    elif len(ss) != len(ps):
        return False
    return all(p == "+" or p == x for p, x in zip(ps, ss))


def _scope_match(e: dict, scope_q: str) -> bool:
    """NGSI-LD scopeQ subset: ',' = OR, ';' = AND, '+' = one level, '#' = this level and below."""
    if not scope_q:
        return True
    scopes = e.get("scope") or []
    scopes = [scopes] if isinstance(scopes, str) else scopes
    return any(all(any(_scope_one(s, c) for s in scopes) for c in alt.split(";")) for alt in scope_q.split(","))


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body=None):
        data = json.dumps(body).encode() if body is not None else b""
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        if u.path != "/ngsi-ld/v1/entityOperations/upsert":
            return self._send(404, {"title": "not found"})
        if "application/ld+json" not in (self.headers.get("Content-Type") or ""):
            return self._send(415, {"title": "ld+json required"})
        ents = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        for e in ents:
            if "@context" not in e:
                return self._send(400, {"title": f"{e.get('id')}: no @context"})
            STORE[e["id"]] = e
        return self._send(204)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        qs = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
        if u.path == "/ngsi-ld/v1/entities":
            rows = [_kv(e) for e in STORE.values()
                    if e["type"] == qs.get("type") and _match(e, qs.get("q", "")) and _scope_match(e, qs.get("scopeQ", ""))]
            return self._send(200, rows)
        if u.path.startswith("/ngsi-ld/v1/entities/"):
            eid = urllib.parse.unquote(u.path.rsplit("/", 1)[1])
            return self._send(200, _kv(STORE[eid])) if eid in STORE else self._send(404, {"title": "not found"})
        return self._send(404, {"title": "not found"})


def selftest() -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from pets_transformer import fiware
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        res = fiware.run(None, url, None, False, 0.85)
    finally:
        srv.shutdown()
    views = res.get("scope_views", {})
    ok = (res.get("publish", {}).get("status") == 204 and res.get("consumed", 0) >= 1
          and views.get("public recipient") == 1 and views.get("research recipient (controlled environment)") == 2)
    print("\nmock selftest:", "PASS" if ok else "FAIL", f"({len(STORE)} entities stored)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(selftest() if "--selftest" in sys.argv else 0)
