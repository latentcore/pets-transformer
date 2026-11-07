"""Test double for an NGSI-LD broker - NOT a FIWARE component.

Implements only what `run.py fiware` calls (batch upsert, GET entities by type + simple q + scopeQ, GET entity by id,
keyValues rendering) so the publish/consume code path can be smoke-tested offline. The real run targets a FIWARE context broker.

    python tools/ngsi_ld_mock.py --selftest     # starts the mock on a free port, publishes, consumes, then runs the
                                                # layer-3 access cases through the PEP (pets_transformer/access.py), stops
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


def access_cases(fiware, broker_url: str) -> bool:
    """Layer 3 through the PEP: each case is (name, credential, query params, expected HTTP status, expected count)."""
    import json as _j
    import urllib.request as _u

    from pets_transformer import access
    key, stranger = access.new_issuer_key(), access.new_issuer_key()
    pep = access.serve_pep(broker_url, key.public_key())
    purl = f"http://127.0.0.1:{pep.server_address[1]}"
    pub = access.issue(key, "did:example:dashboard-viewer", "public")
    res = access.issue(key, "did:example:research-team", "controlled_research")
    h, _, g = pub.split(".")
    body = _j.loads(access._ub64u(pub.split(".")[1]))
    body["vc"]["credentialSubject"]["trustLevel"] = "controlled_research"
    edited = f"{h}.{access._b64u(_j.dumps(body).encode())}.{g}"
    pa = {"type": "PrivacyAssessment"}
    di = {"type": "FieldPolicy", "scopeQ": "/health/+/+/direct_identifier"}
    research_ds = "urn:ngsi-ld:Dataset:pets-transformer:health-HEALTH-S1-week"
    cases = [("no credential", None, pa, 401, None),
             ("public credential", pub, pa, 200, 1),
             ("research credential", res, pa, 200, 2),
             ("public credential asks for research data", pub, {**pa, "scopeQ": "/health/research/#"}, 403, None),
             ("public credential edited to say research", edited, pa, 401, None),
             ("expired research credential", access.issue(key, "r", "controlled_research", ttl=-60), pa, 401, None),
             ("research credential from an unknown issuer", access.issue(stranger, "r", "controlled_research"), pa, 401, None),
             ("research credential, every direct identifier", res, di, 200, 5),
             ("public credential, every direct identifier", pub, di, 200, 0)]
    ok = True
    print("\nlayer 3: signed credential + PEP in front of the broker")
    try:
        for name, tok, params, want_st, want_n in cases:
            st, rows = fiware.query(purl, params, None, tok)
            n = len(rows) if isinstance(rows, list) else None
            good = st == want_st and (want_n is None or n == want_n)
            ok &= good
            print(f"  {'PASS' if good else 'FAIL'}  {name}: HTTP {st}" + (f", {n} entities" if n is not None else f" ({rows})"))
        st, _ = fiware._req("GET", f"{purl}/ngsi-ld/v1/entities/{research_ds}?options=keyValues", headers=fiware._headers(None, token=pub))
        ok &= st == 403
        print(f"  {'PASS' if st == 403 else 'FAIL'}  public credential reads the research Dataset by id: HTTP {st}")
        rows = fiware.consume(purl, 0.0, None, token=res)
        good = len(rows) == 2 and all(isinstance(r["field_policies"], int) for r in rows)
        ok &= good
        print(f"  {'PASS' if good else 'FAIL'}  research recipient end to end through the PEP: {len(rows)} releases")
        rq = _u.Request(f"{purl}/ngsi-ld/v1/entityOperations/upsert", data=b"[]", method="POST",
                        headers={"Content-Type": "application/ld+json", "Authorization": f"Bearer {res}"})
        try:
            _u.urlopen(rq, timeout=10)
            st = 200
        except Exception as ex:  # HTTPError
            st = getattr(ex, "code", 0)
        ok &= st == 403
        print(f"  {'PASS' if st == 403 else 'FAIL'}  recipient tries to write through the PEP: HTTP {st}")
    finally:
        pep.shutdown()
    return ok


def selftest() -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from pets_transformer import fiware
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        res = fiware.run(None, url, None, False, 0.85)
        access_ok = access_cases(fiware, url)
    finally:
        srv.shutdown()
    views = res.get("scope_views", {})
    ok = (res.get("publish", {}).get("status") == 204 and res.get("consumed", 0) >= 1
          and views.get("public recipient") == 1 and views.get("research recipient (controlled environment)") == 2
          and access_ok)
    print("\nmock selftest:", "PASS" if ok else "FAIL", f"({len(STORE)} entities stored)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(selftest() if "--selftest" in sys.argv else 0)
