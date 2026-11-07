"""FIWARE demo video (about 90 s) for the FIWARE Open Innovation Award entry: the NGSI-LD catalogue, scope/scopeQ and the
credential + PEP layer, with the outputs of the live runs on the Antares broker (browser playground, 7 Nov 2026).

The tables are the console output of `outputs/fiware/antares_console.js` pasted into
https://antaresbroker.joinedcontext.com/demo/ (captured 7 Nov 2026, about 16:30 / 21:35 / 21:45 EET); the run can be repeated with
`python run.py fiware` and the same paste. Same pipeline as tools/build_video.py (Edge headless -> edge-tts -> ffmpeg).

    python tools/build_fiware_video.py      # writes outputs/video/fiware_demo.mp4
"""
from __future__ import annotations

import asyncio
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_video import CSS, EDGE, OUT, duration, render_png, srt_time, tts  # noqa: E402

FV = OUT / "fiware"
FOOT = ("<div class='foot'><span>PETs Transformer · FIWARE Open Innovation Award · team Cowork (Since AI Hackathon 2026)</span>"
        "<span>live run: Antares NGSI-LD broker</span></div>")
EXTRA = """
.mini td,.mini th{font-size:18px;padding:5px 10px}.ok{color:#5ed38a;font-weight:600}.no{color:#ff8a8a;font-weight:600}
.box{background:#1a2230;border:1px solid #2b3647;border-radius:12px;padding:12px 16px}
.arrow{color:#7fb3ff;font-size:18px}
"""


def page(title: str, body: str, tag: str) -> str:
    return (f"<!doctype html><html><head><meta charset='utf-8'><style>{CSS}{EXTRA}</style></head><body><div class='s'>"
            f"<div class=tag>{tag}</div><h2>{title}</h2>{body}{FOOT}</div></body></html>")


def slides() -> list[tuple[str, str, str]]:
    s = []
    s.append(("f1_title", f"<!doctype html><html><head><meta charset='utf-8'><style>{CSS}{EXTRA}</style></head><body>"
              "<div class='s' style='justify-content:center'><div class='tag'>FIWARE Open Innovation Award · Since AI Hackathon 2026</div>"
              "<h1>PETs Transformer on FIWARE</h1>"
              "<p class='big' style='font-size:36px;color:#7fb3ff'>A context broker as the catalogue of privacy-assessed data releases</p>"
              "<ul><li>NGSI-LD entities + Smart Data Models DCAT-AP</li><li>classification on the data: NGSI-LD <code>scope</code> / <code>scopeQ</code></li>"
              "<li>who the recipient is: signed credential + policy enforcement point</li></ul>"
              f"{FOOT}</div></body></html>",
              "PETs Transformer on FIWARE. Every privacy-protected data release is published to an NGSI-LD context broker as a "
              "catalogue entry, never as rows. Everything you see ran live on the Antares broker."))
    s.append(("f2_model", page("Data model: five entity types, linked", """
<div class='tiles'>
<div class='tile'><div class='v b' style='font-size:28px'>Dataset</div><div class='l'>Smart Data Model <b>DCAT-AP</b><br>title, legislation, provenance</div></div>
<div class='tile'><div class='v b' style='font-size:28px'>Distribution</div><div class='l'><b>DCAT-AP</b>, one per file<br>→ belongsToDataset</div></div>
<div class='tile'><div class='v y' style='font-size:28px'>DataSharingScenario</div><div class='l'>recipient, purpose,<br>trust level, threat model</div></div></div>
<div class='tiles'>
<div class='tile'><div class='v g' style='font-size:28px'>PrivacyAssessment</div><div class='l'>scores, each linkage attack before → after, k-anonymity, DP budget<br>→ refDataset, refScenario</div></div>
<div class='tile'><div class='v g' style='font-size:28px'>FieldPolicy</div><div class='l'>one per source column: role, PET applied, reason<br>→ refDataset</div></div></div>
<p style='font-size:22px'>48 entities for two releases · DCAT-AP entities validated against the official Smart Data Models JSON schemas (PASS)</p>""",
                                      "NGSI-LD + Smart Data Models"),
              "Five entity types. The Smart Data Models DCAT-AP Dataset and Distribution, validated against the official schemas, "
              "plus the sharing scenario, a privacy assessment with every attack before and after, and one field policy per column, "
              "all linked by relationships."))
    s.append(("f3_live", page("Live: the controller publishes, a recipient queries", """
<pre class='live'>controller: batch upsert of 48 entities into tenant "pets-transformer" → HTTP 201
recipient:  PrivacyAssessment with compositeScore >= 0.85 → 1 release</pre>
<table class='mini' style='margin-top:12px'><tr><th>release</th><th>composite</th><th>privacy</th><th>utility</th><th>worst attack before → after</th><th>sacrificed</th><th>field policies</th></tr>
<tr><td>health · research in a controlled environment · 'week'</td><td class='ok'>0.898</td><td>1.0</td><td>0.8</td><td>0.57 → <span class='ok'>0</span></td><td>H-U8</td><td>38</td></tr></table>
<pre class='live' style='margin-top:12px'>why was date_of_birth protected?
ed_visits.date_of_birth: age_band - birth date -> age at event in 10-year bands (top-coded 80+)</pre>""", "layer 1 · the catalogue"),
              "The controller writes forty-eight entities in one batch upsert. A recipient asks for releases with a composite score "
              "of at least zero point eight five, follows the link to the dataset, and reads why each field was protected."))
    s.append(("f4_scope", page("Classification on the data itself: NGSI-LD scope", """
<div class='two'><div class='box' style='flex:0 0 430px'><p style='font-size:20px;margin:0'><b>scope</b> on every entity</p>
<pre class='live' style='font-size:17px;border:0;padding:6px 0'>/health/research
/health/research/pseudonymised
/health/research/pseudonymised/&lt;role&gt;
/health/public
/health/public/dp</pre></div>
<div><table class='mini'><tr><th>recipient · scopeQ</th><th>result (live)</th></tr>
<tr><td>public · <code>/health/public/#</code></td><td>1 release: <b>/health/public/dp</b> (composite 0.777)</td></tr>
<tr><td>research · <code>/health/research/#,/health/public/#</code></td><td>2 releases</td></tr>
<tr><td>any · <code>/health/+/+/direct_identifier</code></td><td>5 fields: hetu → keyed pseudonym; name, phone, address, physician name → dropped</td></tr></table></div></div>""",
                                     "layer 2 · scope / scopeQ"),
              "Classification travels on the data itself, as an NGSI-LD scope. A public recipient filtering with scope Q sees only "
              "the differentially private release. A research recipient sees both. One query lists every direct identifier, and "
              "what was done to it."))
    rows = [("no credential", "PrivacyAssessment", "—", "401 no credential", "no"),
            ("public credential", "PrivacyAssessment", "—", "200 · 1 entity · /health/public/dp", "ok"),
            ("research credential", "PrivacyAssessment", "—", "200 · 2 entities", "ok"),
            ("public asks for research data", "PrivacyAssessment", "/health/research/#", "403 outside trust level", "no"),
            ("public edited to say research", "PrivacyAssessment", "—", "401 signature invalid", "no"),
            ("expired research credential", "PrivacyAssessment", "—", "401 credential expired", "no"),
            ("research, unknown issuer", "PrivacyAssessment", "—", "401 signature invalid", "no"),
            ("research · every direct identifier", "FieldPolicy", "/health/+/+/direct_identifier", "200 · 5 entities", "ok"),
            ("public · every direct identifier", "FieldPolicy", "/health/+/+/direct_identifier", "200 · 0 entities", "ok")]
    trs = "".join(f"<tr><td>{a}</td><td>{b}</td><td><code style='font-size:15px'>{c}</code></td><td class='{e}'>{d}</td></tr>" for a, b, c, d, e in rows)
    s.append(("f5_pep", page("Who the recipient is: signed credential + policy enforcement point", f"""
<p style='font-size:21px;margin:0 0 8px'>The controller signs a data-access credential (ES256, trust level, expiry). The PEP in front of the broker verifies it,
maps the trust level to the scopes it allows and narrows every <code>scopeQ</code> to them.</p>
<table class='mini'><tr><th>recipient</th><th>type</th><th>asked scopeQ</th><th>PEP decision (live)</th></tr>{trs}</table>""",
                                   "layer 3 · access"),
              "Who the recipient is. The controller signs a credential. A policy enforcement point in front of the broker verifies it, "
              "and narrows every scope query to what that trust level allows. Edited, expired or foreign credentials are refused, "
              "and out-of-scope requests get four oh three."))
    s.append(("f6_close", f"<!doctype html><html><head><meta charset='utf-8'><style>{CSS}{EXTRA}</style></head><body>"
              "<div class='s' style='justify-content:center'><h1>Publish the catalogue, never the rows</h1>"
              "<ul><li>NGSI-LD + Smart Data Models DCAT-AP, live on Antares</li><li>scope / scopeQ: the classification travels with the data</li>"
              "<li>credential + PEP: a recipient only sees what its trust level allows</li></ul>"
              "<p style='color:#9fb0c3'>Code: github.com/latentcore/pets-transformer (pets_transformer/fiware.py, access.py) · "
              "tested offline too: mock broker + PEP, 12 of 12 cases · the scope layer was suggested by Chandra Challagonda (FIWARE Foundation)</p>"
              f"{FOOT}</div></body></html>",
              "Publish the catalogue, never the rows. The code and the full results are on GitHub. Thank you to Chandra "
              "Challagonda for suggesting the scope layer."))
    return s


def main() -> None:
    if EDGE is None:
        sys.exit("Microsoft Edge not found for headless rendering")
    FV.mkdir(parents=True, exist_ok=True)
    segs, t, srt, n = [], 0.0, [], 0
    for sid, html_s, narration in slides():
        hp, pp, mp = FV / f"{sid}.html", FV / f"{sid}.png", FV / f"{sid}.mp3"
        hp.write_text(html_s, encoding="utf-8", newline="\n")
        render_png(hp, pp)
        asyncio.run(tts(narration, mp))
        d = duration(mp) + 0.4
        sents = [x.strip() for x in re.split(r"(?<=[.!?:])\s+", narration) if x.strip()]
        total = sum(len(x) for x in sents) or 1
        t0 = t
        for x in sents:
            dur = d * len(x) / total
            n += 1
            srt.append(f"{n}\n{srt_time(t0)} --> {srt_time(t0 + dur - 0.05)}\n{x}\n")
            t0 += dur
        seg = FV / f"{sid}.mp4"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-framerate", "30", "-i", str(pp), "-i", str(mp),
                        "-c:v", "libx264", "-tune", "stillimage", "-pix_fmt", "yuv420p", "-r", "30", "-c:a", "aac", "-ar", "44100", "-ac", "2",
                        "-t", f"{d:.2f}", "-shortest", str(seg)], check=True)
        segs.append(seg)
        t += d
        print(f"{sid}: {d:.1f}s")
    (FV / "captions.srt").write_text("\n".join(srt), encoding="utf-8", newline="\n")
    (FV / "concat.txt").write_text("".join(f"file '{p.as_posix()}'\n" for p in segs), encoding="utf-8", newline="\n")
    raw = FV / "fiware_nocap.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(FV / "concat.txt"), "-c", "copy", str(raw)], check=True)
    srt_path = (FV / "captions.srt").as_posix().replace(":", "\\:")
    vf = ("pad=1280:840:0:0:color=0x0f1419,"
          f"subtitles='{srt_path}':force_style='FontName=Segoe UI,FontSize=12,PrimaryColour=&H00FFFFFF,"
          "BorderStyle=1,Outline=0,Shadow=0,Alignment=2,MarginV=6,MarginL=20,MarginR=20'")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(raw), "-vf", vf,
                    "-c:v", "libx264", "-crf", "20", "-preset", "medium", "-pix_fmt", "yuv420p", "-c:a", "copy",
                    str(OUT / "fiware_demo.mp4")], check=True)
    print(f"total {t:.1f}s -> {OUT / 'fiware_demo.mp4'}")


if __name__ == "__main__":
    main()
