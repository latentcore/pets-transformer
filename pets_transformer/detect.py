"""Detect: what does each column contain? Value-based profiling first, column-name hints second.

Output rows: dataset, table, column, privacy_role, subtype, confidence, evidence
Roles / subtypes follow metadata/taxonomy.md so `pets-eval detect` can score the result.

Development protocol (honest numbers): rules were tuned looking at the catalog of health, finance and cyber only;
the other six datasets and `unseen` are held out.

Two passes per table: (1) profile every column and find the columns that identify a person by their values alone;
(2) classify every column, knowing whether the table is a *person table* (one row describes a person: its
categorical attributes are quasi-identifiers) or an *event table* (rows are events: categories are operational).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

SAMPLE = 3000

# ------------------------------------------------------------------ value patterns
P = {
    "HETU": re.compile(r"^\d{6}[-+ABCDEFUVWXY]\d{3}[0-9ABCDEFHJKLMNPRSTUVWXY]$"),
    "HETU_NOSEP": re.compile(r"^\d{9}[0-9ABCDEFHJKLMNPRSTUVWXY]$"),
    "EMAIL": re.compile(r"^[\w.+-]+@[\w-]+(\.[\w-]+)+$"),
    "PHONE": re.compile(r"^(\+358|0)\s?[1-9]\d{0,2}[\s-]?\d{2,4}[\s-]?\d{2,4}([\s-]?\d{0,3})?$"),
    "IBAN": re.compile(r"^[A-Z]{2}\d{2}(\s?[A-Z0-9]{4}){3,7}(\s?[A-Z0-9]{1,4})?$"),
    "CARD": re.compile(r"^(\d[\s-]?){13,19}$"),
    "CARD_MASKED": re.compile(r"^\d{4,6}[\s*xX#]{4,12}\d{4}$|^(\*{4}\s?){3}\d{4}$"),
    "IPV4": re.compile(r"^(\d{1,3}\.){3}\d{1,3}(/\d{1,2})?$"),
    "MAC": re.compile(r"^([0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}$"),
    "PLATE": re.compile(r"^[A-ZÅÄÖ]{2,3}-\d{1,3}$"),
    "POSTCODE": re.compile(r"^\d{5}$"),
    "HEX32": re.compile(r"^[0-9a-fA-F]{32}$"),
    "HEX40": re.compile(r"^[0-9a-fA-F]{40}$"),
    "HEX64": re.compile(r"^[0-9a-fA-F]{64}$"),
    "URL": re.compile(r"^(https?://|www\.)\S+$", re.I),
    "UA": re.compile(r"^(Mozilla|Opera|curl|python-requests|Dalvik|okhttp)/", re.I),
    "HOSTNAME": re.compile(r"^[A-Za-z][A-Za-z0-9-]{1,30}(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}$|^[A-Z]{1,4}-[A-Z0-9]{2,12}-\d{2,6}$"),
    "ICD10": re.compile(r"^[A-Z]\d{2}(\.\d{1,2})?$"),
    "ISO_DT": re.compile(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(:\d{2})?(Z|[+-]\d{2}:?\d{2})?$"),
    "ISO_D": re.compile(r"^\d{4}-\d{2}-\d{2}$"),
    "FI_DT": re.compile(r"^\d{1,2}\.\d{1,2}\.\d{2,4}(\s+(klo\s+)?\d{1,2}[.:]\d{2})?$"),
    "SLASH_D": re.compile(r"^\d{1,2}/\d{1,2}/\d{2,4}$"),
    "EXPIRY": re.compile(r"^(0[1-9]|1[0-2])/\d{2}$"),
    "WEEK": re.compile(r"^\d{4}-?W\d{1,2}$"),
    "MONTH": re.compile(r"^\d{4}-\d{2}$|^\d{4}-\d{2}/\d$"),
    "ID_PREFIX": re.compile(r"^[A-Z]{1,8}\d{0,3}[-_]?\d{3,}[A-Z]?$"),
    "ID_ALNUM": re.compile(r"^[A-Z0-9]{6,}$"),
    "MONEY": re.compile(r"^-?\d{1,3}( \d{3})*(,\d{2})$|^-?\d+\.\d{2}$"),
    "LATLON": re.compile(r"^-?\d{1,3}\.\d{3,}$"),
    "BOOL": re.compile(r"^(true|false|yes|no|0|1|y|n|kyllä|ei)$", re.I),
    "AGE_BAND": re.compile(r"^\d{1,3}\s*[-–]\s*\d{1,3}$|^\d{1,3}\+$"),
}
SEX_VALUES = {"m", "f", "male", "female", "mies", "nainen", "man", "kvinna", "k", "n", "other", "muu", "x", "u"}
LANG_VALUES = {"fi", "sv", "en", "ru", "et", "ar", "so", "de", "fr", "es", "fin", "swe", "eng", "finnish", "swedish",
               "english", "suomi", "ruotsi", "englanti", "svenska", "other", "muu"}
STREET_SUFFIX = re.compile(r"(katu|tie|kuja|polku|ranta|rinne|väylä|kaari|raitti|puistikko|tori|gatan|vägen|gränd|"
                           r"stigen|strand|backen|väg|gata)\b", re.I)
ORG_HINT = re.compile(r"(\boy\b|\bab\b|\bltd\b|\boyj\b|koti\b|hoivakoti|palvelu|keskus|sairaala|koulu|lukio|opisto|"
                      r"yliopisto|kaupunki|kunta|seurakunta|yhdistys|\bry\b|center|centre|school|hospital|clinic|"
                      r"logistics|bank|pankki|market|kauppa)", re.I)
OCCUPATION_HINT = re.compile(r"(hoitaja|lääkäri|opettaja|insinööri|johtaja|myyjä|kuljettaja|siivooja|avustaja|"
                             r"assistentti|sihteeri|asentaja|teknikko|analyst|engineer|manager|nurse|teacher|driver|"
                             r"cleaner|assistant|developer|consultant|specialist|officer|clerk|operator)", re.I)
NAME_HINT = re.compile(r"(^|_)(name|nimi|holder|applicant|reporter|author|physician|teacher|guardian|display|given|"
                       r"surname|first_names|last_name|full_name|from_name|to_name)($|_)", re.I)
NOT_PERSON_NAME = re.compile(r"(street|stop|station|merchant|place|org|company|employer|school|course|test|rule|"
                             r"process|file|host|device|sensor|location|car_park|subject|product|user_agent|zone|"
                             r"district|area|programme|attachment|domain|network|car_|sender_display)", re.I)
SENSITIVE_NAME = re.compile(r"(diagnos|icd|triage|disposition|readmit|pep_|support|accessib|religion|income|salary|"
                            r"wage|pay_|_pay|grade$|grade_points|raw_points|absen|unexcused|excused|late_arr|"
                            r"sentiment|csat|medical|health|default|priority|kyc|late_payment|payment_plan|abnormal|"
                            r"^value$|los_minutes|systolic|diastolic|heart|resp_rate|temp_c|spo2|pain|kwh|cost|fee|"
                            r"merchant_name|^mcc$|union|verdict$)", re.I)
QI_NAME = re.compile(r"(sex|gender|language|municipality|city|town|postcode|zip|employer|occupation|job_title|"
                     r"department|office|shift|programme|school|class_group|heating_type|contract_type|arrival_mode|"
                     r"birth|dob|^age|country|work_pattern|home_|nationality|district|zone|unit$|vehicle|^line$|"
                     r"fare|applicant_type|floor_area|main_fuse|ev_charger|solar|registered|manager|expiry|"
                     r"currency|merchant_city|merchant_country|lines$)", re.I)
NONSENS_NAME = re.compile(r"(^unit$|qtype|rcode|method$|http_status|^direction$|quality|^term$|period|ref_range|"
                          r"test_code|test_name|credits|max_points|capacity|cloud|wind|temperature_c|oktas|bytes|"
                          r"size_kb|spam_score|read_confidence|seq$|n_turns|handle_time|resolved|speaker|note_type|"
                          r"split$|severity|mitre|rule_name|process_name|subject_code|subject_name|course_name|"
                          r"course_code|grade_scale|exam_session|academic_year|installed|name_sv|original_amount|"
                          r"decision$|project_type|device_count|interval_start|action$|result$|client_app|mfa_|"
                          r"failure_reason|answer$|query$|network_type|organisation|range_id|attack_label|campaign|"
                          r"label$|is_fraud|fraud_type|risk_level|^status$|channel|card_type|transfer_type|"
                          r"payment_method|marketing_consent|^intent$|category$|ssid|rssi|^type$|^count)", re.I)
ID_SUBTYPE_HINTS = [("customer", "CUSTOMER_ID"), ("student", "STUDENT_ID"), ("learner", "STUDENT_ID"),
                    ("employee", "EMPLOYEE_ID"), ("staff", "EMPLOYEE_ID"), ("physician", "EMPLOYEE_ID"),
                    ("agent_id", "EMPLOYEE_ID"), ("identity", "EMPLOYEE_ID"), ("patient", "PATIENT_ID"),
                    ("card_id", "CARD_ID"), ("card_ref", "CARD_ID"), ("device", "DEVICE_ID"), ("meter", "METER_ID"),
                    ("account", "ACCOUNT_ID"), ("property", "PROPERTY_ID"), ("sensor", "DEVICE_ID"),
                    ("vehicle", "DEVICE_ID"), ("bike_id", "DEVICE_ID"), ("ambulance", "DEVICE_ID"),
                    ("business_id", "ORG_NAME"), ("contract", "ACCOUNT_ID"), ("customer_number", "CUSTOMER_ID"),
                    ("reference", "ACCOUNT_ID"), ("session", "DEVICE_ID"), ("correlation", "RECORD_ID")]


@dataclass
class Gazetteers:
    first_names: set
    surnames: set
    municipalities: set

    @classmethod
    def from_root(cls, root: Path) -> "Gazetteers":
        f = root / "adversary" / "public_directory.csv"
        first, last, munis = set(), set(), set()
        if f.exists():
            d = pd.read_csv(f, dtype=str, usecols=["name", "city"]).fillna("")
            for n in d["name"]:
                parts = n.split()
                if len(parts) >= 2:
                    first.add(parts[0].lower())
                    last.add(parts[-1].lower())
            munis = set(d["city"].str.strip().str.lower()) - {""}
        return cls(first, last, munis)


# ------------------------------------------------------------------ profiling
def _share(vals: list, pat: re.Pattern) -> float:
    return float(np.mean([bool(pat.match(v)) for v in vals])) if vals else 0.0


def _luhn(s: str) -> bool:
    d = [int(c) for c in re.sub(r"\D", "", s)]
    if len(d) < 13:
        return False
    tot, alt = 0, False
    for x in reversed(d):
        if alt:
            x *= 2
            if x > 9:
                x -= 9
        tot += x
        alt = not alt
    return tot % 10 == 0


def _hetu_date_ok(v: str) -> bool:
    try:
        return 1 <= int(v[0:2]) <= 31 and 1 <= int(v[2:4]) <= 12
    except ValueError:
        return False


def _years(vals: list) -> list:
    out = []
    for v in vals:
        m = re.search(r"\b(19\d{2}|20\d{2})\b", v)
        if m:
            out.append(int(m.group(1)))
            continue
        m = re.match(r"^\d{1,2}[./]\d{1,2}[./](\d{2})$", v)
        if m:
            yy = int(m.group(1))
            out.append(1900 + yy if yy > 26 else 2000 + yy)
    return out


def profile(s: pd.Series, gaz: Gazetteers) -> dict:
    nn = s.dropna().astype(str).str.strip()
    nn = nn[nn != ""]
    n, n_nonnull = len(s), len(nn)
    f: dict = {"n": n, "n_nonnull": n_nonnull, "null_share": 1 - n_nonnull / max(n, 1)}
    if n_nonnull == 0:
        f.update(n_unique=0, unique_ratio=0.0, is_unique=False, mean_len=0.0, vals=[])
        return f
    f["n_unique"] = int(nn.nunique())
    f["unique_ratio"] = f["n_unique"] / n_nonnull
    f["is_unique"] = f["n_unique"] == n_nonnull
    vals = nn.sample(min(SAMPLE, n_nonnull), random_state=0).tolist()
    f["vals"] = vals
    lens = np.array([len(v) for v in vals])
    f["mean_len"], f["max_len"] = float(lens.mean()), int(lens.max())
    f["space_share"] = float(np.mean([v.count(" ") >= 2 for v in vals]))
    f["mean_tokens"] = float(np.mean([len(v.split()) for v in vals]))
    for k, pat in P.items():
        f[k] = _share(vals, pat)
    f["HETU_ANY"] = f["HETU"] + float(np.mean([bool(P["HETU_NOSEP"].match(v)) and _hetu_date_ok(v) for v in vals]))
    f["LUHN"] = float(np.mean([_luhn(v) for v in vals if P["CARD"].match(v)])) if f["CARD"] > 0 else 0.0
    toks = [re.split(r"[\s,\-]+", v.lower()) for v in vals]
    f["first_name_share"] = float(np.mean([any(t in gaz.first_names for t in ts) for ts in toks]))
    f["surname_share"] = float(np.mean([any(t in gaz.surnames for t in ts) for ts in toks]))
    f["muni_share"] = float(np.mean([v.lower() in gaz.municipalities for v in vals]))
    f["street_share"] = float(np.mean([bool(STREET_SUFFIX.search(v)) and bool(re.search(r"\d", v)) for v in vals]))
    f["org_share"] = float(np.mean([bool(ORG_HINT.search(v)) for v in vals]))
    f["occupation_share"] = float(np.mean([bool(OCCUPATION_HINT.search(v)) for v in vals]))
    f["sex_share"] = float(np.mean([v.lower() in SEX_VALUES for v in vals]))
    f["lang_share"] = float(np.mean([v.lower() in LANG_VALUES for v in vals]))
    nums = pd.to_numeric(pd.Series(vals).str.replace(" ", "", regex=False).str.replace(",", ".", regex=False), errors="coerce")
    f["num_share"] = float(nums.notna().mean())
    f["num_min"], f["num_max"] = (float(nums.min()), float(nums.max())) if nums.notna().any() else (None, None)
    f["int_share"] = float(np.mean([bool(re.match(r"^-?\d+$", v)) for v in vals]))
    f["has_time_share"] = float(np.mean([bool(re.search(r"(\d{1,2}:\d{2}|klo \d)", v)) for v in vals]))
    f["date_share"] = min(1.0, f["ISO_DT"] + f["ISO_D"] + f["FI_DT"] + f["SLASH_D"] + f["WEEK"] + f["MONTH"])  # mixed formats add up
    yrs = _years(vals) if f["date_share"] >= 0.5 else []
    f["year_min"], f["year_max"] = (min(yrs), max(yrs)) if yrs else (None, None)
    f["cap_token_share"] = float(np.mean([all(t[:1].isupper() for t in v.split()[:2]) for v in vals if v])) if vals else 0.0
    return f


# ------------------------------------------------------------------ classification
def identifies_person_by_value(f: dict) -> bool:
    return f.get("n_nonnull", 0) > 0 and (f["HETU_ANY"] >= 0.6 or f["EMAIL"] >= 0.6 or f["IBAN"] >= 0.6 or
                                          f["PHONE"] >= 0.6 or f["street_share"] >= 0.5 or
                                          ((f["first_name_share"] >= 0.5 or f["surname_share"] >= 0.5) and f["mean_tokens"] >= 1.5))


def classify(col: str, f: dict, person_table: bool) -> tuple[str, str, float, str]:
    """Return (privacy_role, subtype, confidence, evidence)."""
    name = col.lower()
    if f["n_nonnull"] == 0:
        return "non_sensitive", "OTHER", 0.2, "empty column"
    R = lambda role, sub, conf, ev: (role, sub, conf, ev)  # noqa: E731

    # ---- direct identifiers from values
    if f["HETU_ANY"] >= 0.6:
        return R("direct_identifier", "NATIONAL_ID", 0.95, f"{f['HETU_ANY']:.0%} valid HETU pattern (with or without separator)")
    if f["EMAIL"] >= 0.6:
        return R("direct_identifier", "EMAIL", 0.95, f"{f['EMAIL']:.0%} e-mail addresses / UPNs")
    if f["IBAN"] >= 0.6 and f["mean_len"] >= 15:
        return R("direct_identifier", "IBAN", 0.95, "IBAN pattern")
    if f["CARD"] >= 0.6 and f["LUHN"] >= 0.5:
        return R("direct_identifier", "PAYMENT_CARD", 0.95, "13–19 digits passing Luhn")
    if f["CARD_MASKED"] >= 0.6:
        return R("direct_identifier", "PAYMENT_CARD", 0.8, "masked card number (first/last digits)")
    if f["PHONE"] >= 0.6 and f["mean_len"] >= 7:
        return R("direct_identifier", "PHONE", 0.9, f"{f['PHONE']:.0%} Finnish phone numbers")
    if f["PLATE"] >= 0.6:
        return R("direct_identifier", "LICENSE_PLATE", 0.9, "licence plate pattern")
    if f["street_share"] >= 0.5:
        return R("direct_identifier", "STREET_ADDRESS", 0.9, "street name + house number")
    if f["mean_len"] >= 40 and f["space_share"] >= 0.5 and f["unique_ratio"] > 0.3:
        return R("free_text", "FREE_TEXT", 0.9, f"long text, mean {f['mean_len']:.0f} chars (names inside are spans, not a name column)")
    name_hits = max(f["first_name_share"], f["surname_share"])
    if name_hits >= 0.5 and f["unique_ratio"] > 0.02 and not NOT_PERSON_NAME.search(name) and f["org_share"] < 0.3:
        if f["mean_tokens"] >= 1.5 or NAME_HINT.search(name) or f["n_unique"] > 200:
            return R("direct_identifier", "PERSON_NAME", 0.9, f"first-name hits {f['first_name_share']:.0%}, surname hits {f['surname_share']:.0%}")
    if NAME_HINT.search(name) and not NOT_PERSON_NAME.search(name) and f["mean_tokens"] >= 1.5 and f["cap_token_share"] > 0.7:
        return R("direct_identifier", "PERSON_NAME", 0.7, "name-like column with capitalised tokens")

    # ---- technical identifiers from values
    if f["IPV4"] >= 0.6:
        return R("technical_identifier", "IP_ADDRESS", 0.95, "IPv4")
    if f["MAC"] >= 0.6:
        return R("technical_identifier", "MAC_ADDRESS", 0.95, "MAC address")
    if f["UA"] >= 0.5:
        return R("technical_identifier", "USER_AGENT", 0.9, "browser user agent")
    if f["URL"] >= 0.5:
        return R("free_text", "URL", 0.8, "URLs: paths and query strings can carry names, e-mails and secrets")
    if f["HEX64"] >= 0.8 or f["HEX40"] >= 0.8 or f["HEX32"] >= 0.8:
        if "sha" in name or "hash" in name or "md5" in name:
            return R("non_sensitive", "OTHER", 0.5, "file hash")
        return R("technical_identifier", "RECORD_ID", 0.7, "hex digest: hashed identifier (reversible by enumeration if unkeyed)")
    if f["ICD10"] >= 0.8:
        return R("sensitive_attribute", "DIAGNOSIS_CODE", 0.9, "ICD-10 codes")
    if f["HOSTNAME"] >= 0.6 and f["num_share"] < 0.5:
        return R("technical_identifier", "HOSTNAME", 0.85, "hostname pattern (may embed a person's name)")

    # ---- free text
    if f["mean_len"] >= 40 and f["space_share"] >= 0.5 and f["unique_ratio"] > 0.3:
        return R("free_text", "FREE_TEXT", 0.9, f"long text, mean {f['mean_len']:.0f} chars")
    if f["mean_len"] >= 15 and f["mean_tokens"] >= 2.5 and f["n_unique"] >= 150 and f["num_share"] < 0.2 and f["org_share"] < 0.3 \
            and f["muni_share"] < 0.3 and f["street_share"] < 0.3:
        return R("free_text", "FREE_TEXT", 0.7, "sentence-like values (hundreds of distinct phrases)")

    # ---- locations / demographics
    if f["LATLON"] >= 0.8 and (("lat" in name or "lon" in name or "lng" in name) or
                              (f["num_min"] is not None and 19 <= f["num_min"] and f["num_max"] <= 71)):
        return R("quasi_identifier", "GPS_COORDINATE", 0.9, "coordinate-like decimals")
    if f["POSTCODE"] >= 0.8 and f["n_unique"] > 5:
        return R("quasi_identifier", "POSTCODE", 0.9, "5-digit postcodes")
    if f["muni_share"] >= 0.6:
        return R("quasi_identifier", "MUNICIPALITY", 0.9, f"{f['muni_share']:.0%} municipality names")
    if f["sex_share"] >= 0.9 and f["n_unique"] <= 5:
        return R("quasi_identifier", "SEX", 0.9, "sex/gender codes")
    if f["lang_share"] >= 0.9 and f["n_unique"] <= 12:
        return R("quasi_identifier", "LANGUAGE", 0.85, "language codes")
    if f["EXPIRY"] >= 0.8:
        return R("quasi_identifier", "DATE", 0.7, "card expiry month")
    if f["date_share"] >= 0.8:
        has_time = f["has_time_share"] >= 0.5
        spread = f["year_min"] is not None and f["year_max"] is not None and (f["year_max"] - f["year_min"]) >= 25
        if not has_time and ("birth" in name or "dob" in name or (spread and f["year_max"] <= 2012)):
            return R("quasi_identifier", "BIRTH_DATE", 0.85, "dates of birth (years spread over decades)")
        if has_time:
            return R("quasi_identifier", "TIMESTAMP", 0.9, "timestamps with time of day")
        return R("quasi_identifier", "DATE", 0.8, "dates")
    if f["AGE_BAND"] >= 0.8 or (("age" in name) and f["int_share"] >= 0.9 and f["num_max"] is not None and f["num_max"] <= 120):
        return R("quasi_identifier", "AGE", 0.85, "age or age band")

    # ---- keys and ids
    idlike = f["ID_PREFIX"] >= 0.8 or (f["ID_ALNUM"] >= 0.8 and f["unique_ratio"] > 0.5) or \
        (f["int_share"] >= 0.95 and f["unique_ratio"] > 0.9 and f["mean_len"] >= 5)
    if idlike and f["is_unique"] and (name.endswith("_id") or "id" in name or f["ID_PREFIX"] >= 0.8):
        return R("record_key", "RECORD_ID", 0.85, "unique id per row")
    if idlike or name.endswith("_id") or name.endswith("_ref") or "_id_" in name:
        sub = next((s for h, s in ID_SUBTYPE_HINTS if h in name), "RECORD_ID")
        if NONSENS_NAME.search(name):
            return R("non_sensitive", "CATEGORY", 0.6, "label / campaign code")
        return R("technical_identifier", sub, 0.7, "identifier shared across rows (foreign key / entity id)")

    # ---- numbers
    if f["num_share"] >= 0.9:
        money = f["MONEY"] >= 0.5 or any(h in name for h in ("amount", "eur", "salary", "income", "cost", "fee", "price", "wage"))
        if money:
            if person_table or any(h in name for h in ("salary", "income", "wage")):
                return R("sensitive_attribute", "FINANCIAL_DATA", 0.7, "money amount describing a person")
            return R("quasi_identifier", "FINANCIAL_DATA", 0.6, "transaction amount: links to receipts and statements")
        if SENSITIVE_NAME.search(name):
            sub = "MEASUREMENT" if any(h in name for h in ("bp", "rate", "temp", "spo2", "pain", "kwh", "value")) else "HEALTH_DATA"
            return R("sensitive_attribute", sub, 0.7, "numeric with a sensitive column name")
        if NONSENS_NAME.search(name):
            return R("non_sensitive", "MEASUREMENT", 0.7, "operational number")
        if QI_NAME.search(name):
            return R("quasi_identifier", "CATEGORY", 0.6, "numeric profile attribute")
        if person_table and f["n_unique"] > 12:
            return R("sensitive_attribute", "MEASUREMENT", 0.5, "numeric attribute of a person")
        if f["n_unique"] <= 12:
            return R("quasi_identifier" if person_table else "non_sensitive", "CATEGORY", 0.5, "small numeric code set")
        return R("non_sensitive", "MEASUREMENT", 0.5, "number without a sensitive hint")

    # ---- categoricals and the rest
    if f["n_unique"] <= 60 or f["unique_ratio"] < 0.02:
        if SENSITIVE_NAME.search(name) and not NONSENS_NAME.search(name):
            sub = "HEALTH_DATA" if re.search(r"(diagnos|triage|dispos|readmit|medical|health|abnormal|support|accessib)", name) else "CATEGORY"
            return R("sensitive_attribute", sub, 0.7, "category with a sensitive column name")
        if NONSENS_NAME.search(name):
            return R("non_sensitive", "CATEGORY", 0.6, "operational category")
        if QI_NAME.search(name):
            return R("quasi_identifier", "CATEGORY", 0.6, "demographic / profile category")
        if f["org_share"] >= 0.5:
            return R("quasi_identifier", "ORG_NAME", 0.7, "organisation / employer names")
        if f["occupation_share"] >= 0.5:
            return R("quasi_identifier", "OCCUPATION", 0.7, "occupation titles")
        if f["BOOL"] >= 0.9:
            return R("quasi_identifier" if person_table else "non_sensitive", "CATEGORY", 0.5, "boolean flag")
        if person_table:
            return R("quasi_identifier", "CATEGORY", 0.5, "categorical attribute of a person (person table)")
        return R("non_sensitive", "CATEGORY", 0.5, "operational category (event table)")
    if f["org_share"] >= 0.4:
        return R("quasi_identifier", "ORG_NAME", 0.6, "organisation names")
    if f["cap_token_share"] > 0.8 and f["mean_tokens"] <= 4:
        return R("quasi_identifier", "LOCATION_NAME" if re.search(r"(name|location|place|stop|station|merchant|employer|school|org|company)", name) else "CATEGORY", 0.5, "proper-noun values")
    return R("non_sensitive", "OTHER", 0.3, "no rule matched")


def detect_tables(dataset: str, tables: dict, gaz: Gazetteers) -> pd.DataFrame:
    rows = []
    for tname, df in tables.items():
        profiles = {col: profile(df[col], gaz) for col in df.columns}
        person_table = sum(identifies_person_by_value(f) for f in profiles.values()) >= 2
        for col, f in profiles.items():
            role, sub, conf, ev = classify(col, f, person_table)
            rows.append({"dataset": dataset, "table": tname, "column": col, "privacy_role": role, "subtype": sub,
                         "confidence": conf, "evidence": ev, "person_table": person_table,
                         "n_unique": f.get("n_unique", 0), "n_rows": len(df), "sample": " | ".join(f.get("vals", [])[:3])[:120],
                         "samples12": " | ".join(str(v)[:40] for v in f.get("vals", [])[:12])})
    return pd.DataFrame(rows)
