"""Build the 2-minute demo video: HTML slides -> PNG (headless Edge) -> narration (edge-tts) -> MP4 with captions (ffmpeg).

    python tools/build_video.py            # writes outputs/video/demo.mp4
"""
from __future__ import annotations

import asyncio
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
OUT = PROJECT / "outputs" / "video"
SL = OUT / "slides"
VOICE = "en-GB-RyanNeural"
EDGE = next((p for p in [r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                         r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"] if Path(p).exists()), None)
sys.path.insert(0, str(PROJECT))

CSS = """
*{box-sizing:border-box}body{margin:0;width:1280px;height:720px;overflow:hidden;background:#0f1419;color:#e9eef3;
font-family:Segoe UI,system-ui,sans-serif}.s{width:1280px;height:720px;padding:56px 72px;display:flex;flex-direction:column}
h1{font-size:54px;margin:0 0 10px;letter-spacing:-.5px}h2{font-size:38px;margin:0 0 22px;color:#fff}
.tag{color:#7fb3ff;font-size:22px;letter-spacing:.5px;text-transform:uppercase;margin-bottom:14px}
p,li{font-size:27px;line-height:1.4;color:#d6dee7}ul{margin:0;padding-left:30px}li{margin:8px 0}
.big{font-size:64px;font-weight:700;color:#fff}.tiles{display:flex;gap:22px;margin:18px 0}
.tile{flex:1;background:#1a2230;border:1px solid #2b3647;border-radius:14px;padding:22px;text-align:center}
.tile .v{font-size:60px;font-weight:700}.tile .l{font-size:20px;color:#9fb0c3}.g{color:#5ed38a}.b{color:#7fb3ff}.y{color:#f3c56b}
table{border-collapse:collapse;width:100%;font-size:21px}th,td{padding:7px 12px;border-bottom:1px solid #2b3647;text-align:left}
th{color:#9fb0c3;font-weight:600}code{background:#1a2230;padding:2px 8px;border-radius:6px;font-size:21px;color:#c8d6e5}
.badge{display:inline-block;padding:2px 10px;border-radius:12px;font-size:17px;font-weight:600;color:#fff}
.b-direct_identifier{background:#c62828}.b-quasi_identifier{background:#ef6c00}.b-sensitive_attribute{background:#6a1b9a}
.b-technical_identifier{background:#1565c0}.b-free_text{background:#00838f}.b-record_key{background:#546e7a}.b-non_sensitive{background:#2e7d32}
.foot{position:absolute;bottom:26px;left:72px;right:72px;font-size:18px;color:#6b7b8d;display:flex;justify-content:space-between}
.red{background:#3a1d1d;border-radius:6px;padding:0 6px;color:#ffb4b4}.grn{background:#173a2a;border-radius:6px;padding:0 6px;color:#9be6b6}
.two{display:flex;gap:40px}.two>div{flex:1}img{border-radius:10px}
"""


def slide(title: str, body: str, tag: str = "") -> str:
    return (f"<!doctype html><html><head><meta charset='utf-8'><style>{CSS}</style></head><body><div class='s'>"
            f"{'<div class=tag>' + tag + '</div>' if tag else ''}<h2>{title}</h2>{body}"
            f"<div class='foot'><span>PETs Transformer · Since AI Hackathon 2026 · TrustED challenge</span><span>team Cowork · Mert Yünlü</span></div>"
            f"</div></body></html>")


def build_slides() -> list[tuple[str, str, str]]:
    import pandas as pd
    out = PROJECT / "outputs"
    det = json.loads((out / "detect" / "scores.json").read_text(encoding="utf-8"))
    ho, al = det["held_out"], det["all"]
    sm = json.loads((out / "health" / "HEALTH-S1" / "week" / "summary.json").read_text(encoding="utf-8"))["summary"]
    search = json.loads((out / "search" / "health" / "HEALTH-S1" / "catalog" / "search_summary.json").read_text(encoding="utf-8"))
    clin = json.loads((out / "redact" / "health__clinical_notes__note_text" / "scores.json").read_text(encoding="utf-8"))
    cs = json.loads((out / "redact" / "customer_service__turns__text" / "scores.json").read_text(encoding="utf-8"))
    unseen = pd.read_csv(out / "detect" / "unseen_labels.csv", dtype=str).fillna("")
    dec = json.loads((out / "health" / "HEALTH-S1" / "week" / "decisions.json").read_text(encoding="utf-8"))

    s = []
    s.append(("01_title",
              f"<!doctype html><html><head><meta charset='utf-8'><style>{CSS}</style></head><body><div class='s' style='justify-content:center'>"
              "<div class='tag'>Since AI Hackathon 2026 · TrustED challenge</div><h1>PETs Transformer</h1>"
              "<p class='big' style='font-size:40px;color:#7fb3ff'>Protect the data, keep its value.</p>"
              "<p>A working tool that detects personal data, chooses a protection for every field, transforms the dataset and measures what was removed and what was kept.</p>"
              "<div class='foot'><span>team Cowork · Mert Yünlü · solo</span><span>all data synthetic · measured with pets-eval</span></div></div></body></html>",
              "PETs Transformer: protect the data, keep its value. A solo entry for the TrustED challenge."))
    s.append(("02_problem", slide("Removing names is not enough", """
<ul><li>Hospitals, cities and banks must share data for research and services.</li>
<li>Birth date + postcode + sex single out <b>98.9%</b> of patients in the emergency-department table.</li>
<li>Protect too much, and the data cannot answer the questions it was shared for.</li>
<li>The right protection depends on <b>who receives the data and why</b>.</li></ul>""", "the problem"),
              "Removing names is not enough: birth date, postcode and sex identify almost every patient. Protect too much, and the data becomes useless."))
    s.append(("03_steps", slide("Four steps, one command", """
<div class='tiles'>
<div class='tile'><div class='v b'>1</div><div class='l'>DETECT what each column and text contains — from the values</div></div>
<div class='tile'><div class='v b'>2</div><div class='l'>SELECT a PET per field for the sharing scenario, with a reason</div></div>
<div class='tile'><div class='v b'>3</div><div class='v' style='font-size:0'></div><div class='l'>TRANSFORM: pseudonyms, bands, rounding, suppression, redaction</div></div>
<div class='tile'><div class='v b'>4</div><div class='l'>DEMONSTRATE: attacks re-run, utility tasks re-asked, frontier drawn</div></div></div>
<p><code>python run.py protect --dataset health --scenario HEALTH-S1</code></p>""", "what it does"),
              "Four steps, one command: detect what each column contains, select a protection per field, transform the data, and measure what was removed and what was kept."))
    rows = "".join(f"<tr><td><code>{r.column}</code></td><td><span class='badge b-{r.privacy_role}'>{r.privacy_role}</span></td><td>{r.subtype}</td><td>{r['sample'].split(' | ')[0][:30]}</td></tr>"
                   for _, r in unseen.iterrows() if r.column in ("c04", "c05", "c06", "c07", "c10", "c11", "c16", "c22"))
    s.append(("04_detect", slide("Detect: an HR export with no column names", f"""
<div class='two'><div><table style='font-size:19px'><tr><th>column</th><th>role</th><th>subtype</th><th>sample</th></tr>{rows}</table></div>
<div style='flex:0 0 360px'><div class='tile'><div class='v g'>{ho['macro_f1']:.2f}</div><div class='l'>macro-F1 on six held-out datasets<br>(organisers' baseline 0.65)</div></div>
<div class='tile' style='margin-top:16px'><div class='v g'>{ho['sensitive_recall']:.0%}</div><div class='l'>sensitive columns found<br>(baseline 73%)</div></div></div></div>""", "step 1"),
              "Detection works from the values, not the names. In this HR export without column names it finds national IDs, birth dates, addresses, IBANs, salaries and free text. "
              f"On six unseen datasets: macro F1 {ho['macro_f1']:.2f}, {ho['sensitive_recall']:.0%} of sensitive columns found; baseline 0.65 and 73 percent."))
    picks = [d for d in dec["decisions"] if d["table"] == "ed_visits" and d["column"] in ("patient_hetu", "patient_name", "date_of_birth", "arrival_ts", "municipality", "icd10_primary", "phone", "los_minutes")]
    rows = "".join(f"<tr><td><code>{d['column']}</code></td><td><span class='badge b-{d['role']}'>{d['role']}</span></td><td><b>{d['action']}</b></td><td>{d['reason'][:70]}</td></tr>" for d in picks)
    s.append(("05_select", slide("Select and transform: one decision per field, with a reason", f"""
<table><tr><th>column</th><th>role</th><th>action</th><th>why</th></tr>{rows}</table>
<p style='margin-top:14px'>Then local suppression: every row shares its quasi-identifiers with at least <b>5</b> others. Keyed pseudonyms keep joins; the key stays in the controller's vault.</p>""", "steps 2 and 3"),
              "Every field gets one decision with a reason: keyed pseudonyms keep joins, birth dates become age bands, timestamps become weeks, names and addresses go. Then suppression to k of five."))
    s.append(("06_measure", slide("Demonstrate: attacks re-run, questions re-asked", f"""
<div class='tiles'><div class='tile'><div class='v g'>{sm['privacy']:.0%}</div><div class='l'>privacy score (1 − risk)<br>3 linkage attacks, k-anonymity, hash reversal, text leakage</div></div>
<div class='tile'><div class='v g'>{sm['utility']:.0%}</div><div class='l'>utility retained<br>4 of 5 research tasks answered identically</div></div>
<div class='tile'><div class='v g'>{sm['composite']:.2f}</div><div class='l'>composite<br>classic baselines B2 and B2-k: 0.48</div></div></div>
<table><tr><th>attack (organisers' attacker files)</th><th>original data</th><th>protected</th></tr>
<tr><td>Local news clips: date, place, age, sex of an accident</td><td class='y'>18.5% re-identified</td><td class='g'>0.0%</td></tr>
<tr><td>Social posts from the ER: time, first name, home city</td><td class='y'>54.2%</td><td class='g'>0.0%</td></tr>
<tr><td>Public address directory: name, street, postcode, city</td><td class='y'>57.0%</td><td class='g'>0.0%</td></tr></table>""", "step 4"),
              "The organisers' toolkit re-runs three linkage attacks and five research tasks: privacy one point zero, 80 percent of utility kept, composite 0.90. The classic baselines score 0.48."))
    s.append(("07_frontier", slide("Not one setting — the whole trade-off", f"""
<div class='two'><div><img src='file:///{(PROJECT / 'outputs' / 'search' / 'health' / 'HEALTH-S1' / 'catalog' / 'frontier.png').as_posix()}' width='700'></div>
<div><ul><li><b>{search['n_runs']} policies</b> scored with the toolkit (time resolution, k, suppression order, age bands…)</li>
<li>Pareto front drawn; chosen point marked; baselines marked</li>
<li>The one task we lose: the hour-of-day curve. Keeping the hour makes almost every row unique on (municipality, time); suppression then destroys the municipality mix.</li></ul></div></div>""", "measurable trade-offs"),
              "It does not stop at one setting: 81 policies scored, the frontier drawn. We lose one task, the hour-of-day curve, and the chart shows why."))
    s.append(("08_text", slide("Free text: Finnish and Swedish notes and chats", f"""
<p style='font-size:23px'><span class='red'>Konsultoitu ortopedian päivystäjää (Katja Repo).</span> → Konsultoitu ortopedian päivystäjää (<span class='grn'>[PERSON]</span>).<br>
<span class='red'>personbeteckningen är 070889 streck 965 2</span> → personbeteckningen är <span class='grn'>[NATIONAL_ID]</span><br>
<span class='red'>numero on nolla neljä viisi, neljä kahdeksan kaksi</span> → numero on <span class='grn'>[PHONE]</span></p>
<div class='tiles'><div class='tile'><div class='v g'>{clin['exact']['micro']['f2']:.2f}</div><div class='l'>F2 on 30 103 clinical notes</div></div>
<div class='tile'><div class='v g'>{cs['exact']['micro']['f2']:.2f}</div><div class='l'>F2 on 26 000 chat turns</div></div>
<div class='tile'><div class='v g'>{clin['leakage']['identifying_leakage_rate']:.2%}</div><div class='l'>identifying values leaking (notes)</div></div></div>
<p>Validators with check digits, spoken numbers, spelled e-mails, cue words, public gazetteers, conversation context — no model training, fully explainable.</p>""", "the hardest part"),
              "Free text is the hardest part: Finnish and Swedish notes and chats, with spoken numbers. "
              f"The redactor reaches F2 {clin['exact']['micro']['f2']:.2f} on clinical notes and {cs['exact']['micro']['f2']:.2f} on chats, leaking {clin['leakage']['identifying_leakage_rate']:.2%} of identifying values."))
    s.append(("09_honest", slide("What the report says remains", """
<ul><li>Privacy is measured against the attacker files we were given — a stronger attacker can do better. Evidence, not proof.</li>
<li>Utility given up is listed, not hidden: the hour-of-day curve in this scenario.</li>
<li>Detector rules were tuned on three datasets; the honest numbers are the six held-out ones.</li>
<li>Pseudonyms are keyed: whoever holds the key can re-identify. The demo key must live in a vault.</li>
<li>Everything reproduces with one command; outputs go to the organisers' hidden-attacker check.</li></ul>""", "honesty"),
              "Every number is measured, and the report says what remains: attacks we did not model, utility we gave up, a demo key that belongs in a vault."))
    s.append(("10_close",
              f"<!doctype html><html><head><meta charset='utf-8'><style>{CSS}</style></head><body><div class='s' style='justify-content:center'>"
              "<h1>PETs Transformer</h1><p class='big' style='font-size:40px;color:#7fb3ff'>Protect the data, keep its value.</p>"
              "<p>detect · select · transform · demonstrate — one command, every number measured</p>"
              "<p style='color:#9fb0c3'>Built in 48 hours with Claude Code under the author's direction · evaluation: pets-eval (Since AI ry) · all data synthetic</p>"
              "<div class='foot'><span>team Cowork · Mert Yünlü</span><span>Since AI Hackathon 2026 · Turku</span></div></div></body></html>",
              "One command reproduces everything. PETs Transformer: protect the data, keep its value."))
    return s


def render_png(html_path: Path, png_path: Path) -> None:
    subprocess.run([EDGE, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--window-size=1280,720",
                    f"--screenshot={png_path}", html_path.as_uri()], check=False, capture_output=True, timeout=60)


async def tts(text: str, path: Path) -> None:
    import edge_tts
    await edge_tts.Communicate(text, VOICE, rate="+8%").save(str(path))


def duration(path: Path) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                       capture_output=True, text=True)
    return float(r.stdout.strip())


def srt_time(t: float) -> str:
    ms = int(round(t * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def main() -> None:
    if EDGE is None:
        sys.exit("Microsoft Edge not found for headless rendering")
    SL.mkdir(parents=True, exist_ok=True)
    slides = build_slides()
    segs, t, srt, n = [], 0.0, [], 0
    for sid, html_s, narration in slides:
        hp, pp, mp = SL / f"{sid}.html", SL / f"{sid}.png", SL / f"{sid}.mp3"
        hp.write_text(html_s, encoding="utf-8", newline="\n")
        render_png(hp, pp)
        asyncio.run(tts(narration, mp))
        d = duration(mp) + 0.3
        # captions: split narration into sentences, proportional timing
        sents = [x.strip() for x in re.split(r"(?<=[.!?])\s+", narration) if x.strip()]
        total = sum(len(x) for x in sents) or 1
        t0 = t
        for x in sents:
            dur = d * len(x) / total
            n += 1
            srt.append(f"{n}\n{srt_time(t0)} --> {srt_time(t0 + dur - 0.05)}\n{x}\n")
            t0 += dur
        seg = SL / f"{sid}.mp4"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-framerate", "30", "-i", str(pp), "-i", str(mp),
                        "-c:v", "libx264", "-tune", "stillimage", "-pix_fmt", "yuv420p", "-r", "30", "-c:a", "aac", "-ar", "44100", "-ac", "2",
                        "-t", f"{d:.2f}", "-shortest", str(seg)], check=True)
        segs.append(seg)
        t += d
        print(f"{sid}: {d:.1f}s")
    (OUT / "captions.srt").write_text("\n".join(srt), encoding="utf-8", newline="\n")
    (OUT / "concat.txt").write_text("".join(f"file '{p.as_posix()}'\n" for p in segs), encoding="utf-8", newline="\n")
    raw = OUT / "demo_nocap.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(OUT / "concat.txt"), "-c", "copy", str(raw)], check=True)
    srt_path = (OUT / "captions.srt").as_posix().replace(":", "\\:")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(raw), "-vf",
                    f"subtitles='{srt_path}':force_style='FontName=Segoe UI,FontSize=19,PrimaryColour=&H00FFFFFF,OutlineColour=&H80000000,BorderStyle=4,BackColour=&H80000000,MarginV=28'",
                    "-c:v", "libx264", "-crf", "20", "-preset", "medium", "-pix_fmt", "yuv420p", "-c:a", "copy", str(OUT / "demo.mp4")], check=True)
    print(f"total {t:.1f}s -> {OUT / 'demo.mp4'}")


if __name__ == "__main__":
    main()
