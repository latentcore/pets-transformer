"""Voice channel privacy: does personal data survive the audio round trip, and does the redactor still catch it?

Contact centres record calls. Before a recording or its transcript can be shared (quality audits, intent models, an
external analytics vendor: scenarios CS-S1..S3), the identifiers a customer *says* must be found and removed. This module
measures that on synthetic customer-service turns:

    synthetic turn text  --ElevenLabs text-to-speech-->  audio (mp3)  --ElevenLabs speech-to-text (Scribe)-->  transcript
                                                                        --PETs Transformer redactor-->  redacted transcript

For every gold identifier in the original turn (national id, phone, IBAN, card, account number, e-mail, address, name,
secret, ...) it checks (1) whether the value came back out of the speech-to-text step at all (digits read out in words,
"streck", spaces: all normalised) and (2) whether it is still present after redaction. The honest number for a voice
channel is (2): identifying values that would leave the building inside a redacted transcript.

Only synthetic text goes to ElevenLabs (the Proving Ground package, Since AI ry, CC BY 4.0). The API key is read from
`.env` (ELEVENLABS_API_KEY) and never written anywhere.

    python run.py voice --select-only           # choose the sample, score the text channel, no API calls
    python run.py voice                          # TTS -> STT -> redact -> scores (needs ELEVENLABS_API_KEY)
    python run.py voice --reuse                  # recompute transcripts/scores from saved audio (STT only)
    python run.py voice --offline                # recompute scores from saved transcripts (no API calls)
"""
from __future__ import annotations

import csv
import json
import os
import re
import shutil
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

import pandas as pd

from pets_eval import io

from . import redact as Rd
from .config import outputs_dir, package_root

API = "https://api.elevenlabs.io"
TTS_MODEL = "eleven_multilingual_v2"
STT_MODEL = "scribe_v1"
# identifiers whose survival matters (span labels of metadata/taxonomy.md); LOCATION/ORG/HEALTH/URL are context, not keys
IDENTIFYING = ["NATIONAL_ID", "PHONE", "IBAN", "PAYMENT_CARD", "ACCOUNT_NUMBER", "EMAIL", "ADDRESS", "PERSON", "SECRET",
               "DEVICE_ID", "DATE", "AGE", "LICENSE_PLATE", "IP_ADDRESS", "USERNAME", "PROPERTY_ID"]
# how many turns per label we try to include (a turn often carries several labels)
TARGET = {"PAYMENT_CARD": 3, "SECRET": 2, "NATIONAL_ID": 6, "PHONE": 5, "IBAN": 4, "ACCOUNT_NUMBER": 3, "ADDRESS": 3,
          "EMAIL": 3, "DATE": 2, "DEVICE_ID": 1, "AGE": 1}
LANG_SHARE = {"fi": 0.6, "sv": 0.2, "en": 0.2}

WORD2DIGIT: dict[str, str] = {}
for _lang, _words in Rd.DIGIT_WORDS.items():
    for _i, _w in enumerate([w for w in _words if w != "oh"]):
        WORD2DIGIT[_w] = str(_i)
WORD2DIGIT["oh"] = "0"
SEP_WORDS = set(Rd.SEP_WORDS) | {"välilyönti", "mellanslag", "space", "piste", "punkt", "dot", "ät", "at", "snabel", "snabela",
                                 "understreck", "alaviiva", "underscore", "miukumauku"}
# a mail provider's domain is shared by thousands of customers and identifies nobody; these are the synthetic corpus's providers
GENERIC = {"example", "gmail", "hotmail", "outlook", "aurapost", "postilaatikko", "kotiposti", "nettiposti", "inbox", "webmail",
           "nordmail", "mailbox", "sahkoposti", "sähköposti", "posti", "mail", "com", "net", "org"}
# Finnish letter names as a recogniser writes them after a digit run ("958 äm" = 958M, the check character of a HETU)
LETTER_NAMES = {"aa": "A", "bee": "B", "see": "C", "dee": "D", "ee": "E", "äf": "F", "gee": "G", "hoo": "H", "ii": "I", "jii": "J",
                "koo": "K", "äl": "L", "äm": "M", "än": "N", "oo": "O", "pee": "P", "kuu": "Q", "är": "R", "äs": "S", "tee": "T",
                "uu": "U", "vee": "V", "yy": "Y", "ääm": "M", "ään": "N", "ääs": "S"}
STREET_SUFFIX = r"(?:katu|tie|kuja|polku|ranta|rinne|väylä|kaari|raitti|puistikko|gatan|vägen|gränd|stigen)"
NAME_LIKE = {"PERSON", "ADDRESS", "ORG", "LOCATION"}
_TOKEN = re.compile(r"[a-zåäöéü]+|\d+", re.I)


def _load_dotenv() -> None:
    f = Path(__file__).resolve().parents[1] / ".env"
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def api_key() -> str:
    _load_dotenv()
    k = os.environ.get("ELEVENLABS_API_KEY", "").strip()
    if not k:
        raise RuntimeError("ELEVENLABS_API_KEY not set (put it in .env as ELEVENLABS_API_KEY=...)")
    return k


# ----------------------------------------------------------------------------------------------- normalisation
def normalise(text: str) -> tuple[str, str]:
    """(alnum, digits): letters and digits only, lower-case, digit words -> digits, separator words dropped."""
    out = []
    for tok in _TOKEN.findall(text.lower()):
        if tok in SEP_WORDS:
            continue
        out.append(WORD2DIGIT.get(tok, tok))
    alnum = "".join(out)
    return alnum, "".join(c for c in alnum if c.isdigit())


def present(value: str, text: str, label: str = "") -> bool:
    """Is the identifier value (gold span text) still present in text, after normalisation of both sides?"""
    va, vd = normalise(value)
    ta, td = normalise(text)
    if not va or not ta:
        return False
    if va in ta:
        return True
    if len(vd) >= 5 and vd in td:  # a number read out differently (groups, words, dashes)
        return True
    if label in NAME_LIKE or label in ("EMAIL", "USERNAME"):
        # a name or a spelled-out address: any distinctive part of 5+ letters surviving counts (separator and generic
        # words such as "understreck" or "example" do not identify anyone)
        parts = [p for p in _TOKEN.findall(value.lower()) if len(p) >= 5 and p not in SEP_WORDS and p not in GENERIC and not p.isdigit()]
        return any(p in ta for p in parts)
    return False


# ----------------------------------------------------------------------------------------------- sample
def select_sample(n_max: int = 28, max_chars: int = 220, total_chars: int = 3600, seed: int = 7) -> pd.DataFrame:
    root = package_root()
    turns = io.read_table(root / "data" / "customer_service" / "turns.csv")
    conv = io.read_table(root / "data" / "customer_service" / "conversations.csv")[["conversation_id", "channel", "language", "split"]]
    gold = io.load_spans(io.spans_path(root, "customer_service", "turns", "text"))
    df = turns.merge(conv, on="conversation_id", how="left")
    df = df[(df.split == "train") & (df.channel == "phone") & (df.speaker == "customer")].copy()
    df["spans"] = df.turn_id.map(lambda i: gold.get(str(i), []))
    df["labels"] = df.spans.map(lambda s: sorted({x["label"] for x in s if x["label"] in IDENTIFYING}))
    df = df[(df.labels.str.len() > 0) & (df.text.str.len() <= max_chars) & (df.text.str.len() >= 40)]
    df = df.sample(frac=1.0, random_state=seed)
    # rare labels first, so that cards and secrets are not crowded out by the frequent ones
    df["rarity"] = df.labels.map(lambda ls: min(list(TARGET).index(l) if l in TARGET else 99 for l in ls))
    df = df.sort_values("rarity", kind="stable")
    quota = dict(TARGET)
    lang_cap = {l: max(1, round(n_max * s)) for l, s in LANG_SHARE.items()}
    chosen, chars = [], 0
    for _, r in df.iterrows():
        if len(chosen) >= n_max or chars + len(r.text) > total_chars:
            break
        need = [l for l in r.labels if quota.get(l, 0) > 0]
        if not need or lang_cap.get(r.language, 0) <= 0:
            continue
        for l in need:
            quota[l] -= 1
        lang_cap[r.language] -= 1
        chosen.append(r)
        chars += len(r.text)
    out = pd.DataFrame(chosen)[["turn_id", "conversation_id", "language", "speaker", "text", "spans", "labels"]]
    out["gold"] = out.apply(lambda r: [{"label": s["label"], "value": r.text[s["start"]:s["end"]]} for s in r.spans], axis=1)
    return out.reset_index(drop=True)


# ----------------------------------------------------------------------------------------------- ElevenLabs API
def _request(method: str, path: str, key: str, body: bytes | None = None, content_type: str | None = None,
             params: dict | None = None, retries: int = 5) -> tuple[int, bytes, dict]:
    url = API + path + (("?" + urllib.parse.urlencode(params)) if params else "")
    headers = {"xi-api-key": key, "Accept": "*/*"}
    if content_type:
        headers["Content-Type"] = content_type
    last = None
    for attempt in range(retries):
        rq = urllib.request.Request(url, data=body, method=method, headers=headers)
        try:
            with urllib.request.urlopen(rq, timeout=120) as r:
                return r.status, r.read(), dict(r.headers)
        except urllib.error.HTTPError as e:
            raw = e.read()
            last = (e.code, raw)
            if e.code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                time.sleep(4 * (attempt + 1))
                continue
            return e.code, raw, dict(e.headers)
        except (urllib.error.URLError, TimeoutError) as e:  # network blip (the laptop is moving between Wi-Fi networks)
            last = (0, str(e).encode())
            time.sleep(10 * (attempt + 1))
    return last[0], last[1], {}


def list_voices(key: str) -> list[dict]:
    st, raw, _ = _request("GET", "/v1/voices", key)
    if st != 200:
        raise RuntimeError(f"GET /v1/voices -> {st}: {raw[:200]!r}")
    return json.loads(raw)["voices"]


def pick_voices(key: str) -> dict[str, dict]:
    """One voice per language. Library voices for fi/sv are added to the account when possible; premade voices otherwise."""
    mine = list_voices(key)
    by_lang: dict[str, dict] = {}
    for v in mine:
        lang = (v.get("labels") or {}).get("language", "")
        by_lang.setdefault(lang, v)
    out = {}
    for lang in ("fi", "sv", "en"):
        if lang in by_lang:
            out[lang] = by_lang[lang]
            continue
        if lang != "en":
            st, raw, _ = _request("GET", "/v1/shared-voices", key, params={"language": lang, "page_size": 5, "category": "professional"})
            if st == 200:
                for sv in json.loads(raw).get("voices", []):
                    st2, raw2, _ = _request("POST", f"/v1/voices/add/{sv['public_owner_id']}/{sv['voice_id']}", key,
                                            json.dumps({"new_name": sv["name"]}).encode(), "application/json", retries=1)
                    if st2 in (200, 201):
                        out[lang] = {"voice_id": json.loads(raw2).get("voice_id", sv["voice_id"]), "name": sv["name"], "labels": {"language": lang}}
                        break
        if lang not in out:
            fallback = next((v for v in mine if (v.get("labels") or {}).get("language", "").startswith("en")), mine[0] if mine else None)
            if fallback is None:
                raise RuntimeError("no voices available in this ElevenLabs account")
            out[lang] = fallback
    return out


def tts(key: str, text: str, voice_id: str, path: Path, language: str) -> dict:
    body = {"text": text, "model_id": TTS_MODEL}
    st, raw, hdr = _request("POST", f"/v1/text-to-speech/{voice_id}", key, json.dumps(body).encode(), "application/json",
                            params={"output_format": "mp3_44100_128"})
    if st != 200:
        raise RuntimeError(f"text-to-speech -> {st}: {raw[:300]!r}")
    path.write_bytes(raw)
    return {"bytes": len(raw), "request_id": hdr.get("request-id", ""), "model": TTS_MODEL, "voice_id": voice_id}


def stt(key: str, path: Path, language: str) -> dict:
    boundary = "----pets" + uuid.uuid4().hex
    fields = {"model_id": STT_MODEL, "language_code": language, "tag_audio_events": "false", "diarize": "false"}
    parts = []
    for k, v in fields.items():
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode())
    parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{path.name}\"\r\n"
                 f"Content-Type: audio/mpeg\r\n\r\n".encode() + path.read_bytes() + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    st_, raw, _ = _request("POST", "/v1/speech-to-text", key, b"".join(parts), f"multipart/form-data; boundary={boundary}")
    if st_ != 200:
        raise RuntimeError(f"speech-to-text -> {st_}: {raw[:300]!r}")
    return json.loads(raw)


# ----------------------------------------------------------------------------------------------- scoring
def asr_adapt(text: str) -> str:
    """Generic post-processing of a speech-to-text transcript before redaction (no knowledge of the true text):
    "4242 -4220" -> "4242-4220", runs of single spoken characters "1 5 1 2 0 1" -> "151201", "snabel a" -> "snabel-a",
    "hashtag" -> "#". Recognisers write numbers read out loud in many shapes; the redactor's validators expect one."""
    t = re.sub(r"(?<=[\w])\s+-\s*(?=[\w])", " ", text)  # a dash between spoken groups is a group separator
    t = re.sub(r"\b(?:[0-9A-Za-zÅÄÖåäö][,\s]+){2,}[0-9A-Za-zÅÄÖåäö]\b",
               lambda m: re.sub(r"[,\s]+", "", m.group()) if sum(c.isdigit() for c in m.group()) >= 3 else m.group(), t)
    # groups of digits read out in chunks ("27 956 87 144 554 961", "4269 6207", "210645 926V"): one token when 8+ digits
    t = re.sub(r"\b\d+[A-Za-zÅÄÖ]?(?:[ ,]+\d+[A-Za-zÅÄÖ]?)+\b",
               lambda m: re.sub(r"[ ,]+", "", m.group()) if sum(c.isdigit() for c in m.group()) >= 8 else m.group(), t)
    # a spelled letter after a digit run ("958 äm", "906 uu") is the letter itself, and it belongs to the number
    t = re.sub(r"(?<=\d)[ ,]+(" + "|".join(sorted(LETTER_NAMES, key=len, reverse=True)) + r")\b",
               lambda m: LETTER_NAMES[m.group(1).lower()], t, flags=re.I)
    t = re.sub(r"\b(\d{6,})\s+([A-Za-zÅÄÖ])\b", r"\1\2", t)
    # a compound street name split by the recogniser ("Koivu Kuja 29" -> "Koivukuja 29")
    t = re.sub(r"\b([A-ZÅÄÖ][a-zåäö]+)\s+(" + STREET_SUFFIX + r")\b(?=\s+\d)", lambda m: m.group(1) + m.group(2).lower(), t, flags=re.I)
    t = re.sub(r"\bsnabel\s+a[vf]?\b", "snabel-a", t, flags=re.I)
    t = re.sub(r"\bhashtag\b", "#", t, flags=re.I)
    return t


COUNTS = ("values", "survived_stt", "leaked_after_redaction", "leaked_raw_redaction", "text_channel_leaked")


def score(rows: list[dict]) -> dict:
    per_label: dict[str, dict] = {}
    tot = dict.fromkeys(COUNTS, 0)
    for r in rows:
        for g in r["gold"]:
            if g["label"] not in IDENTIFYING:
                continue
            d = per_label.setdefault(g["label"], dict.fromkeys(COUNTS, 0))
            full = r.get("transcript_full") or ""
            s = present(g["value"], full, g["label"])
            l = present(g["value"], r["redacted"], g["label"]) if r.get("redacted") is not None else False
            lr = present(g["value"], r["redacted_raw"], g["label"]) if r.get("redacted_raw") is not None else False
            t = present(g["value"], r.get("text_redacted") or "", g["label"])
            for k, v in (("values", 1), ("survived_stt", int(s)), ("leaked_after_redaction", int(l)), ("leaked_raw_redaction", int(lr)),
                         ("text_channel_leaked", int(t))):
                d[k] += v
                tot[k] += v

    def rates(d):
        n = d["values"] or 1
        return {**d, "survival_rate": round(d["survived_stt"] / n, 4), "leak_rate_voice": round(d["leaked_after_redaction"] / n, 4),
                "leak_rate_voice_without_adapter": round(d["leaked_raw_redaction"] / n, 4), "leak_rate_text": round(d["text_channel_leaked"] / n, 4)}
    return {"total": rates(tot), "per_label": {k: rates(v) for k, v in sorted(per_label.items())}}


def build_rows(sample: pd.DataFrame, transcripts: dict[str, str], red: Rd.Redactor, recordings: dict[str, str] | None = None) -> list[dict]:
    """transcripts: turn -> its transcript (or the segment of a grouped recording, for display). recordings: turn -> transcript
    of the whole recording the turn was spoken in; scoring and redaction run on that, as they would on a real call."""
    rows = []
    for _, r in sample.iterrows():
        text_red, _ = red.redact(r.text)
        tr = transcripts.get(r.turn_id)
        full = (recordings or {}).get(r.turn_id, tr)
        row = {"turn_id": r.turn_id, "language": r.language, "text": r.text, "gold": r.gold, "text_redacted": text_red,
               "transcript": tr, "transcript_full": full, "transcript_adapted": None, "redacted": None, "redacted_raw": None, "spans": None}
        if full is not None:
            adapted = asr_adapt(full)
            redacted, spans = red.redact(adapted)
            redacted_raw, _ = red.redact(full)
            row.update(transcript_adapted=adapted, redacted=redacted, redacted_raw=redacted_raw, spans=spans,
                       redacted_segment=red.redact(asr_adapt(tr))[0] if tr else None)
        rows.append(row)
    return rows


def run(n: int = 28, select_only: bool = False, reuse: bool = False, offline: bool = False, max_chars: int = 220,
        total_chars: int = 3600) -> dict:
    out = outputs_dir() / "voice"
    audio = out / "audio"
    audio.mkdir(parents=True, exist_ok=True)
    bench = Path(__file__).resolve().parents[1] / "voice_benchmark"
    if (reuse or offline) and not (out / "transcripts.json").exists() and (bench / "transcripts.json").exists():
        shutil.copytree(bench, out, dirs_exist_ok=True)  # the committed artefacts: sample, audio, transcripts, scores
        print(f"copied {bench} -> {out}")
    sample_p = out / "sample.csv"
    if sample_p.exists() and (reuse or offline):
        sample = pd.read_csv(sample_p, dtype=str).fillna("")
        sample["gold"] = sample.gold.map(json.loads)
    else:
        sample = select_sample(n, max_chars, total_chars)
        s2 = sample.drop(columns=["spans"]).copy()
        s2["gold"] = s2.gold.map(lambda g: json.dumps(g, ensure_ascii=False))
        s2["labels"] = s2.labels.map(lambda l: ",".join(l))
        s2.to_csv(sample_p, index=False, encoding="utf-8")
    red = Rd.Redactor(Rd.TextGazetteers.from_root(package_root()))
    print(f"sample: {len(sample)} customer turns ({sample.text.str.len().sum()} characters), languages "
          f"{sample.language.value_counts().to_dict()}, gold identifiers "
          f"{sum(1 for g in sample.gold for x in g if x['label'] in IDENTIFYING)}")
    tr_p = out / "transcripts.json"
    transcripts: dict[str, str] = json.loads(tr_p.read_text(encoding="utf-8")) if tr_p.exists() and (reuse or offline) else {}
    meta_p = out / "api_log.json"
    meta = json.loads(meta_p.read_text(encoding="utf-8")) if meta_p.exists() else {"tts": {}, "stt": {}}
    if not select_only and not offline:
        key = api_key()
        voices = pick_voices(key)
        meta["voices"] = {l: {"voice_id": v["voice_id"], "name": v.get("name")} for l, v in voices.items()}
        print("voices:", {l: v.get("name") for l, v in voices.items()})
        for i, r in sample.iterrows():
            mp3 = audio / f"{r.turn_id}.mp3"
            if not mp3.exists() or (not reuse and r.turn_id not in meta["tts"]):
                meta["tts"][r.turn_id] = tts(key, r.text, voices[r.language]["voice_id"], mp3, r.language)
            if r.turn_id not in transcripts:
                res = stt(key, mp3, r.language)
                transcripts[r.turn_id] = res.get("text", "")
                meta["stt"][r.turn_id] = {"model": STT_MODEL, "language_code": res.get("language_code"),
                                          "language_probability": res.get("language_probability")}
                tr_p.write_text(json.dumps(transcripts, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n")
                meta_p.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n")
            print(f"  {i + 1:2d}/{len(sample)} {r.turn_id} {r.language}: {transcripts[r.turn_id][:90]}")
    rec_p, stt_meta_p = out / "recording_transcripts.json", out / "stt_meta.json"
    recordings = json.loads(rec_p.read_text(encoding="utf-8")) if rec_p.exists() and (reuse or offline) else None
    stt_info = (json.loads(stt_meta_p.read_text(encoding="utf-8")) if stt_meta_p.exists() and (reuse or offline)
                else {"stt": "ElevenLabs Scribe", "model": STT_MODEL})
    rows = build_rows(sample, transcripts, red, recordings)
    sc = score(rows)
    sc["n_turns"] = len(rows)
    sc["n_transcribed"] = sum(1 for r in rows if r["transcript"] is not None)
    sc["tts_model"] = TTS_MODEL
    sc["stt"] = {k: v for k, v in stt_info.items() if k != "recordings"}
    sc["grouped_recordings"] = recordings is not None
    (out / "scores.json").write_text(json.dumps(sc, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n")
    with (out / "results.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["turn_id", "language", "original", "transcript", "transcript_adapted", "redacted_transcript", "gold",
                    "survived_stt", "leaked_after_redaction", "leaked_without_adapter"])
        for r in rows:
            ids = [g for g in r["gold"] if g["label"] in IDENTIFYING]
            full = r["transcript_full"] or ""
            w.writerow([r["turn_id"], r["language"], r["text"], r["transcript"] or "", asr_adapt(r["transcript"] or ""),
                        r.get("redacted_segment") or "", "; ".join(f"{g['label']}={g['value']}" for g in ids),
                        sum(present(g["value"], full, g["label"]) for g in ids),
                        sum(present(g["value"], r["redacted"] or "", g["label"]) for g in ids) if r["redacted"] is not None else "",
                        sum(present(g["value"], r["redacted_raw"] or "", g["label"]) for g in ids) if r["redacted_raw"] is not None else ""])
    t = sc["total"]
    print(f"\ntext channel (original turns -> redactor): {t['text_channel_leaked']}/{t['values']} identifying values leaked "
          f"({t['leak_rate_text']:.1%})")
    if sc["n_transcribed"]:
        print(f"voice channel ({sc['stt'].get('stt')} {sc['stt'].get('model')}): {t['survived_stt']}/{t['values']} values came back out of "
              f"speech-to-text ({t['survival_rate']:.1%}); after redaction {t['leaked_after_redaction']}/{t['values']} still present "
              f"({t['leak_rate_voice']:.1%}); without the ASR adapter {t['leaked_raw_redaction']}/{t['values']} ({t['leak_rate_voice_without_adapter']:.1%})")
        for lab, d in sc["per_label"].items():
            print(f"  {lab:15s} n={d['values']:2d} survived {d['survived_stt']:2d} leaked {d['leaked_after_redaction']:2d} "
                  f"(without adapter {d['leaked_raw_redaction']:2d}, text channel {d['text_channel_leaked']:2d})")
    print(f"wrote {out}")
    return sc
