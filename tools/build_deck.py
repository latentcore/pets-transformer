"""Pitch deck PDF (voters can open it from the vote card): the video slides plus deck-only slides.

The video stays as published; the deck adds three slides in the same style: TrustED's five points with evidence
(after the title), the HEALTH-S2 differential-privacy release and the FIWARE catalogue (after the free-text slide).
"""
import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.image as mpimg  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.backends.backend_pdf import PdfPages  # noqa: E402

PROJECT = Path(__file__).resolve().parents[1]
OUT = PROJECT / "outputs" / "video"
EXTRA = OUT / "deck_extra"
sys.path.insert(0, str(PROJECT / "tools"))
from build_video import render_png, slide  # noqa: E402


def extra_slides() -> list[tuple[str, str]]:
    o = PROJECT / "outputs"
    s1 = json.loads((o / "health" / "HEALTH-S1" / "week" / "summary.json").read_text(encoding="utf-8"))["summary"]
    s2 = json.loads((o / "health" / "HEALTH-S2" / "dp_eps3" / "summary.json").read_text(encoding="utf-8"))
    det = json.loads((o / "detect" / "scores.json").read_text(encoding="utf-8"))["held_out"]
    eps_png = (o / "search" / "health" / "HEALTH-S2" / "eps_frontier.png").as_posix()
    out = [("01b_checklist", slide("TrustED's five points, each with evidence", f"""
<table style='font-size:22px'><tr><th>TrustED asks for</th><th>we show</th></tr>
<tr><td>1 · data types and privacy risks</td><td>every column labelled from its values ({det['sensitive_recall']:.0%} of sensitive columns on 6 unseen datasets); original data: attacks re-identify <span class='y'>18.5 / 54.2 / 57.0 %</span></td></tr>
<tr><td>2 · PETs and the reasoning</td><td>one action per field with a written reason: 38 decisions in <code>decisions.json</code></td></tr>
<tr><td>3 · protected output</td><td>HEALTH-S1 tables (k ≥ 5), HEALTH-S2 differentially private release, redacted free text</td></tr>
<tr><td>4 · measurable privacy and utility</td><td>attacks <span class='g'>0 %</span> · privacy <span class='g'>{s1['privacy']:.2f}</span> · utility <span class='g'>{s1['utility']:.2f}</span> · composite <span class='g'>{s1['composite']:.2f}</span> (baselines ≈0.48)</td></tr>
<tr><td>5 · scenario and limitations</td><td>research group in a controlled environment; hour-of-day curve sacrificed, frontier of 81 policies shows why</td></tr></table>""",
                                       "the challenge checklist"))]
    out.append(("08b_dp", slide("Public dashboard: a differentially private release", f"""
<div class='two'><div><img src='file:///{eps_png}' width='640'></div>
<div><ul><li>Scenario HEALTH-S2: open data, anyone can download every weekly release</li>
<li>Laplace mechanism at <b>person level</b>, at most 4 visits per person</li>
<li>Total budget <b>ε = {s2['dp']['epsilon_total']}</b>, stated and composed</li>
<li>Utility <b>{s2['summary']['utility']:.2f}</b>, privacy <b>{s2['summary']['privacy']:.2f}</b>; the curve shows what each extra ε buys</li>
<li>No row links back to a person</li></ul></div></div>""", "a second scenario")))
    out.append(("08c_fiware", slide("FIWARE: the broker as a catalogue of assessed releases", """
<div class='tiles'>
<div class='tile'><div class='v b' style='font-size:30px'>Dataset</div><div class='l'>Smart Data Model DCAT-AP<br>title, legislation, provenance</div></div>
<div class='tile'><div class='v b' style='font-size:30px'>Distribution</div><div class='l'>DCAT-AP, one per file<br>→ belongsToDataset</div></div>
<div class='tile'><div class='v g' style='font-size:30px'>PrivacyAssessment</div><div class='l'>scores, each attack before → after<br>→ refDataset, refScenario</div></div>
<div class='tile'><div class='v y' style='font-size:30px'>FieldPolicy</div><div class='l'>one per column: PET + reason<br>→ refDataset</div></div></div>
<p>The controller publishes <b>the catalogue, never the rows</b>: NGSI-LD batch upsert of 48 entities for two releases, DCAT-AP validated against the official schemas, <b>running live on the Antares broker</b>.</p>
<p>The recipient asks the broker: <code>type=PrivacyAssessment &amp; q=compositeScore&gt;=0.85</code> → follows <code>refDataset</code> → reads why each field was protected.</p>""",
                                    "FIWARE NGSI-LD + Smart Data Models")))
    return out


def main() -> None:
    EXTRA.mkdir(parents=True, exist_ok=True)
    for sid, html in extra_slides():
        hp, pp = EXTRA / f"{sid}.html", EXTRA / f"{sid}.png"
        hp.write_text(html, encoding="utf-8")
        render_png(hp, pp)
    pages = sorted(list((OUT / "slides").glob("*.png")) + list(EXTRA.glob("*.png")), key=lambda p: p.stem)
    with PdfPages(OUT / "deck.pdf") as pdf:
        for p in pages:
            img = mpimg.imread(p)
            fig = plt.figure(figsize=(12.8, 7.2), dpi=100)
            ax = fig.add_axes([0, 0, 1, 1])
            ax.imshow(img)
            ax.axis("off")
            pdf.savefig(fig)
            plt.close(fig)
    print(f"{len(pages)} pages -> {OUT / 'deck.pdf'}: {[p.stem for p in pages]}")


if __name__ == "__main__":
    main()
