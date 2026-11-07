"""ElevenLabs Scribe speech-to-text for the voice benchmark: the same 9 recordings as tools/whisper_stt.py (4 single
turns, 5 grouped recordings made with ElevenLabs text-to-speech) are sent to Scribe (scribe_v1) with word timestamps,
grouped recordings are split into turns with the same similarity-guided method, and the result goes to
outputs/voice_scribe/ (transcripts.json, recording_transcripts.json, stt_meta.json, plus a copy of sample.csv) for

    python run.py voice --offline --subdir voice_scribe      # scores the Scribe transcripts

Needs ELEVENLABS_API_KEY in .env. Run with the project venv: python tools/scribe_stt.py
"""
from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from pets_transformer.voice import STT_MODEL, api_key, stt  # noqa: E402
from whisper_stt import split_words  # noqa: E402

VO = ROOT / "outputs" / "voice"
AUDIO = VO / "audio"
OUT = ROOT / "outputs" / "voice_scribe"


def words_of(res: dict) -> list[dict]:
    return [{"text": w["text"].strip(), "start": float(w.get("start") or 0.0), "end": float(w.get("end") or 0.0)}
            for w in res.get("words", []) if w.get("type", "word") == "word" and w.get("text", "").strip()]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    shutil.copy(VO / "sample.csv", OUT / "sample.csv")
    sample = pd.read_csv(VO / "sample.csv", dtype=str).fillna("")
    lang = dict(zip(sample.turn_id, sample.language))
    text = dict(zip(sample.turn_id, sample.text))
    batches = json.loads((VO / "mcp_batches.json").read_text(encoding="utf-8"))
    key = api_key()
    t0 = time.time()
    transcripts: dict[str, str] = {}
    recording: dict[str, str] = {}
    meta: dict = {"stt": "ElevenLabs Scribe", "model": STT_MODEL, "device": "api", "recordings": {}}
    for tid in batches["singles"]:
        p = AUDIO / f"{tid}.mp3"
        if not p.exists():
            continue
        res = stt(key, p, lang[tid], {"timestamps_granularity": "word"})
        transcripts[tid] = res.get("text", "").strip()
        recording[tid] = transcripts[tid]
        meta["recordings"][tid] = {"turns": [tid], "words": len(words_of(res)), "language_code": res.get("language_code"),
                                   "language_probability": res.get("language_probability")}
        print(f"{tid} ({lang[tid]}): {transcripts[tid]}")
    for b in batches["batches"]:
        p = AUDIO / f"{b['id']}.mp3"
        if not p.exists():
            continue
        res = stt(key, p, b["lang"], {"timestamps_granularity": "word"})
        words = words_of(res)
        full = res.get("text", "").strip()
        parts, how = split_words(words, len(b["turn_ids"]), [text[t] for t in b["turn_ids"]])
        rec = {"turns": b["turn_ids"], "words": len(words), "transcript": full, "split": parts is not None, "split_method": how,
               "language_code": res.get("language_code"), "language_probability": res.get("language_probability")}
        for tid in b["turn_ids"]:
            recording[tid] = full
        if parts is None:
            print(f"{b['id']}: could not split into {len(b['turn_ids'])} turns ({how}); the whole transcript is used for each turn")
            for tid in b["turn_ids"]:
                transcripts[tid] = full
        else:
            for tid, seg in zip(b["turn_ids"], parts):
                transcripts[tid] = " ".join(w["text"] for w in seg)
                print(f"{tid} ({b['lang']}): {transcripts[tid]}")
        meta["recordings"][b["id"]] = rec
    meta["seconds"] = round(time.time() - t0, 1)
    (OUT / "transcripts.json").write_text(json.dumps(transcripts, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n")
    (OUT / "recording_transcripts.json").write_text(json.dumps(recording, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n")
    (OUT / "stt_meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n")
    print(f"{len(transcripts)} transcripts, Scribe {STT_MODEL}, {meta['seconds']}s -> {OUT / 'transcripts.json'}")


if __name__ == "__main__":
    sys.exit(main())
