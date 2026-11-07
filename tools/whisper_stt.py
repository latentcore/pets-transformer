"""Local speech-to-text for the voice benchmark (open-source Whisper via faster-whisper), used when no ElevenLabs API key
is available for Scribe. Reads outputs/voice/audio/*.mp3 (single turns T*.mp3 and grouped recordings B*.mp3 made with
ElevenLabs text-to-speech), transcribes with word timestamps, splits grouped recordings at the 1.5 s pauses between
turns, cuts one clip per turn, and writes outputs/voice/transcripts.json for `python run.py voice --offline`.

Why not the ElevenLabs connector's transcription node? Wired to its own speech node it returns the input text by
alignment (the <break/> tags come back verbatim, 0 credits), which is not a recognition pass and would make the
measurement meaningless. With ELEVENLABS_API_KEY set, `python run.py voice` uses Scribe through the API instead.

    python tools/whisper_stt.py --model large-v3        # needs: faster-whisper, torch (CUDA optional), ffmpeg
"""
from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
VO = ROOT / "outputs" / "voice"
AUDIO = VO / "audio"
# digit words in the three languages, used only to compare a transcript segment with the turn it should belong to
DIGITS = {"nolla": "0", "yksi": "1", "kaksi": "2", "kolme": "3", "neljä": "4", "viisi": "5", "kuusi": "6", "seitsemän": "7",
          "kahdeksan": "8", "yhdeksän": "9", "noll": "0", "ett": "1", "en": "1", "två": "2", "tre": "3", "fyra": "4", "fem": "5",
          "sex": "6", "sju": "7", "åtta": "8", "nio": "9", "zero": "0", "oh": "0", "one": "1", "two": "2", "three": "3",
          "four": "4", "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9"}


def _norm(text: str) -> str:
    return "".join(DIGITS.get(w, w) for w in re.findall(r"[a-zåäöéü]+|\d+", text.lower()))


def load_audio(path: Path) -> np.ndarray:
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "f32le", "-ac", "1", "-ar", "16000", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype=np.float32)


def model(name: str):
    try:
        import torch  # the CUDA runtime DLLs ship inside torch/lib on Windows
        libs = os.path.join(os.path.dirname(torch.__file__), "lib")
        if os.path.isdir(libs):
            os.add_dll_directory(libs)
            os.environ["PATH"] = libs + os.pathsep + os.environ["PATH"]
        cuda = torch.cuda.is_available()
    except Exception:  # noqa: BLE001
        cuda = False
    from faster_whisper import WhisperModel
    if cuda:
        try:
            return WhisperModel(name, device="cuda", compute_type="float16"), "cuda"
        except Exception as e:  # noqa: BLE001
            print("cuda failed, falling back to cpu:", str(e)[:120])
    return WhisperModel(name, device="cpu", compute_type="int8"), "cpu"


def transcribe(m, audio: np.ndarray, lang: str) -> list[dict]:
    segs, _ = m.transcribe(audio, language=lang, word_timestamps=True, beam_size=5, vad_filter=False)
    words = []
    for s in segs:
        for w in s.words or []:
            words.append({"text": w.word.strip(), "start": float(w.start), "end": float(w.end)})
    return words


def _cut(words: list[dict], cut: tuple[int, ...] | list[int]) -> list[list[dict]]:
    out, prev = [], 0
    for i in cut:
        out.append(words[prev:i])
        prev = i
    out.append(words[prev:])
    return out


def split_words(words: list[dict], n: int, originals: list[str] | None = None) -> tuple[list[list[dict]], str] | tuple[None, str]:
    """Split a grouped recording's words into n per-turn segments at pauses. Only for display and the per-turn clips:
    the leakage scores are computed on the whole recording's transcript, never on these segments.

    With the turns' original texts given, the cut points are chosen among the 18 longest pauses (>= 0.15 s) and the
    recogniser's own sentence ends so that the sum over turns of the similarity between each segment and the turn it
    should hold is largest (difflib ratio on normalised text, digit words -> digits; dynamic programme over the
    candidate boundaries). A recogniser's own sentence breaks are often longer than the 1.5 s silence between turns,
    so the longest pauses alone put a turn's tail on the next turn. Without originals: the n-1 longest pauses
    (each >= 0.6 s)."""
    if n <= 1:
        return [words], "single"
    if len(words) < n:
        return None, "too few words"
    gaps = sorted(((words[i]["start"] - words[i - 1]["end"], i) for i in range(1, len(words))), reverse=True)
    if originals and len(originals) == n:
        cands = {i for g, i in gaps[:18] if g >= 0.15} | {i for i in range(1, len(words)) if words[i - 1]["text"].endswith((".", "?", "!"))}
        pos = [0] + sorted(cands) + [len(words)]
        m = len(pos) - 1
        if m >= n:
            targets = [_norm(o) for o in originals]
            cache: dict[tuple[int, int, int], float] = {}

            def sim(a: int, b: int, k: int) -> float:
                if (a, b, k) not in cache:
                    seg = _norm(" ".join(w["text"] for w in words[pos[a]:pos[b]]))
                    cache[(a, b, k)] = difflib.SequenceMatcher(None, seg, targets[k]).ratio()
                return cache[(a, b, k)]

            best: dict[tuple[int, int], tuple[float, int]] = {(0, 0): (0.0, -1)}  # (turns placed, end index) -> (score, start index)
            for k in range(1, n + 1):
                for j in range(k, m + 1):
                    options = [(best[(k - 1, i)][0] + sim(i, j, k - 1), i) for i in range(k - 1, j) if (k - 1, i) in best]
                    if options:
                        best[(k, j)] = max(options)
            cut, j = [], m
            for k in range(n, 0, -1):
                i = best[(k, j)][1]
                if k > 1:
                    cut.append(pos[i])
                j = i
            return _cut(words, sorted(cut)), "similarity"
    if gaps[n - 2][0] < 0.6:  # the (n-1)th largest pause is too short to be a turn boundary
        return None, "pauses too short"
    return _cut(words, sorted(i for g, i in gaps[: n - 1])), "longest pauses"


def cut_clip(src: Path, dst: Path, start: float, end: float) -> None:
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-ss", f"{max(0.0, start - 0.25):.3f}", "-to", f"{end + 0.35:.3f}",
                    "-c:a", "libmp3lame", "-q:a", "3", str(dst)], check=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="large-v3")
    a = ap.parse_args()
    sample = pd.read_csv(VO / "sample.csv", dtype=str).fillna("")
    lang = dict(zip(sample.turn_id, sample.language))
    text = dict(zip(sample.turn_id, sample.text))
    batches = json.loads((VO / "mcp_batches.json").read_text(encoding="utf-8"))
    m, device = model(a.model)
    t0 = time.time()
    transcripts: dict[str, str] = {}
    recording: dict[str, str] = {}  # turn -> transcript of the whole recording it was spoken in (used for scoring)
    meta = {"stt": "faster-whisper", "model": a.model, "device": device, "recordings": {}}
    for tid in batches["singles"]:
        p = AUDIO / f"{tid}.mp3"
        if not p.exists():
            continue
        words = transcribe(m, load_audio(p), lang[tid])
        transcripts[tid] = " ".join(w["text"] for w in words)
        recording[tid] = transcripts[tid]
        meta["recordings"][tid] = {"turns": [tid], "words": len(words)}
        print(f"{tid} ({lang[tid]}): {transcripts[tid]}")
    for b in batches["batches"]:
        p = AUDIO / f"{b['id']}.mp3"
        if not p.exists():
            continue
        words = transcribe(m, load_audio(p), b["lang"])
        full = " ".join(w["text"] for w in words)
        parts, how = split_words(words, len(b["turn_ids"]), [text[t] for t in b["turn_ids"]])
        rec = {"turns": b["turn_ids"], "words": len(words), "transcript": full, "split": parts is not None, "split_method": how}
        for tid in b["turn_ids"]:
            recording[tid] = full
        if parts is None:
            print(f"{b['id']}: pauses did not separate {len(b['turn_ids'])} turns; the whole transcript is used for each turn")
            for tid in b["turn_ids"]:
                transcripts[tid] = full
        else:
            for tid, seg in zip(b["turn_ids"], parts):
                transcripts[tid] = " ".join(w["text"] for w in seg)
                if seg:
                    cut_clip(p, AUDIO / f"{tid}.mp3", seg[0]["start"], seg[-1]["end"])
                print(f"{tid} ({b['lang']}): {transcripts[tid]}")
        meta["recordings"][b["id"]] = rec
    meta["seconds"] = round(time.time() - t0, 1)
    (VO / "transcripts.json").write_text(json.dumps(transcripts, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n")
    (VO / "recording_transcripts.json").write_text(json.dumps(recording, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n")
    (VO / "stt_meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n")
    print(f"{len(transcripts)} transcripts, {device}, {meta['seconds']}s -> {VO / 'transcripts.json'}")


if __name__ == "__main__":
    sys.exit(main())
