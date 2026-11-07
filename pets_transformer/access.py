"""Layer 3 - who is the recipient? Signed access credentials and a policy enforcement point (PEP) in front of the broker.

Layer 1 (PETs Transformer) protects the data itself. Layer 2 labels every NGSI-LD entity with a scope
(/health/public/dp, /health/research/pseudonymised, ...). Layer 3, this module, makes sure a recipient can only query
the scopes its *verified* trust level allows:

1. The data controller issues each recipient a data-access credential: W3C Verifiable-Credential-shaped claims
   (credentialSubject.trustLevel = the scenario trust level, e.g. "public" or "controlled_research"), signed as a
   compact JWS with ES256 (ECDSA P-256) and an expiry.
2. The recipient sends it as "Authorization: Bearer <credential>" to the PEP, the only network path to the broker.
3. The PEP verifies signature, issuer key and expiry (401 otherwise), maps the trust level to the scopes POLICY allows,
   and narrows the recipient's own scopeQ to them (403 when nothing is left). Entities read by id are checked against
   the same scopes; writes are refused (the controller writes inside its own network).

In a FIWARE deployment the same roles are played by Keyrock (identity, OAuth2) with the Wilma PEP proxy, or by the
data-space verifiable-credential stack (VCVerifier + a PEP + ODRL policies). This module is a small, tested stand-in
for that pattern, not a replacement for it: no revocation list, one issuer key, no consent or purpose checks.

    python tools/ngsi_ld_mock.py --selftest     # broker test double + this PEP, 9 access cases
"""
from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# trust level (from the scenario registry) -> scopes a recipient with that verified trust level may query
POLICY = {"public": ["/+/public/#"], "controlled_research": ["/+/research/#", "/+/public/#"]}
ISSUER = "did:example:data-controller"


class AccessDenied(Exception):
    def __init__(self, status: int, reason: str):
        super().__init__(f"{status} {reason}")
        self.status, self.reason = status, reason


# ----------------------------------------------------------------------------------------------- scope algebra
def _parse(p: str) -> tuple[list[str], bool]:
    segs = p.strip().strip("/").split("/") if p.strip().strip("/") else []
    hash_ = bool(segs) and segs[-1] == "#"
    return (segs[:-1] if hash_ else segs), hash_


def meet(a: str, b: str) -> str | None:
    """Intersection of two NGSI-LD scope patterns ('+' one level, trailing '#' this level and below), or None."""
    (sa, ha), (sb, hb) = _parse(a), _parse(b)
    out = []
    for x, y in zip(sa, sb):
        if x == "+":
            out.append(y)
        elif y == "+" or x == y:
            out.append(x)
        else:
            return None
    ra, rb = sa[len(out):], sb[len(out):]
    if not ra and not rb:
        tail = ha and hb
    elif ra:
        if not hb:
            return None
        out, tail = out + ra, ha
    else:
        if not ha:
            return None
        out, tail = out + rb, hb
    return "/" + "/".join(out + (["#"] if tail else []))


def effective_scope_q(claims: dict, requested: str | None = None) -> str:
    trust = (((claims.get("vc") or {}).get("credentialSubject")) or {}).get("trustLevel")
    allowed = POLICY.get(trust)
    if not allowed:
        raise AccessDenied(403, f"no access policy for trust level {trust!r}")
    if requested and ";" in requested:
        raise AccessDenied(400, "scopeQ with ';' is not supported by this PEP")
    asked = [r for r in (requested or "").split(",") if r.strip()] or allowed
    eff = list(dict.fromkeys(m for r in asked for a in allowed if (m := meet(r, a))))
    if not eff:
        raise AccessDenied(403, f"scopeQ {requested!r} is outside what trust level {trust!r} may see")
    return ",".join(eff)


def visible(entity: dict, scope_q: str) -> bool:
    scopes = entity.get("scope") or []
    scopes = [scopes] if isinstance(scopes, str) else scopes
    return any(meet(s, p) == "/" + s.strip("/") for s in scopes for p in scope_q.split(","))


# ----------------------------------------------------------------------------------------------- credentials
def _b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _ub64u(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def new_issuer_key():
    from cryptography.hazmat.primitives.asymmetric import ec
    return ec.generate_private_key(ec.SECP256R1())


def issue(key, subject: str, trust_level: str, ttl: int = 3600, now: float | None = None) -> str:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
    t = int(now if now is not None else time.time())
    claims = {"iss": ISSUER, "sub": subject, "iat": t, "exp": t + ttl,
              "vc": {"@context": ["https://www.w3.org/2018/credentials/v1"],
                     "type": ["VerifiableCredential", "DataAccessCredential"],
                     "credentialSubject": {"id": subject, "trustLevel": trust_level}}}
    head = _b64u(json.dumps({"alg": "ES256", "typ": "vc+jwt"}).encode())
    body = _b64u(json.dumps(claims).encode())
    r, s = decode_dss_signature(key.sign(f"{head}.{body}".encode(), ec.ECDSA(hashes.SHA256())))
    return f"{head}.{body}.{_b64u(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}"


def verify(token: str | None, public_key, now: float | None = None) -> dict:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
    parts = (token or "").split(".")
    if len(parts) != 3:
        raise AccessDenied(401, "no credential")
    head, body, sig = parts
    try:
        if json.loads(_ub64u(head)).get("alg") != "ES256":
            raise AccessDenied(401, "unsupported algorithm")
        raw = _ub64u(sig)
        if len(raw) != 64:
            raise AccessDenied(401, "malformed signature")
        public_key.verify(encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big")),
                          f"{head}.{body}".encode(), ec.ECDSA(hashes.SHA256()))
        claims = json.loads(_ub64u(body))
    except InvalidSignature:
        raise AccessDenied(401, "signature invalid (edited credential or unknown issuer)") from None
    except AccessDenied:
        raise
    except Exception:
        raise AccessDenied(401, "malformed credential") from None
    if claims.get("iss") != ISSUER:
        raise AccessDenied(401, "unknown issuer")
    if float(claims.get("exp", 0)) < (now if now is not None else time.time()):
        raise AccessDenied(401, "credential expired")
    return claims


# ----------------------------------------------------------------------------------------------- PEP proxy
def make_pep(backend: str, public_key):
    """HTTP handler class: a read-only PEP in front of the NGSI-LD broker at `backend`."""
    base = backend.rstrip("/")

    def forward(path: str, qs: dict, headers) -> tuple[int, object]:
        h = {k: headers[k] for k in ("Accept", "Link", "NGSILD-Tenant") if headers.get(k)}
        url = f"{base}{path}" + (f"?{urllib.parse.urlencode(qs)}" if qs else "")
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=30) as r:
                raw = r.read().decode("utf-8")
                return r.status, (json.loads(raw) if raw.strip() else None)
        except urllib.error.HTTPError as ex:
            return ex.code, {"title": ex.read().decode("utf-8", "replace")[:300]}

    class PEP(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body=None):
            data = json.dumps(body).encode() if body is not None else b""
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _refuse(self):
            self._send(403, {"title": "the PEP gives recipients read-only access; the controller writes inside its own network"})

        do_POST = do_PATCH = do_PUT = do_DELETE = _refuse

        def do_GET(self):
            u = urllib.parse.urlparse(self.path)
            qs = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
            auth = self.headers.get("Authorization") or ""
            try:
                claims = verify(auth[7:] if auth.startswith("Bearer ") else None, public_key)
                eff = effective_scope_q(claims, qs.get("scopeQ"))
            except AccessDenied as ex:
                return self._send(ex.status, {"title": ex.reason})
            if u.path == "/ngsi-ld/v1/entities":
                st, body = forward(u.path, {**qs, "scopeQ": eff}, self.headers)
                return self._send(st, body)
            if u.path.startswith("/ngsi-ld/v1/entities/"):
                st, body = forward(u.path, qs, self.headers)
                if st == 200 and not (isinstance(body, dict) and visible(body, eff)):
                    return self._send(403, {"title": "entity is outside the credential's scope"})
                return self._send(st, body)
            return self._send(404, {"title": "not found"})

    return PEP


def serve_pep(backend: str, public_key, host: str = "127.0.0.1", port: int = 0) -> ThreadingHTTPServer:
    import threading
    srv = ThreadingHTTPServer((host, port), make_pep(backend, public_key))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv
