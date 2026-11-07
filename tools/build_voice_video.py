"""Voice-privacy demo clip (about 75 s): the real ElevenLabs audio of synthetic customer turns plays while the slide shows
the original text, the speech-to-text transcript and the redacted transcript; then the scoreboard.

Inputs: outputs/voice/results.csv, outputs/voice/scores.json, outputs/voice/audio/<turn_id>.mp3 (from `python run.py voice`,
or the connector + tools/whisper_stt.py route). Narration uses ElevenLabs text-to-speech when ELEVENLABS_API_KEY is set,
otherwise edge-tts.

    python tools/build_voice_video.py          # -> outputs/video/voice_demo.mp4
"""
from __future__ import annotations

import asyncio
import csv
import html
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_video import CSS, EDGE, OUT, duration, render_png, srt_time, tts as edge_narration  # noqa: E402

VO = Path(__file__).resolve().parents[1] / "outputs" / "voice"
VD = OUT / "voice"
FOOT = ""  # set in main() once the speech-to-text engine is known (faster-whisper large-v3 or Scribe)
EXTRA = """
.row{display:flex;gap:18px;margin:10px 0}.col{flex:1;background:#1a2230;border:1px solid #2b3647;border-radius:12px;padding:12px 16px}
.lbl{color:#7fb3ff;font-size:17px;text-transform:uppercase;letter-spacing:.5px;margin-bottom:6px}.txt{font-size:22px;line-height:1.35;color:#e9eef3}
.hl{background:#3a1d1d;border-radius:5px;padding:0 5px;color:#ffb4b4}.ok{background:#173a2a;border-radius:5px;padding:0 5px;color:#9be6b6}
.big2{font-size:52px;font-weight:700}.sub{color:#9fb0c3;font-size:19px}
.cmp h2{margin-bottom:10px}.cmp .tiles{gap:16px;margin:4px 0 10px}.cmp .tile{padding:10px 12px}.cmp .tile .v{font-size:40px}
.cmp .tile .l{font-size:15px;line-height:1.25}.cmp table{font-size:15px}.cmp th,.cmp td{padding:3px 10px}
"""
SHOW = ["T0024085", "T0020551", "T0075445", "T0018688"]  # fi national id, en national id, fi IBAN, sv e-mail (fallback: first rows)


def foot(stt_name: str) -> str:
    return (f"<div class='foot'><span>PETs Transformer · voice channel · ElevenLabs text-to-speech + {html.escape(stt_name)}</span>"
            "<span>team Cowork · Since AI Hackathon 2026</span></div>")


def page(title: str, body: str, tag: str, cls: str = "") -> str:
    return (f"<!doctype html><html><head><meta charset='utf-8'><style>{CSS}{EXTRA}</style></head><body><div class='s {cls}'>"
            f"<div class=tag>{tag}</div><h2>{title}</h2>{body}{FOOT}</div></body></html>")


def mark(text: str, values: list[str], cls: str) -> str:
    out = html.escape(text)
    for v in sorted(set(values), key=len, reverse=True):
        if v:
            out = re.sub(re.escape(html.escape(v)), f"<span class='{cls}'>{html.escape(v)}</span>", out, flags=re.I)
    return out


def main() -> None:
    if EDGE is None:
        sys.exit("Microsoft Edge not found")
    VD.mkdir(parents=True, exist_ok=True)
    for old in VD.glob("*"):
        old.unlink()
    rows = {r["turn_id"]: r for r in csv.DictReader((VO / "results.csv").open(encoding="utf-8"))}
    sc = json.loads((VO / "scores.json").read_text(encoding="utf-8"))
    t = sc["total"]
    stt_name = f"{sc.get('stt', {}).get('stt', 'speech-to-text')} {sc.get('stt', {}).get('model', '')}".strip()
    global FOOT
    FOOT = foot(stt_name)
    picks =[i for i in SHOW if i in rows and (VO / "audio" / f"{i}.mp3").exists()] or list(rows)[:4]
    key = None
    try:
        from pets_transformer.voice import api_key, list_voices, tts as el_tts
        key = api_key()
        voice_id = next((v["voice_id"] for v in list_voices(key) if (v.get("labels") or {}).get("language", "").startswith("en")), None)
    except Exception:  # noqa: BLE001
        voice_id = None

    def narrate(text: str, path: Path) -> None:
        if key and voice_id:
            el_tts(key, text, voice_id, path, "en")
        else:
            asyncio.run(edge_narration(text, path))

    slides: list[tuple[str, str, Path | None, str]] = []  # id, html, audio, caption
    slides.append(("v1_title", f"<!doctype html><html><head><meta charset='utf-8'><style>{CSS}{EXTRA}</style></head><body><div class='s' style='justify-content:center'>"
                   "<div class='tag'>PETs Transformer · voice channel</div><h1>Does personal data survive a phone call?</h1>"
                   f"<p class='big' style='font-size:34px;color:#7fb3ff'>Synthetic customer turns → ElevenLabs speech → {html.escape(stt_name)} transcript → PETs Transformer redaction</p>"
                   f"<p>{sc['n_turns']} turns in Finnish, Swedish and English · {t['values']} identifiers read out loud · only synthetic text and audio</p>{FOOT}</div></body></html>", None,
                   "Contact centres record calls. Does personal data survive the trip from speech to transcript, and does the redactor still catch it? "
                   "We let ElevenLabs voices read synthetic customer turns aloud, ran a speech recogniser over the audio, and redacted the transcripts."))
    for i, tid in enumerate(picks, 1):
        r = rows[tid]
        golds = [g.split("=", 1)[1] for g in r["gold"].split("; ") if "=" in g]
        body = (f"<div class='row'><div class='col'><div class='lbl'>original turn ({r['language']})</div><div class='txt'>{mark(r['original'], golds, 'hl')}</div></div></div>"
                f"<div class='row'><div class='col'><div class='lbl'>{html.escape(stt_name)} transcript of the ElevenLabs audio</div><div class='txt'>{mark(r['transcript'], golds, 'hl')}</div></div>"
                f"<div class='col'><div class='lbl'>after PETs Transformer redaction</div><div class='txt'>{mark(r['redacted_transcript'], [], 'hl').replace('[', chr(1)).replace(']', chr(2))}</div></div></div>"
                f"<p class='sub'>identifiers in this turn: {html.escape(r['gold'])} · came back out of speech-to-text: {r['survived_stt']} · left after redaction: {r['leaked_after_redaction']}</p>")
        body = re.sub(chr(1) + r"([A-Z_]+)" + chr(2), r"<span class='ok'>[\1]</span>", body)
        slides.append((f"v{i + 1}_{tid}", page("Listen: the customer reads out an identifier", body, f"example {i} of {len(picks)}"),
                       VO / "audio" / f"{tid}.mp3", ""))
    per = "".join(f"<tr><td>{lab}</td><td>{d['values']}</td><td>{d['survived_stt']}</td><td class='y'>{d['leaked_raw_redaction']}</td>"
                  f"<td class='{'g' if d['leaked_after_redaction'] == 0 else 'y'}'>{d['leaked_after_redaction']}</td><td>{d['text_channel_leaked']}</td></tr>"
                  for lab, d in sc["per_label"].items())
    slides.append(("v9_scores", page("Measured on the whole sample", f"""
<div class='tiles'><div class='tile'><div class='v b'>{t['survival_rate']:.0%}</div><div class='l'>identifiers that came back out of speech-to-text<br>({t['survived_stt']} of {t['values']})</div></div>
<div class='tile'><div class='v y'>{t['leak_rate_voice_without_adapter']:.1%}</div><div class='l'>left after redacting the raw transcript<br>({t['leaked_raw_redaction']} of {t['values']})</div></div>
<div class='tile'><div class='v g'>{t['leak_rate_voice']:.1%}</div><div class='l'>left with the voice adapter (dashes, digit groups, spelled letters)<br>({t['leaked_after_redaction']} of {t['values']})</div></div>
<div class='tile'><div class='v g'>{t['leak_rate_text']:.1%}</div><div class='l'>same turns, text channel<br>({t['text_channel_leaked']} of {t['values']})</div></div></div>
<table><tr><th>identifier</th><th>n</th><th>came back out of STT</th><th>left, raw</th><th>left, adapter</th><th>left, text channel</th></tr>{per}</table>""", "results", "cmp"), None,
                   f"Across the sample, {t['survived_stt']} of {t['values']} identifiers came back out of speech-to-text. Redacting the raw transcript left "
                   f"{t['leaked_raw_redaction']}: recognisers write numbers as groups with dashes and spelled letters. A small voice adapter normalises them, "
                   f"and {t['leaked_after_redaction']} remained, the same as the text channel."))
    slides.append(("v10_close", f"<!doctype html><html><head><meta charset='utf-8'><style>{CSS}{EXTRA}</style></head><body><div class='s' style='justify-content:center'>"
                   "<h1>Voice is a data channel too</h1><p>ElevenLabs multilingual voices turn the synthetic chat corpus into a measurable voice benchmark; "
                   "the same redactor that scores F2 0.97 on clinical notes is checked on what people actually say, and the gaps it found are fixed in a voice adapter.</p>"
                   "<p style='color:#9fb0c3'>python run.py voice · audio, transcripts and scores in voice_benchmark/ · code: github.com/latentcore/pets-transformer · all data synthetic</p>"
                   f"{FOOT}</div></body></html>", None, "Voice is a data channel too. The code, the audio and every transcript are in the repository."))

    segs, t0, srt, n = [], 0.0, [], 0
    for sid, html_s, audio, caption in slides:
        hp, pp = VD / f"{sid}.html", VD / f"{sid}.png"
        hp.write_text(html_s, encoding="utf-8", newline="\n")
        render_png(hp, pp)
        if audio is None:
            audio = VD / f"{sid}.mp3"
            narrate(caption, audio)
        d = duration(audio) + 0.6
        if caption:
            sents = [x.strip() for x in re.split(r"(?<=[.!?:])\s+", caption) if x.strip()]
            total = sum(len(x) for x in sents) or 1
            tt = t0
            for x in sents:
                dur = d * len(x) / total
                n += 1
                srt.append(f"{n}\n{srt_time(tt)} --> {srt_time(tt + dur - 0.05)}\n{x}\n")
                tt += dur
        seg = VD / f"{sid}.mp4"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-framerate", "30", "-i", str(pp), "-i", str(audio),
                        "-c:v", "libx264", "-tune", "stillimage", "-pix_fmt", "yuv420p", "-r", "30", "-c:a", "aac", "-ar", "44100", "-ac", "2",
                        "-t", f"{d:.2f}", "-shortest", str(seg)], check=True)
        segs.append(seg)
        t0 += d
        print(f"{sid}: {d:.1f}s")
    (VD / "captions.srt").write_text("\n".join(srt), encoding="utf-8", newline="\n")
    (VD / "concat.txt").write_text("".join(f"file '{p.as_posix()}'\n" for p in segs), encoding="utf-8", newline="\n")
    raw = VD / "voice_nocap.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(VD / "concat.txt"), "-c", "copy", str(raw)], check=True)
    srt_path = (VD / "captions.srt").as_posix().replace(":", "\\:")
    vf = ("pad=1280:840:0:0:color=0x0f1419,"
          f"subtitles='{srt_path}':force_style='FontName=Segoe UI,FontSize=12,PrimaryColour=&H00FFFFFF,BorderStyle=1,Outline=0,Shadow=0,Alignment=2,MarginV=6,MarginL=20,MarginR=20'")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(raw), "-vf", vf, "-c:v", "libx264", "-crf", "20", "-preset", "medium",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", str(OUT / "voice_demo.mp4")], check=True)
    print(f"total {t0:.1f}s -> {OUT / 'voice_demo.mp4'}")


if __name__ == "__main__":
    main()
