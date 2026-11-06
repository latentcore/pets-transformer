"""Optional second opinion from a language model for columns the rule-based detector is unsure about.

Backends: Ollama (local, e.g. qwen3:8b — no data leaves the laptop) or the Gemini API (GEMINI_API_KEY).
The model sees the column name, a dozen sample values, the other column names of the table and whether the
table describes people; it must answer with a privacy_role and subtype from the taxonomy plus a one-line reason.
The rule-based evidence stays in the output next to the model's reason, so every decision remains explainable.
"""
from __future__ import annotations

import json
import os
import re
import urllib.request

from pets_eval.detect import ROLES

SUBTYPES = ("PERSON_NAME, NATIONAL_ID, EMAIL, PHONE, STREET_ADDRESS, POSTCODE, MUNICIPALITY, GPS_COORDINATE, LOCATION_NAME, "
            "BIRTH_DATE, AGE, SEX, LANGUAGE, NATIONALITY, IBAN, PAYMENT_CARD, CUSTOMER_ID, ACCOUNT_ID, EMPLOYEE_ID, STUDENT_ID, "
            "PATIENT_ID, CARD_ID, DEVICE_ID, IP_ADDRESS, MAC_ADDRESS, IMEI, HOSTNAME, USERNAME, URL, USER_AGENT, LICENSE_PLATE, "
            "PROPERTY_ID, METER_ID, TIMESTAMP, DATE, DURATION, HEALTH_DATA, DIAGNOSIS_CODE, FINANCIAL_DATA, EDUCATION_DATA, "
            "SOCIAL_DATA, OCCUPATION, ORG_NAME, MEASUREMENT, CATEGORY, AGGREGATE, SECRET, FREE_TEXT, RECORD_ID, OTHER")

PROMPT = """You classify one column of a tabular dataset for privacy protection (GDPR context, Finland).
Roles (pick exactly one):
- direct_identifier: identifies a person on its own (name, national id, email, phone, IBAN, card number, plate, street address)
- quasi_identifier: identifies in combination with others (birth date, age, sex, postcode, municipality, timestamps, coordinates, employer, occupation, profile attributes of a person)
- sensitive_attribute: the protected fact itself (diagnosis, health measurement, special support, income/salary, union, religion, payment default)
- technical_identifier: device/network/system ids that single out a person or device (IP, MAC, hostname, device id, user account, foreign keys to a person)
- free_text: unstructured text that may contain anything
- record_key: row id with no meaning outside the table
- non_sensitive: operational values safe to share as they are (product category, unit, status codes, weather, counts)
Subtype (pick one): {subtypes}

Table: {table} ({n_rows} rows). Other columns: {others}
This table describes {kind}.
Column: {column}
{n_unique} distinct values. Sample values: {samples}
Rule-based guess: {guess}

Answer with JSON only: {{"privacy_role": "...", "subtype": "...", "reason": "one short sentence"}}"""


def _ollama(prompt: str, model: str, timeout: int = 90) -> str:
    body = json.dumps({"model": model, "prompt": prompt, "stream": False, "think": False, "format": "json",
                       "options": {"temperature": 0, "num_predict": 200}}).encode()
    req = urllib.request.Request("http://localhost:11434/api/generate", data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())["response"]


def _load_dotenv() -> None:
    """Read KEY=value lines from the project's .env (git-ignored) into the environment, without printing them."""
    from pathlib import Path
    f = Path(__file__).resolve().parents[1] / ".env"
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _gemini(prompt: str, model: str = "gemini-3.8-flash", timeout: int = 60) -> str:
    _load_dotenv()
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("GEMINI_API_KEY not set (put it in .env as GEMINI_API_KEY=...)")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
    body = json.dumps({"contents": [{"parts": [{"text": prompt}]}],
                       "generationConfig": {"temperature": 0, "responseMimeType": "application/json"}}).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    import time
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                out = json.loads(r.read().decode())
            time.sleep(6.5)  # free-tier rate limit
            return out["candidates"][0]["content"]["parts"][0]["text"]
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 3:
                time.sleep(20 * (attempt + 1))
                continue
            raise
    raise RuntimeError("gemini: retries exhausted")


def second_opinion(backend: str, table: str, n_rows: int, others: list, person_table: bool, column: str,
                   n_unique: int, samples: list, guess: str, model: str | None = None) -> dict | None:
    prompt = PROMPT.format(subtypes=SUBTYPES, table=table, n_rows=n_rows, others=", ".join(others)[:400],
                           kind="people (one row per person)" if person_table else "events or records (one row per event)",
                           column=column, n_unique=n_unique, samples=" | ".join(str(s)[:40] for s in samples[:12]), guess=guess)
    try:
        raw = _ollama(prompt, model or "qwen3:8b") if backend == "ollama" else _gemini(prompt, model or "gemini-3.8-flash")
        m = re.search(r"\{.*\}", raw, re.S)
        ans = json.loads(m.group() if m else raw)
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)[:120]}
    role = str(ans.get("privacy_role", "")).strip().lower().replace(" ", "_").replace("-", "_")
    sub = str(ans.get("subtype", "")).strip().upper().replace(" ", "_")
    if role not in ROLES:
        return {"error": f"invalid role {role!r}"}
    return {"privacy_role": role, "subtype": sub or "OTHER", "reason": str(ans.get("reason", ""))[:200]}
