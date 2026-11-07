"""FIWARE / NGSI-LD publishing layer: a context broker as the catalogue of privacy-assessed releases.

A data controller protects a dataset with PETs Transformer and publishes, to an NGSI-LD context broker, *what* it
released and *how safe and useful* that release is - never the rows themselves. A recipient (or a data-space
connector) queries the broker before asking for access: "which releases for my trust level keep composite >= 0.85,
what was sacrificed, which PET was applied to each field and why?".

Entities (ETSI NGSI-LD, normalised form, written with one batch upsert):

    Dataset              Smart Data Model dataModel.DCAT-AP/Dataset      one per release (title, keywords, legislation,
                                                                        temporal resolution, provenance, distributions)
    Distribution         Smart Data Model dataModel.DCAT-AP/Distribution one per released file   -> belongsToDataset
    DataSharingScenario  recipient, purpose, trust level, threat model  (from the scenario registry)
    PrivacyAssessment    pets-eval scores, linkage attacks before/after, k-anonymity, utility tasks, sacrificed tasks,
                         DP budget                                       -> refDataset, refScenario
    FieldPolicy          one per source column: role, subtype, PET action, reason      -> refDataset

The two DCAT-AP entity types are validated against the official Smart Data Models JSON schemas (keyValues form).
The other three types use the NGSI-LD default context (no private vocabulary to host).

Data classification travels on the data itself as NGSI-LD `scope` (a hierarchical label, ETSI GS CIM 009), so a
recipient filters with `scopeQ` and only sees what its trust level allows (suggested by Chandra Challagonda,
FIWARE Foundation, at the hackathon):

    /<dataset>/<audience>                              DataSharingScenario   e.g. /health/research, /health/public
    /<dataset>/<audience>/<protection>                 Dataset, Distribution, PrivacyAssessment
                                                       e.g. /health/research/pseudonymised, /health/public/dp
    /<dataset>/<audience>/<protection>/<privacy role>  FieldPolicy  e.g. /health/research/pseudonymised/direct_identifier

    scopeQ=/health/public/#                     public recipient: only the differentially private release
    scopeQ=/health/research/#,/health/public/#  research recipient in a controlled environment: both
    scopeQ=/health/+/+/direct_identifier        every direct identifier and how it was handled, any release

    python run.py fiware                                   # dry run: build, validate, write outputs/fiware/
    python run.py fiware --broker https://<broker>         # publish (batch upsert) and read back as a recipient
    python run.py fiware --broker https://<broker> --consume --min-composite 0.85
"""
from __future__ import annotations

import datetime as dt
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .config import outputs_dir

SDM_CONTEXT = "https://smart-data-models.github.io/dataModel.DCAT-AP/context.jsonld"
CORE_CONTEXT = "https://uri.etsi.org/ngsi-ld/v1/ngsi-ld-core-context.jsonld"
CONTEXT = [SDM_CONTEXT, CORE_CONTEXT]
SCHEMAS = {
    "Dataset": "https://raw.githubusercontent.com/smart-data-models/dataModel.DCAT-AP/master/Dataset/schema.json",
    "Distribution": "https://raw.githubusercontent.com/smart-data-models/dataModel.DCAT-AP/master/Distribution/schema.json",
}
RELATIONSHIPS = {"refDataset", "refScenario", "belongsToDataset"}
DEFAULT_RELEASES = ["health:HEALTH-S1:week", "health:HEALTH-S2:dp_eps3"]
ID_PREFIX = "pets-transformer"
TIME_RES = {"hour": "PT1H", "day": "P1D", "week": "P7D", "month": "P1M"}
AUDIENCE = {"controlled_research": "research", "public": "public"}  # scenario trust level -> scope segment
SCOPE_VIEWS = [["public recipient", "/health/public/#"],
               ["research recipient (controlled environment)", "/health/research/#,/health/public/#"]]


def _seg(x) -> str:
    return "".join(c if c.isalnum() or c in "_-" else "_" for c in str(x or "")) or "unclassified"


# ----------------------------------------------------------------------------------------------- building entities
def _rid(dataset: str, scenario: str, variant: str) -> str:
    return f"{dataset}-{scenario}-{variant}"


def _iso(path: Path) -> str:
    t = dt.datetime.fromtimestamp(path.stat().st_mtime, tz=dt.timezone.utc).replace(microsecond=0)
    return t.isoformat().replace("+00:00", "Z")


def _scenario(scenario_id: str) -> dict:
    try:
        from pets_eval import io

        from .config import package_root
        return io.get_scenario(scenario_id, package_root()) or {"id": scenario_id}
    except Exception:  # package not present (fresh clone without the team-only material)
        return {"id": scenario_id}


def _linkage(scorecard: dict) -> list[dict]:
    out = []
    for table, t in (scorecard.get("tables") or {}).items():
        for adversary, r in (t.get("linkage") or {}).items():
            if isinstance(r, dict) and "reidentification_rate" in r:
                out.append({"table": table, "adversary": adversary,
                            "reidentificationRateOriginal": r.get("baseline_reidentification_rate_original"),
                            "reidentificationRateReleased": r.get("reidentification_rate"),
                            "targetsInData": r.get("n_targets_in_data"),
                            "medianCandidateSet": r.get("median_candidate_set")})
    return out


def _kanon(scorecard: dict) -> list[dict]:
    out = []
    for table, t in (scorecard.get("tables") or {}).items():
        k, k0 = t.get("k_anonymity"), t.get("k_anonymity_original")
        if isinstance(k, dict) and k.get("qis"):
            out.append({"table": table, "quasiIdentifiers": k.get("qis"), "minK": k.get("min_k"), "medianK": k.get("median_k"),
                        "uniqueShareReleased": k.get("sample_uniqueness"),
                        "uniqueShareOriginal": (k0 or {}).get("sample_uniqueness")})
    return out


def release_entities(dataset: str, scenario_id: str, variant: str) -> list[dict]:
    """keyValues entities for one release directory outputs/<dataset>/<scenario>/<variant>/."""
    rdir = outputs_dir() / dataset / scenario_id / variant
    summ_p, sc_p = rdir / "summary.json", rdir / "scorecard.json"
    if not summ_p.exists() or not sc_p.exists():
        raise FileNotFoundError(f"{rdir}: run protect/dp first (summary.json + scorecard.json needed)")
    summary = json.loads(summ_p.read_text(encoding="utf-8"))
    scorecard = json.loads(sc_p.read_text(encoding="utf-8"))
    decisions = json.loads((rdir / "decisions.json").read_text(encoding="utf-8")) if (rdir / "decisions.json").exists() else {}
    scen = _scenario(scenario_id)
    rid = _rid(dataset, scenario_id, variant)
    ds_id = f"urn:ngsi-ld:Dataset:{ID_PREFIX}:{rid}"
    sc_id = f"urn:ngsi-ld:DataSharingScenario:{ID_PREFIX}:{scenario_id}"
    pa_id = f"urn:ngsi-ld:PrivacyAssessment:{ID_PREFIX}:{rid}"
    issued = _iso(summ_p)
    dp = summary.get("dp")
    policy = summary.get("policy") or decisions.get("policy") or {}
    utility = summary.get("utility") or {}
    s = summary.get("summary") or {}

    files = sorted(p for p in rdir.glob("*.csv") if not p.name.startswith("rowmap_"))  # row maps never leave
    dist_ids = [f"urn:ngsi-ld:Distribution:{ID_PREFIX}:{rid}:{p.stem}" for p in files]
    if dp:
        method = f"{dp.get('mechanism')}, total epsilon {dp.get('epsilon_total')}, contribution bound {dp.get('contribution_bound_visits')}"
        keywords = ["differential privacy", "aggregate release", "synthetic data", dataset]
        tres = ["P7D"]
    else:
        method = (f"keyed pseudonyms (HMAC-SHA256), age bands of {policy.get('age_band')} years, "
                  f"timestamps rounded to {policy.get('time_res')}, local suppression to k >= {policy.get('k')}, free text {policy.get('text')}")
        keywords = ["pseudonymisation", "k-anonymity", "generalisation", "synthetic data", dataset]
        tres = [TIME_RES[policy["time_res"]]] if policy.get("time_res") in TIME_RES else []
    trust = scen.get("trust_level", "")
    audience = f"/{_seg(dataset)}/{_seg(AUDIENCE.get(trust, trust))}"
    scope = f"{audience}/{'dp' if dp else 'pseudonymised'}"

    entities: list[dict] = [{
        "id": sc_id, "type": "DataSharingScenario", "scope": audience,
        "scenarioId": scenario_id, "name": scen.get("title", scenario_id),
        "recipient": scen.get("recipient", ""), "purpose": scen.get("purpose", ""),
        "trustLevel": scen.get("trust_level", ""), "threatModel": scen.get("threat_model", ""),
        "legalContext": scen.get("legal_context", ""), "utilityRequirements": scen.get("utility_requirements", []),
    }]
    ds = {
        "id": ds_id, "type": "Dataset", "scope": scope,
        "title": f"{dataset} - {scen.get('title', scenario_id)} - protected release '{variant}'",
        "description": (f"Protected release of the synthetic '{dataset}' dataset for scenario {scenario_id} "
                        f"({scen.get('recipient', 'recipient not stated')}). Method: {method}. "
                        f"pets-eval self-assessment: privacy {s.get('privacy')}, utility {s.get('utility')}, composite {s.get('composite')}."),
        "keyword": keywords, "theme": [dataset],
        "creator": ["PETs Transformer, team Cowork (Since AI Hackathon 2026)"], "publisher": "team Cowork",
        "issued": issued, "version": variant, "temporalResolution": tres,
        "accessRights": scen.get("trust_level", "") or "not stated",
        "applicableLegislation": [scen["legal_context"]] if scen.get("legal_context") else [],
        "provenance": [f"Source: synthetic PETs Proving Ground '{dataset}' package (Since AI ry). Transformed by PETs Transformer; "
                       "row maps are kept for scoring only and are never published."],
        "wasGeneratedBy": [f"PETs Transformer release '{variant}' for {scenario_id}"],
        "distribution": dist_ids,
    }
    entities.append(ds)
    for p, did in zip(files, dist_ids):
        entities.append({"id": did, "type": "Distribution", "scope": scope, "title": [p.name],
                         "description": [f"{p.stem} of release '{variant}' ({scenario_id}), delivered inside the recipient's "
                                         "controlled environment, not published"],
                         "accessURL": [f"pets_submission/{dataset}/{scenario_id}/{p.name}"],
                         "format": "CSV", "mediaType": "text/csv", "byteSize": p.stat().st_size,
                         "belongsToDataset": ds_id})
    sacrificed = sorted(k for k, v in utility.items() if v is None or (isinstance(v, (int, float)) and v < 0.5))
    pa = {
        "id": pa_id, "type": "PrivacyAssessment", "scope": scope,
        "privacyScore": s.get("privacy"), "utilityScore": s.get("utility"), "compositeScore": s.get("composite"),
        "scoreLabel": s.get("label", "self-assessment"), "scoringMethod": s.get("method", ""), "tool": "pets-eval 1.0.0",
        "riskComponents": summary.get("risks") or scorecard.get("risk_components") or {},
        "linkageAttacks": _linkage(scorecard), "kAnonymity": _kanon(scorecard),
        "utilityTasks": {k: (round(v, 4) if isinstance(v, (int, float)) else "not answerable") for k, v in utility.items()},
        "sacrificedTasks": sacrificed, "dateAssessed": _iso(sc_p),
        "refDataset": ds_id, "refScenario": sc_id,
    }
    if dp:
        pa["differentialPrivacy"] = {"mechanism": dp.get("mechanism"), "epsilonTotal": dp.get("epsilon_total"),
                                     "delta": dp.get("delta"), "unitOfPrivacy": dp.get("unit_of_privacy"),
                                     "contributionBound": dp.get("contribution_bound_visits")}
    entities.append(pa)
    for d in decisions.get("decisions", []):
        col = f"{d.get('table')}.{d.get('column')}"
        entities.append({"id": f"urn:ngsi-ld:FieldPolicy:{ID_PREFIX}:{rid}:{col}", "type": "FieldPolicy",
                         "scope": f"{scope}/{_seg(d.get('role'))}",
                         "table": d.get("table"), "column": d.get("column"), "privacyRole": d.get("role"),
                         "subtype": d.get("subtype") or "", "action": d.get("action"),
                         "outputColumn": d.get("output_column") or "", "reason": d.get("reason") or "",
                         "refDataset": ds_id})
    return entities


def build(releases: list[str] | None = None) -> list[dict]:
    seen, out = set(), []
    for r in releases or DEFAULT_RELEASES:
        dataset, scenario_id, variant = r.split(":")
        for e in release_entities(dataset, scenario_id, variant):
            if e["id"] not in seen:  # the scenario entity is shared between releases
                seen.add(e["id"])
                out.append(e)
    return out


def normalise(e: dict, context=None) -> dict:
    """keyValues -> NGSI-LD normalised form with @context (context=False: leave it out). `scope` is a core member of
    the entity (not a Property) and is kept as is."""
    out = {"id": e["id"], "type": e["type"]}
    for k, v in e.items():
        if k in ("id", "type"):
            continue
        if k == "scope":
            out[k] = v
            continue
        out[k] = {"type": "Relationship", "object": v} if k in RELATIONSHIPS else {"type": "Property", "value": v}
    if context is not False:
        out["@context"] = CONTEXT if context is None else context
    return out


CORE_CONTEXT_V19 = "https://uri.etsi.org/ngsi-ld/v1/ngsi-ld-core-context-v1.9.jsonld"


def inline_sdm_context(entities: list[dict]) -> dict:
    """The official DCAT-AP Smart Data Models context, cut to the terms our Dataset/Distribution entities use
    (same IRIs). For brokers that cannot fetch remote @context documents, e.g. the in-browser Antares build."""
    full = _fetch_json(SDM_CONTEXT, outputs_dir() / "fiware" / "schema_cache")["@context"]
    used = {"Dataset", "Distribution"}
    for e in entities:
        if e["type"] in ("Dataset", "Distribution"):
            used |= set(e) - {"id", "type", "@context", "scope"}
    return {k: full[k] for k in sorted(used) if k in full}


# ----------------------------------------------------------------------------------------------- validation
def _fetch_json(url: str, cache: Path) -> dict:
    cache.mkdir(parents=True, exist_ok=True)
    f = cache / (urllib.parse.quote(url, safe="") + ".json")
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    with urllib.request.urlopen(url, timeout=30) as r:
        data = json.loads(r.read().decode("utf-8"))
    f.write_text(json.dumps(data), encoding="utf-8")
    return data


def validate(entities: list[dict]) -> dict:
    """Validate Dataset/Distribution (keyValues) against the official Smart Data Models schemas; check every entity
    is well-formed NGSI-LD (URN id, type, attributes). Returns a report dict."""
    import jsonschema
    from referencing import Registry, Resource
    from referencing.jsonschema import DRAFT202012

    cache = outputs_dir() / "fiware" / "schema_cache"
    registry = Registry(retrieve=lambda uri: Resource.from_contents(_fetch_json(uri, cache), default_specification=DRAFT202012))
    validators = {t: jsonschema.Draft202012Validator(_fetch_json(u, cache), registry=registry,
                                                       format_checker=jsonschema.FormatChecker())
                  for t, u in SCHEMAS.items()}
    report = {"schemas": SCHEMAS, "checked": {}, "errors": []}
    for e in entities:
        if not str(e["id"]).startswith("urn:ngsi-ld:"):
            report["errors"].append(f"{e['id']}: id is not an NGSI-LD URN")
        v = validators.get(e["type"])
        report["checked"][e["type"]] = report["checked"].get(e["type"], 0) + 1
        if v is None:
            continue
        for err in v.iter_errors({k: x for k, x in e.items() if k not in ("@context", "scope")}):  # scope: NGSI-LD core
            report["errors"].append(f"{e['id']}: {'/'.join(map(str, err.absolute_path)) or '(root)'}: {err.message[:200]}")
    report["valid"] = not report["errors"]
    return report


# ----------------------------------------------------------------------------------------------- broker I/O
def _req(method: str, url: str, body=None, headers: dict | None = None, timeout: int = 30):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    rq = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(rq, timeout=timeout) as r:
            raw = r.read().decode("utf-8")
            return r.status, (json.loads(raw) if raw.strip() else None)
    except urllib.error.HTTPError as ex:
        raw = ex.read().decode("utf-8", "replace")
        return ex.code, raw[:500]


def _headers(tenant: str | None, ld_body: bool = False) -> dict:
    h = {"Accept": "application/json",
         "Link": f'<{SDM_CONTEXT}>; rel="http://www.w3.org/ns/json-ld#context"; type="application/ld+json"'}
    if ld_body:
        h = {"Content-Type": "application/ld+json", "Accept": "application/json"}
    if tenant:
        h["NGSILD-Tenant"] = tenant
    return h


def publish(entities: list[dict], broker: str, tenant: str | None = None) -> dict:
    base = broker.rstrip("/")
    norm = [normalise(e) for e in entities]
    status, body = _req("POST", f"{base}/ngsi-ld/v1/entityOperations/upsert?options=update", norm, _headers(tenant, ld_body=True))
    return {"status": status, "response": body, "entities": len(norm)}


def query(broker: str, params: dict, tenant: str | None = None) -> tuple[int, list | str]:
    base = broker.rstrip("/")
    qs = urllib.parse.urlencode({**params, "options": "keyValues"}, safe="=<>!\"")
    return _req("GET", f"{base}/ngsi-ld/v1/entities?{qs}", headers=_headers(tenant))


def consume(broker: str, min_composite: float = 0.0, tenant: str | None = None, scope_q: str | None = None) -> list[dict]:
    """The recipient's side: find assessed releases above a composite score (with scope_q: only inside the
    classification the recipient is cleared for), then pull their catalogue entries."""
    params = {"type": "PrivacyAssessment", "q": f"compositeScore>={min_composite}", "limit": 100}
    if scope_q:
        params["scopeQ"] = scope_q
    status, pas = query(broker, params, tenant)
    if status != 200 or not isinstance(pas, list):
        raise RuntimeError(f"query PrivacyAssessment failed: HTTP {status}: {pas}")
    rows = []
    for pa in pas:
        ds_id = pa.get("refDataset")
        ds_id = ds_id.get("object") if isinstance(ds_id, dict) else ds_id
        st, ds = _req("GET", f"{broker.rstrip('/')}/ngsi-ld/v1/entities/{urllib.parse.quote(ds_id, safe='')}?options=keyValues",
                      headers=_headers(tenant))
        st2, fps = query(broker, {"type": "FieldPolicy", "q": f'refDataset=="{ds_id}"', "limit": 200}, tenant)
        attacks = pa.get("linkageAttacks") or []
        rows.append({"dataset": (ds or {}).get("title") if isinstance(ds, dict) else ds_id, "scope": pa.get("scope"),
                     "composite": pa.get("compositeScore"), "privacy": pa.get("privacyScore"), "utility": pa.get("utilityScore"),
                     "linkage_before": max([a.get("reidentificationRateOriginal") or 0 for a in attacks], default=None),
                     "linkage_after": max([a.get("reidentificationRateReleased") or 0 for a in attacks], default=None),
                     "sacrificed": pa.get("sacrificedTasks"),
                     "field_policies": len(fps) if isinstance(fps, list) else f"HTTP {st2}",
                     "distributions": len((ds or {}).get("distribution") or []) if isinstance(ds, dict) else f"HTTP {st}"})
    return rows


# ----------------------------------------------------------------------------------------------- Antares playground
ANTARES_JS = """// PETs Transformer -> Antares NGSI-LD broker (browser playground, https://antaresbroker.joinedcontext.com/demo/)
// Paste into the DevTools console of that page. The broker runs inside the page (WebAssembly); nothing leaves the browser.
// The in-browser broker cannot fetch remote @context documents, so the DCAT-AP Smart Data Models terms we use are inlined
// (same IRIs as the official context) next to the NGSI-LD core context v1.9.
(async () => {
  const T = __TENANT__, MIN = __MIN__;
  const CTX = __CTX__;
  const ENTITIES = __ENTITIES__.map(e => ({...e, "@context": CTX}));
  const W = {"Content-Type": "application/ld+json", "NGSILD-Tenant": T};
  const R = {"Accept": "application/json", "NGSILD-Tenant": T};
  const pick = (o, k, iri) => (o && (o[k] ?? o[iri]));
  const up = await fetch("/ngsi-ld/v1/entityOperations/upsert?options=update", {method: "POST", headers: W, body: JSON.stringify(ENTITIES)});
  console.log(`controller: batch upsert of ${ENTITIES.length} entities into tenant "${T}" -> HTTP ${up.status}`, up.status >= 300 ? await up.text() : "");
  const enc = encodeURIComponent;
  const pas = await (await fetch(`/ngsi-ld/v1/entities?type=PrivacyAssessment&q=${enc("compositeScore>=" + MIN)}&options=keyValues&limit=100`, {headers: R})).json();
  const rows = [];
  for (const pa of pas) {
    const dsId = typeof pa.refDataset === "object" ? pa.refDataset.object : pa.refDataset;
    const ds = await (await fetch(`/ngsi-ld/v1/entities/${enc(dsId)}?options=keyValues`, {headers: R})).json();
    const fps = await (await fetch(`/ngsi-ld/v1/entities?type=FieldPolicy&q=${enc('refDataset=="' + dsId + '"')}&options=keyValues&limit=100`, {headers: R})).json();
    const atk = pa.linkageAttacks || [];
    rows.push({dataset: pick(ds, "title", "http://purl.org/dc/terms/title"), composite: pa.compositeScore, privacy: pa.privacyScore, utility: pa.utilityScore,
               worstAttackBefore: Math.max(0, ...atk.map(a => a.reidentificationRateOriginal || 0)),
               worstAttackAfter: Math.max(0, ...atk.map(a => a.reidentificationRateReleased || 0)),
               sacrificed: (pa.sacrificedTasks || []).join(", "), fieldPolicies: Array.isArray(fps) ? fps.length : fps,
               distributions: (pick(ds, "distribution", "https://smartdatamodels.org/dataModel.DCAT-AP/distribution") || []).length});
  }
  console.log(`recipient: PrivacyAssessment with compositeScore >= ${MIN} -> ${rows.length} release(s)`);
  console.table(rows);
  if (rows.length && Array.isArray(rows)) {
    const ex = await (await fetch(`/ngsi-ld/v1/entities?type=FieldPolicy&q=${enc('column=="date_of_birth"')}&options=keyValues`, {headers: R})).json();
    console.log("why was date_of_birth protected?", ex.map(f => `${f.table}.${f.column}: ${f.action} - ${f.reason}`));
  }
  // Data classification on the data itself: every entity carries an NGSI-LD scope; each recipient filters with scopeQ.
  const views = {};
  for (const [who, sq] of __VIEWS__) {
    const r = await fetch(`/ngsi-ld/v1/entities?type=PrivacyAssessment&scopeQ=${enc(sq)}&options=keyValues&limit=100`, {headers: R});
    const got = r.ok ? await r.json() : [];
    const t = [];
    for (const pa of got) {
      const dsId = typeof pa.refDataset === "object" ? pa.refDataset.object : pa.refDataset;
      const ds = await (await fetch(`/ngsi-ld/v1/entities/${enc(dsId)}?options=keyValues`, {headers: R})).json();
      t.push({scope: pa.scope, release: pick(ds, "title", "http://purl.org/dc/terms/title"), composite: pa.compositeScore,
              privacy: pa.privacyScore, utility: pa.utilityScore});
    }
    views[who] = t;
    console.log(`${who}: scopeQ=${sq} -> HTTP ${r.status}, ${t.length} release(s)`, r.ok ? "" : await r.text());
    console.table(t);
  }
  const dq = "/health/+/+/direct_identifier";
  const di = await (await fetch(`/ngsi-ld/v1/entities?type=FieldPolicy&scopeQ=${enc(dq)}&options=keyValues&limit=100`, {headers: R})).json();
  console.log(`every direct identifier in any release: scopeQ=${dq} -> ${Array.isArray(di) ? di.length : JSON.stringify(di)} field(s)`);
  console.table((Array.isArray(di) ? di : []).map(f => ({field: `${f.table}.${f.column}`, scope: f.scope, action: f.action})));
  globalThis.__petsTransformerResult = {rows, views, directIdentifiers: di};
  return rows;
})();
"""


def antares_snippet(entities: list[dict], tenant: str = "pets-transformer", min_composite: float = 0.85) -> str:
    norm = [normalise(e, context=False) for e in entities]
    ctx = [inline_sdm_context(entities), CORE_CONTEXT_V19]
    return (ANTARES_JS.replace("__TENANT__", json.dumps(tenant)).replace("__MIN__", json.dumps(min_composite))
            .replace("__CTX__", json.dumps(ctx, separators=(",", ":"))).replace("__VIEWS__", json.dumps(SCOPE_VIEWS))
            .replace("__ENTITIES__", json.dumps(norm, ensure_ascii=False, separators=(",", ":"))))


# ----------------------------------------------------------------------------------------------- command
def run(releases: list[str] | None, broker: str | None, tenant: str | None, consume_only: bool, min_composite: float) -> dict:
    out = outputs_dir() / "fiware"
    out.mkdir(parents=True, exist_ok=True)
    broker = broker or os.environ.get("NGSI_LD_BROKER")
    result: dict = {}
    if not consume_only:
        ents = build(releases)
        (out / "entities_keyvalues.json").write_text(json.dumps(ents, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n")
        (out / "entities_normalized.jsonld").write_text(json.dumps([normalise(e) for e in ents], indent=2, ensure_ascii=False),
                                                       encoding="utf-8", newline="\n")
        rep = validate(ents)
        (out / "validation.json").write_text(json.dumps(rep, indent=2), encoding="utf-8", newline="\n")
        counts = {}
        for e in ents:
            counts[e["type"]] = counts.get(e["type"], 0) + 1
        print(f"built {len(ents)} NGSI-LD entities: {counts}")
        print(f"Smart Data Models validation (DCAT-AP Dataset/Distribution): {'PASS' if rep['valid'] else 'FAIL'}")
        for err in rep["errors"][:10]:
            print("  ", err)
        result.update(entities=len(ents), types=counts, valid=rep["valid"])
        js = out / "antares_console.js"
        js.write_text(antares_snippet(ents, tenant or "pets-transformer", min_composite or 0.85), encoding="utf-8", newline="\n")
        print(f"Antares playground snippet: {js} (paste into the console of https://antaresbroker.joinedcontext.com/demo/)")
        if broker:
            pub = publish(ents, broker, tenant)
            print(f"upsert to {broker}: HTTP {pub['status']} ({pub['entities']} entities)")
            if pub["status"] not in (200, 201, 204, 207):
                print("   response:", pub["response"])
            result["publish"] = {"status": pub["status"]}
        else:
            print("dry run: no broker given (--broker or NGSI_LD_BROKER); payloads in outputs/fiware/")
    if broker:
        rows = consume(broker, min_composite, tenant)
        print(f"\nrecipient query: PrivacyAssessment with compositeScore >= {min_composite} -> {len(rows)} release(s)")
        for r in rows:
            print(f"  - {r['dataset']}\n    composite {r['composite']} (privacy {r['privacy']}, utility {r['utility']}); "
                  f"worst linkage attack {r['linkage_before']} -> {r['linkage_after']}; sacrificed {r['sacrificed']}; "
                  f"{r['field_policies']} field policies; {r['distributions']} distributions")
        views = {}
        for who, sq in SCOPE_VIEWS:
            v = consume(broker, 0.0, tenant, scope_q=sq)
            views[who] = {"scopeQ": sq, "releases": [{"scope": r["scope"], "dataset": r["dataset"], "composite": r["composite"]} for r in v]}
            print(f"\n{who}: scopeQ={sq} -> {len(v)} release(s)")
            for r in v:
                print(f"  - [{r['scope']}] {r['dataset']} (composite {r['composite']})")
        (out / "consume.json").write_text(json.dumps({"min_composite": rows, "scope_views": views}, indent=2, ensure_ascii=False),
                                          encoding="utf-8", newline="\n")
        result["consumed"] = len(rows)
        result["scope_views"] = {w: len(v["releases"]) for w, v in views.items()}
    return result
