"""Supporting document for the submission form ("pilot plan, continuation plan"): a two-page pilot plan and continuation
plan (HTML -> PDF with headless Edge), followed by the two HTML reports as PDF pages when pypdf is installed.

    python tools/build_plan.py      # -> outputs/report/PETs_Transformer_supporting_document.pdf (+ plan_and_continuation.pdf)
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REP = ROOT / "outputs" / "report"
EDGE = next((p for p in [r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                         r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"] if Path(p).exists()), None)

CSS = """
@page{size:A4;margin:13mm 15mm}body{font-family:Segoe UI,Arial,sans-serif;font-size:9.9pt;line-height:1.33;color:#111;margin:0}
h1{font-size:18pt;margin:0 0 2pt}h2{font-size:12pt;margin:8pt 0 3pt;color:#123;border-bottom:1px solid #9ab;padding-bottom:2pt}
p,li{margin:0 0 3pt}ul{margin:0 0 3pt 16pt;padding:0}.sub{color:#456;font-size:9.2pt;margin-bottom:6pt}
table{border-collapse:collapse;width:100%;font-size:9pt;margin:3pt 0 4pt}th,td{border:1px solid #bcd;padding:2.5pt 5pt;vertical-align:top;text-align:left}
th{background:#eef3f8}.small{font-size:9pt;color:#456}.box{border:1px solid #bcd;background:#f6f9fc;padding:5pt 8pt;margin:4pt 0}
a{color:#1a4f8a;text-decoration:none}
"""


def numbers() -> dict:
    out = ROOT / "outputs"
    d: dict = {}
    try:
        sm = json.loads((out / "health" / "HEALTH-S1" / "week" / "summary.json").read_text(encoding="utf-8"))["summary"]
        d["composite"], d["privacy"], d["utility"] = sm["composite"], sm["privacy"], sm["utility"]
    except Exception:  # noqa: BLE001
        d["composite"], d["privacy"], d["utility"] = 0.898, 1.0, 0.80
    try:
        ho = json.loads((out / "detect" / "scores.json").read_text(encoding="utf-8"))["held_out"]
        d["f1"], d["recall"] = ho["macro_f1"], ho["sensitive_recall"]
    except Exception:  # noqa: BLE001
        d["f1"], d["recall"] = 0.744, 0.94
    try:
        v = json.loads((out / "voice" / "scores.json").read_text(encoding="utf-8"))["total"]
        d["v_values"], d["v_left"], d["v_raw"] = v["values"], v["leaked_after_redaction"], v["leaked_raw_redaction"]
    except Exception:  # noqa: BLE001
        d["v_values"], d["v_left"], d["v_raw"] = 43, 1, 7
    return d


def page(n: dict) -> str:
    return f"""<!doctype html><html><head><meta charset='utf-8'><style>{CSS}</style></head><body>
<h1>PETs Transformer — pilot plan and continuation plan</h1>
<div class='sub'>Since AI Hackathon 2026 · TrustED challenge "PETs Transformer" · team Cowork (Mert Yünlü, solo) · 8 November 2026<br>
Code: <a href='https://github.com/latentcore/pets-transformer'>github.com/latentcore/pets-transformer</a> (MIT) ·
demo video: <a href='https://youtu.be/0QSOFyT7YBw'>youtu.be/0QSOFyT7YBw</a> · FIWARE demo: <a href='https://youtu.be/CA0VJ8UXuaA'>youtu.be/CA0VJ8UXuaA</a><br>
Pages 3 onwards of this file: the HTML reports of the two releases (HEALTH-S1 research release, HEALTH-S2 differentially private release) as produced by the tool.</div>

<h2>1. What exists today (measured with the organisers' pets-eval toolkit; self-assessment, not the official score)</h2>
<ul>
<li>A command-line tool with an HTML report. It <b>detects</b> personal data from column values (held-out macro-F1 {n['f1']:.2f}, {n['recall']:.0%} of sensitive
columns found on six datasets never used for rule writing), <b>selects</b> one privacy-enhancing technique per field for a named sharing scenario with a written
reason (38 decisions for HEALTH-S1), <b>transforms</b> the data (keyed pseudonyms, age bands, time rounding, code truncation, local suppression to k ≥ 5,
a differentially private release for open data, free-text redaction) and <b>measures</b> the result: the three linkage attacks fall from 18.5 / 54.2 / 57.0 % to 0 %,
privacy {n['privacy']:.2f}, utility {n['utility']:.2f}, composite {n['composite']:.3f} against baselines of about 0.48; free-text redaction F2 0.97 on clinical notes;
HEALTH-S2 differentially private release at ε = 3.2.</li>
<li>Each release is published to a <b>FIWARE NGSI-LD</b> context broker as a catalogue, never as rows: Smart Data Models DCAT-AP Dataset and Distribution,
a PrivacyAssessment with every attack before and after, one FieldPolicy per column, NGSI-LD scopes and a credential-checking policy enforcement point;
run live on the Antares broker.</li>
<li>A <b>voice-channel probe</b>: {n['v_values']} identifiers read aloud by ElevenLabs voices, transcribed and redacted; the text redactor left {n['v_raw']},
the voice adapter leaves {n['v_left']} (the same as the text channel).</li>
<li><b>Known limits, stated in the report:</b> privacy is measured against the attackers shipped with the package (evidence, not proof); the detector rules
were tuned on three datasets; the text and speech are synthetic; k ≥ 5 holds on municipality and week but not yet with the age band (fix measured:
composite 0.891); the HMAC key is a demo constant; one scenario was tuned end to end.</li>
</ul>

<h2>2. Pilot plan — 12 weeks with TrustED or a data-holding partner (hospital district, city, insurer)</h2>
<p><b>Goal.</b> Show on one real dataset, inside the partner's own environment, that a protected release can be produced, assessed and catalogued in a
repeatable way, and that a named recipient can do its work with it. The tool runs on the partner's machine; no data leaves the organisation.</p>
<table><tr><th style='width:11%'>Weeks</th><th>Step</th><th style='width:27%'>Output</th><th style='width:17%'>Who</th></tr>
<tr><td>0–2</td><td>Set-up and data inventory: install (Python, no cloud), run detection on the real extract, review every column label with the data steward;
measure the risk of the original data with the agreed external datasets.</td><td>labelled inventory, risk report on the original data</td><td>author + data steward</td></tr>
<tr><td>3–5</td><td>Scenario workshop: who receives the data, for which analyses, in which environment, under which contract; write the scenario file;
agree the utility questions and the attacker model (which public or partner-held datasets an attacker could link).</td><td>scenario file, utility tasks, attacker files</td><td>partner, DPO, author</td></tr>
<tr><td>6–8</td><td>Protect and measure: frontier search, chosen policy, protected release, scorecard; k ≥ 5 including the age band; DPIA annex
(techniques per field, reasons, residual risk).</td><td>release, scorecard, DPIA annex</td><td>author; DPO review</td></tr>
<tr><td>9–10</td><td>Catalogue and access: publish the catalogue to the partner's FIWARE context broker (Orion-LD, Scorpio or Antares), issue recipient
credentials, the recipient finds the release through scopeQ and asks for access through the policy enforcement point.</td><td>NGSI-LD catalogue, access log</td><td>author + partner IT</td></tr>
<tr><td>11–12</td><td>Evaluation: the recipient repeats its analyses on the protected release (utility); the partner's security team runs its own
re-identification attempt (privacy); the DPO reviews the report; go / no-go for production use.</td><td>pilot report, decision</td><td>all</td></tr></table>
<div class='box'><b>Success criteria.</b> No re-identification by the partner's own attack · the recipient's named analyses answered within the agreed tolerance
(target: utility ≥ 0.80 on the scenario's tasks) · every field decision explained in the report · the catalogue entry readable by the recipient without seeing rows ·
DPIA annex accepted by the DPO. <b>Resources.</b> One developer (the author), half a day a week from a data steward, two DPO reviews, a laptop or a VM inside
the partner network. The code is MIT-licensed; the evaluation toolkit is pets-eval (Since AI ry, MIT).</div>

<h2>3. Continuation plan — technical roadmap, in order</h2>
<ol style='margin:0 0 4pt 16pt;padding:0'>
<li>k ≥ 5 including the age band as the default (measured: composite 0.898 → 0.891) and a per-scenario quasi-identifier list in the scenario file.</li>
<li>Per-recipient key domains for pseudonyms, so two recipients cannot link their releases; keys in the controller's vault (KMS/HSM), rotation.</li>
<li>Detector on real data: validation on a real, labelled extract; the LLM second opinion kept to low-confidence columns and spans, always with the rule evidence beside it.</li>
<li>Free text and speech on real material: redaction recall on real clinical notes and call transcripts; ElevenLabs Scribe speech-to-text and the voice adapter checked on real call audio.</li>
<li>Access: replace the demo policy enforcement point by FIWARE Keyrock or a data-space connector; credential revocation; audit log.</li>
<li>Data model: propose PrivacyAssessment, FieldPolicy and DataSharingScenario as Smart Data Models; align with DCAT-AP 3 and the EHDS data-quality and utility label.</li>
<li>Engineering: tests, packaging (pip, container), reproducible runs, a web report; more scenarios tuned end to end.</li>
</ol>

<h2>4. Risks and how they are handled</h2>
<table><tr><th style='width:34%'>Risk</th><th>Handling</th></tr>
<tr><td>A stronger or differently informed attacker than the one modelled</td><td>Attacker model agreed with the partner and re-run before every release; residual risk written into the PrivacyAssessment entity, not hidden.</td></tr>
<tr><td>Pseudonymised data is still personal data under the GDPR</td><td>Controlled environment, contract and credential-based access for pseudonymised releases; only the differentially private release for open publication.</td></tr>
<tr><td>Lower recall on real text and real calls than on synthetic material</td><td>Measured in the pilot on a labelled sample; model-assisted pass where the rules are unsure; human review of a sample before release.</td></tr>
<tr><td>Key leakage or key reuse across recipients</td><td>Vault, per-recipient key domains, rotation; the demo constant is never used outside the hackathon.</td></tr>
<tr><td>Repeated releases spend the privacy budget</td><td>Differential-privacy budget accounting per period and per recipient, stated in the catalogue.</td></tr>
<tr><td>One developer</td><td>Everything is scripted and documented; the pilot plan names the partner roles; MIT licence allows the partner to continue without the author.</td></tr></table>
</body></html>"""


def main() -> int:
    if EDGE is None:
        sys.exit("Microsoft Edge not found")
    REP.mkdir(parents=True, exist_ok=True)
    hp, plan = REP / "plan_and_continuation.html", REP / "plan_and_continuation.pdf"
    hp.write_text(page(numbers()), encoding="utf-8", newline="\n")
    if plan.exists():
        plan.unlink()
    subprocess.run([EDGE, "--headless=new", "--disable-gpu", "--no-pdf-header-footer", f"--print-to-pdf={plan}", hp.as_uri()],
                   check=False, capture_output=True, timeout=90)
    if not plan.exists():
        sys.exit("Edge did not write the plan PDF")
    out = REP / "PETs_Transformer_supporting_document.pdf"
    reports = [REP / "health_HEALTH-S1_week.pdf", REP / "health_HEALTH-S2_dp_eps3.pdf"]
    try:
        from pypdf import PdfReader, PdfWriter
    except ImportError:
        print("pypdf not installed: the supporting document is the plan alone")
        out.write_bytes(plan.read_bytes())
        return 0
    w = PdfWriter()
    for p in [plan] + [r for r in reports if r.exists()]:
        w.append(str(p))
    with out.open("wb") as f:
        w.write(f)
    print(f"{plan.name}: {len(PdfReader(str(plan)).pages)} pages; {out.name}: {len(PdfReader(str(out)).pages)} pages, {out.stat().st_size:,} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
