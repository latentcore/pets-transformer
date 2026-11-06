"""One self-contained HTML report per protected release: the five things the TrustED demo must show.

1. Data types identified (detector output with evidence)      2. PETs selected per field (decision log)
3. The protected release (tables, rows, columns)              4. Utility before/after + privacy risk (scorecard, frontier)
5. The sharing scenario it fits, with residual risks stated honestly.
"""
from __future__ import annotations

import base64
import html
import json
from pathlib import Path

import pandas as pd

from pets_eval import io

from .config import outputs_dir, package_root

CSS = """
body{font-family:Segoe UI,system-ui,sans-serif;max-width:1100px;margin:24px auto;padding:0 18px;color:#1d2229;line-height:1.45}
h1{font-size:26px;margin:0 0 4px}h2{font-size:19px;margin:30px 0 10px;border-bottom:2px solid #e6e9ee;padding-bottom:4px}
.sub{color:#5b6572;margin:0 0 18px}.tiles{display:flex;gap:12px;margin:12px 0}.tile{flex:1;border:1px solid #dfe3e8;border-radius:10px;padding:12px;text-align:center;background:#fafbfc}
.v{font-size:30px;font-weight:650}.l{font-size:12px;color:#5b6572}.g{color:#1b7f3b}.y{color:#b26a00}.r{color:#c62828}
table{border-collapse:collapse;width:100%;font-size:13px;margin:8px 0}th,td{border-bottom:1px solid #eceff3;padding:5px 7px;text-align:left;vertical-align:top}
th{background:#f3f5f8;font-weight:600}code{background:#f3f5f8;padding:1px 5px;border-radius:4px;font-size:12px}
.badge{display:inline-block;padding:1px 7px;border-radius:10px;font-size:11px;font-weight:600;color:#fff}
.b-direct_identifier{background:#c62828}.b-quasi_identifier{background:#ef6c00}.b-sensitive_attribute{background:#6a1b9a}
.b-technical_identifier{background:#1565c0}.b-free_text{background:#00838f}.b-record_key{background:#546e7a}.b-non_sensitive{background:#2e7d32}
.note{background:#fff8e1;border-left:4px solid #f9a825;padding:8px 12px;border-radius:6px;font-size:13px;margin:10px 0}
.bar{height:12px;background:#e6e9ee;border-radius:6px;overflow:hidden;display:inline-block;width:220px;vertical-align:middle}
.bar i{display:block;height:100%}.small{font-size:12px;color:#5b6572}img{max-width:100%;border:1px solid #e6e9ee;border-radius:8px}
.cols{display:grid;grid-template-columns:1fr 1fr;gap:18px}ul{margin:6px 0 6px 18px}
"""


def _tile(label: str, v, good=.7, mid=.4):
    if v is None:
        return f'<div class="tile"><div class="v">–</div><div class="l">{label}</div></div>'
    cls = "g" if v >= good else ("y" if v >= mid else "r")
    return f'<div class="tile"><div class="v {cls}">{v:.0%}</div><div class="l">{label}</div></div>'


def _bar(v: float, color: str) -> str:
    return f'<span class="bar"><i style="width:{max(0, min(1, v)) * 100:.0f}%;background:{color}"></i></span> {v:.1%}'


def build(dataset: str, scenario: str, variant: str, labels_path: str | None = None, title: str | None = None) -> Path:
    root = package_root()
    run = outputs_dir() / dataset / scenario / variant
    dp_meta = json.loads((run / "dp_meta.json").read_text(encoding="utf-8")) if (run / "dp_meta.json").exists() else None
    if (run / "decisions.json").exists():
        dec = json.loads((run / "decisions.json").read_text(encoding="utf-8"))
    else:  # differentially private release: the "decisions" are the mechanism
        dec = {"policy": {"time_res": "week (+ sampled hour)", "age_band": 10, "age_top": 80, "code_chars": "diagnosis group",
                          "rare_min": "–", "k": "– (no row map: synthetic rows)", "suppress_order": "–", "text": "drop"},
               "decisions": [{"table": "ed_visits", "column": c, "role": r, "action": act, "output_column": oc, "reason": why} for c, r, act, oc, why in [
                   ("arrival_ts", "quasi_identifier", "dp_histogram", "arrival_ts", "ISO week from the noisy (week, age, diagnosis) histogram; hour sampled from a separate noisy hour histogram"),
                   ("date_of_birth", "quasi_identifier", "dp_histogram", "age", "10-year age band, noisy counts"),
                   ("icd10_primary", "sensitive_attribute", "dp_histogram", "diagnosis_group", "diagnosis group, noisy counts"),
                   ("all other columns", "–", "drop", None, "a public dashboard needs none of them"),
                   ("workload (254 queries)", "–", "dp_answers", "dp_answers.csv", "Laplace noise, ε split over five query families")]],
               "log": [{"step": "dp", **dp_meta}] if dp_meta else []}
    sc = json.loads((run / "scorecard.json").read_text(encoding="utf-8"))
    scen = io.get_scenario(scenario, root) or {}
    labels = pd.read_csv(labels_path or (outputs_dir() / "detect" / f"{dataset}_labels.csv"), dtype=str).fillna("")
    det_scores = outputs_dir() / "detect" / "scores.json"
    det = json.loads(det_scores.read_text(encoding="utf-8")) if det_scores.exists() else {}
    search_dir = outputs_dir() / "search" / dataset / scenario / "catalog"
    search = json.loads((search_dir / "search_summary.json").read_text(encoding="utf-8")) if (search_dir / "search_summary.json").exists() else None
    frontier_png = search_dir / "frontier.png"
    e = html.escape
    s = sc["summary"]
    parts = [f"<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'><title>PETs Transformer — {e(dataset)} / {e(scenario)}</title><style>{CSS}</style></head><body>"]
    parts.append(f"<h1>{e(title or 'PETs Transformer')} — protected release report</h1>"
                 f"<p class='sub'>Dataset <b>{e(dataset)}</b> · scenario <b>{e(scenario)}</b> · variant <code>{e(variant)}</code> · "
                 f"labels from <b>{'the detector (no catalog)' if labels_path else 'the field catalog (oracle)'}</b></p>")

    # 5. scenario first: a PET is only right for a recipient and a purpose
    parts.append("<h2>Sharing scenario</h2>")
    if scen:
        parts.append(f"<p><b>{e(scen.get('title', scenario))}</b> — recipient: {e(str(scen.get('recipient', '')))}; purpose: {e(str(scen.get('purpose', '')))}; "
                     f"trust level: <code>{e(str(scen.get('trust_level', '')))}</code>.</p>"
                     f"<p class='small'>Threat model: {e(str(scen.get('threat_model', '')))}<br>Legal context: {e(str(scen.get('legal_context', '')))}</p>")
    parts.append('<div class="tiles">' + _tile("Privacy (1 − risk)", s["privacy"]) + _tile("Utility retained", s["utility"]) + _tile("Composite", s["composite"]) + "</div>"
                 "<p class='small'>Self-assessment with <code>pets-eval</code> (privacy = 1 − (0.5·worst + 0.5·mean risk); utility = mean score of the scenario's tasks; composite = weighted harmonic mean). Not the official judging score.</p>")

    # 1. detect
    parts.append("<h2>1 · Data types identified</h2>")
    if det:
        h = det.get("held_out") or det.get("all")
        parts.append(f"<p>Detector measured against the field catalog: held-out datasets macro-F1 <b>{det.get('held_out', {}).get('macro_f1', '–')}</b>, "
                     f"sensitive-column recall <b>{det.get('held_out', {}).get('sensitive_recall', '–')}</b> (B1 baseline 0.65 / 0.73); "
                     f"all datasets macro-F1 <b>{det.get('all', {}).get('macro_f1', '–')}</b>. Rules were tuned on {', '.join(det.get('dev_datasets', []))} only.</p>")
    lab = labels[labels.dataset == dataset]
    for tname, g in lab.groupby("table", sort=False):
        parts.append(f"<h3 style='font-size:15px;margin:14px 0 4px'>{e(tname)} <span class='small'>({len(g)} columns)</span></h3><table><tr><th>column</th><th>role</th><th>subtype</th><th>evidence</th><th>sample</th></tr>")
        for _, r in g.iterrows():
            parts.append(f"<tr><td><code>{e(r.column)}</code></td><td><span class='badge b-{e(r.privacy_role)}'>{e(r.privacy_role)}</span></td>"
                         f"<td>{e(r.subtype)}</td><td>{e(r.get('evidence', ''))}</td><td class='small'>{e(str(r.get('sample', ''))[:60])}</td></tr>")
        parts.append("</table>")

    # 2. select
    parts.append("<h2>2 · PET selected for each field</h2>")
    pol = dec["policy"]
    parts.append(f"<p class='small'>Policy: time resolution <code>{e(str(pol.get('time_res')))}</code>, age bands <code>{pol.get('age_band')}</code> y (top-coded {pol.get('age_top')}+), "
                 f"codes truncated to <code>{pol.get('code_chars')}</code> chars, rare categories (&lt;{pol.get('rare_min')}) → other, local suppression to k ≥ <code>{pol.get('k')}</code> "
                 f"(order: {e(str(pol.get('suppress_order')))}), free text: <code>{e(str(pol.get('text')))}</code>, keyed pseudonyms (HMAC-SHA256).</p>")
    for tname in sorted({d["table"] for d in dec["decisions"]}):
        parts.append(f"<h3 style='font-size:15px;margin:14px 0 4px'>{e(tname)}</h3><table><tr><th>column</th><th>role</th><th>action</th><th>output</th><th>why</th></tr>")
        for d in [x for x in dec["decisions"] if x["table"] == tname]:
            parts.append(f"<tr><td><code>{e(d['column'])}</code></td><td><span class='badge b-{e(d['role'])}'>{e(d['role'])}</span></td><td><b>{e(d['action'])}</b></td>"
                         f"<td>{('<code>' + e(d['output_column']) + '</code>') if d['output_column'] else '<i>removed</i>'}</td><td>{e(d['reason'])}</td></tr>")
        parts.append("</table>")
    supp = [l for l in dec["log"] if l.get("step") in ("local_suppression", "k_check", "withheld")]
    if supp:
        parts.append("<ul class='small'>" + "".join(f"<li>{e(json.dumps(l, ensure_ascii=False))}</li>" for l in supp) + "</ul>")

    # 3. transform
    parts.append("<h2>3 · Protected release</h2><table><tr><th>table</th><th>status</th><th>rows</th><th>columns kept</th><th>columns dropped</th></tr>")
    for t, tr in sc["tables"].items():
        if tr.get("status") == "released":
            kept = [c for c in io.read_table(run / f"{t}.csv").columns]
            parts.append(f"<tr><td><code>{e(t)}</code></td><td>released</td><td>{tr.get('rows_protected')}</td><td class='small'>{e(', '.join(kept))}</td><td class='small'>{e(', '.join(tr.get('columns_dropped', [])))}</td></tr>")
        else:
            parts.append(f"<tr><td><code>{e(t)}</code></td><td>{e(str(tr.get('status')))}</td><td>{tr.get('rows_original', '')}</td><td colspan='2' class='small'>not needed by the scenario, so not released (no risk, no utility)</td></tr>")
    parts.append("</table>")
    for t, tr in sc["tables"].items():
        if tr.get("status") == "released":
            df = io.read_table(run / f"{t}.csv").head(4)
            parts.append(f"<p class='small'>First rows of <code>{e(t)}</code> (protected):</p><table><tr>" + "".join(f"<th>{e(str(c))}</th>" for c in df.columns[:12]) + "</tr>")
            for _, r in df.iterrows():
                parts.append("<tr>" + "".join(f"<td class='small'>{e(str(v))[:22]}</td>" for v in r.values[:12]) + "</tr>")
            parts.append("</table>")

    # 4. demonstrate
    parts.append("<h2>4 · Privacy risk removed, utility kept</h2><div class='cols'><div><p><b>Risk components</b> (0 = none, 1 = maximal)</p><table>")
    for k, v in sc["risk_components"].items():
        parts.append(f"<tr><td class='small'>{e(k)}</td><td>{_bar(v, '#c62828')}</td></tr>")
    parts.append("</table></div><div><p><b>Utility tasks</b> (1 = identical answer)</p><table>")
    for u in sc["utility"]:
        v = u["score"]
        parts.append(f"<tr><td class='small'>{e(u['id'])} {e(u['description'][:70])}</td><td>{_bar(v, '#1565c0') if v is not None else '<span class=small>not answerable</span>'}</td></tr>")
    parts.append("</table></div></div>")
    for t, tr in sc["tables"].items():
        for adv, res in (tr.get("linkage") or {}).items():
            if isinstance(res, dict) and res.get("evaluated"):
                parts.append(f"<p class='small'>Linkage attack <b>{e(adv)}</b> on {e(t)}: {e(res.get('attack', ''))} → re-identified "
                             f"<b>{res.get('reidentification_rate', 0):.1%}</b> of {res.get('n_targets_in_data')} targets (original data: {res.get('baseline_reidentification_rate_original', 0) or 0:.1%}); median candidate set {res.get('median_candidate_set')}.</p>")
    eps_png = outputs_dir() / "search" / dataset / scenario / "eps_frontier.png"
    if dp_meta and eps_png.exists():
        parts.append("<h3 style='font-size:15px'>Utility vs privacy budget ε</h3>"
                     f"<img src='data:image/png;base64,{base64.b64encode(eps_png.read_bytes()).decode()}' alt='epsilon sweep'>"
                     f"<p class='small'>This release: ε = {dp_meta['epsilon_total']} ({e(json.dumps(dp_meta['epsilon']))}), δ = 0, unit of privacy = person, "
                     f"contribution bound {dp_meta['contribution_bound_visits']} visits; {dp_meta['rows_released']} synthetic rows from {dp_meta['cells_h1']} noisy cells. {e(dp_meta['note'])}.</p>")
    if search:
        b = search["best"]
        parts.append(f"<h3 style='font-size:15px'>Privacy–utility frontier ({search['n_runs']} policies tried)</h3>")
        if frontier_png.exists():
            parts.append(f"<img src='data:image/png;base64,{base64.b64encode(frontier_png.read_bytes()).decode()}' alt='frontier'>")
        parts.append(f"<p class='small'>Chosen policy: {e(json.dumps(b['params']))} → privacy {b['privacy']:.2f}, utility {b['utility']:.2f}, composite {b['composite']:.2f}. "
                     f"Pareto front: {e(', '.join(f'({r['privacy']:.2f}, {r['utility']:.2f})' for r in search['front']))}.</p>")

    # 5. residual risks
    parts.append("<h2>5 · What remains, honestly</h2><ul>")
    for k, v in sc["risk_components"].items():
        if v and v > 0:
            parts.append(f"<li>{e(k)}: <b>{v:.1%}</b> — still open.</li>")
    lost = [u["id"] for u in sc["utility"] if u["score"] is None or (u["score"] or 0) < 0.5]
    if lost:
        parts.append(f"<li>Utility sacrificed: {e(', '.join(lost))} (the scenario loses these questions; e.g. time of day was coarsened to protect against linkage).</li>")
    parts.append("<li>Privacy score is measured against the attacker files shipped with the package; a stronger or differently informed attacker can do better. A low rate here is evidence, not proof.</li>"
                 "<li>Pseudonyms are keyed (HMAC); whoever holds the key can re-identify. The key must live in the controller's vault, never with the recipient.</li>"
                 "<li>Rows in classes smaller than k had quasi-identifier values blanked; blanks are themselves a visible value and are counted as a class.</li>"
                 "<li>Withheld tables and dropped columns are the strongest PET: no data, no risk — but also no utility.</li></ul>")

    # free-text redaction evidence
    red = sorted((outputs_dir() / "redact").glob(f"{dataset}__*__*/scores.json"))
    if red:
        parts.append("<h2>Free-text redaction (measured against gold spans)</h2><table><tr><th>field</th><th>micro F2 (overlap)</th><th>identifying leakage</th><th>weakest labels</th></tr>")
        for f in red:
            r = json.loads(f.read_text(encoding="utf-8"))
            ov = r["overlap"]["micro"]
            weak = sorted(((k, v["recall"]) for k, v in r["overlap"]["per_label"].items() if v["tp"] + v["fn"] >= 10), key=lambda x: x[1])[:3]
            parts.append(f"<tr><td><code>{e(f.parent.name)}</code></td><td>{ov['f2']:.3f} (P {ov['precision']:.2f} / R {ov['recall']:.2f})</td>"
                         f"<td>{r['leakage']['identifying_leakage_rate']:.2%}</td><td class='small'>{e(', '.join(f'{k} R={v:.2f}' for k, v in weak))}</td></tr>")
        parts.append("</table>")

    parts.append("<h2>Reproduce</h2><pre class='small'>python run.py detect --dataset all\npython run.py protect --dataset "
                 f"{e(dataset)} --scenario {e(scenario)} --variant {e(variant)} --time-res {e(str(pol.get('time_res')))}"
                 f"{' --labels ' + e(labels_path) if labels_path else ''}\npython run.py search --dataset {e(dataset)} --scenario {e(scenario)}\n"
                 f"python run.py report --dataset {e(dataset)} --scenario {e(scenario)} --variant {e(variant)}</pre>")
    parts.append("<p class='small'>PETs Transformer · Since AI Hackathon 2026 · TrustED challenge · evaluation toolkit: pets-eval 1.0.0 (Since AI ry, MIT). All data synthetic.</p></body></html>")
    out = outputs_dir() / "report"
    out.mkdir(exist_ok=True)
    path = out / f"{dataset}_{scenario}_{variant}.html"
    path.write_text("".join(parts), encoding="utf-8", newline="\n")
    return path
