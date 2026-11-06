"""Generic PET engine: field labels + sharing policy -> protected tables, row maps and a decision log.

Every column gets exactly one decision (keep / drop / pseudonym / age_band / time_round / postcode_prefix /
code_truncate / rare_to_other / round / redact) with a reason, so demo item 2 ("the PETs selected for each
data type") is produced by the tool, not written by hand.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import numpy as np
import pandas as pd

from pets_eval import parse as Pz

PSEUDONYM_LEN = 12


@dataclass
class Policy:
    """Parameters of one protected release. Defaults = a cautious 'controlled research' release."""
    key: bytes = b"replace-me-with-a-secret-from-a-vault"
    time_res: str = "week"            # hour | day | week | month | drop
    age_band: int = 10                # years; 0 = keep exact age
    age_top: int = 80                 # top-code ages >= this
    postcode_digits: int = 0          # 0 = drop, 2/3 = prefix
    code_chars: int = 3               # ICD-10 and similar codes truncated to this many characters
    rare_min: int = 20                # categorical QI values rarer than this -> "other"
    k: int = 5                        # local suppression target on catalogued quasi-identifiers
    keep_qi_categoricals: bool = False  # keep QI categoricals (sex, arrival_mode) the tasks do not need
    text: str = "drop"                # drop | keep | redact (redact uses pets_transformer.redact)
    measure_round: Optional[float] = None  # round numerical sensitive attributes to this step
    release_tables: Optional[list] = None  # None = every table with a utility need
    needs: dict = field(default_factory=dict)  # {table: {column, ...}} columns the scenario's tasks need
    pseudonym_subtypes: tuple = ("NATIONAL_ID", "PATIENT_ID", "CUSTOMER_ID", "STUDENT_ID", "EMPLOYEE_ID",
                                 "CARD_ID", "ACCOUNT_ID", "METER_ID", "DEVICE_ID", "USERNAME", "RECORD_ID")
    shuffle: bool = True
    seed: int = 1453
    redactor: Optional[Callable[[pd.Series], pd.Series]] = None

    def describe(self) -> dict:
        d = {k: v for k, v in self.__dict__.items() if k not in ("key", "redactor", "needs")}
        d["needs"] = {t: sorted(c) for t, c in self.needs.items()}
        return d


def pseudonym(values: pd.Series, key: bytes, prefix: str = "") -> pd.Series:
    """Keyed pseudonym (HMAC-SHA256, base32, 12 chars): consistent -> joins survive; no key -> no reversal."""
    def one(v):
        if v is None or (isinstance(v, float) and np.isnan(v)) or str(v).strip() == "":
            return ""
        digest = hmac.new(key, str(v).strip().encode("utf-8"), hashlib.sha256).digest()
        return prefix + base64.b32encode(digest).decode("ascii")[:PSEUDONYM_LEN]
    return values.map(one)


def round_time(s: pd.Series, res: str) -> pd.Series:
    d = Pz.to_datetime(s)
    if res == "hour":
        return d.dt.floor("h").dt.strftime("%Y-%m-%d %H:00").where(d.notna(), "")
    if res == "day":
        return d.dt.strftime("%Y-%m-%d").where(d.notna(), "")
    if res == "week":
        return Pz.to_week(d).fillna("")
    if res == "month":
        return Pz.to_month(d).fillna("")
    raise ValueError(res)


def age_band(age: pd.Series, width: int, top: int) -> pd.Series:
    if width <= 0:
        return age.round().astype("Int64").astype(str).replace("<NA>", "")
    lo = (np.floor(age / width) * width).clip(upper=top)
    return lo.map(lambda v: "" if np.isnan(v) else (f"{int(v)}+" if v >= top else f"{int(v)}-{int(v) + width - 1}"))


def rare_to_other(s: pd.Series, min_count: int, other: str = "other") -> pd.Series:
    vc = s.astype(str).value_counts()
    rare = set(vc[vc < min_count].index)
    return s.astype(str).where(~s.astype(str).isin(rare), other).where(s.notna(), "")


# ------------------------------------------------------------------ decisions
def decide(col: str, role: str, subtype: str, kind: str, needed: bool, pol: Policy, table_has_arrival: bool) -> tuple[str, str]:
    """(action, reason) for one column."""
    role, subtype, kind = (role or "").strip(), (subtype or "").strip().upper(), (kind or "").strip()
    if role == "record_key":
        return "pseudonym", "row key: keyed pseudonym keeps joins, hides sequence"
    if role == "direct_identifier":
        if needed and subtype in pol.pseudonym_subtypes:
            return "pseudonym", "direct identifier the analysis must link on: keyed pseudonym (HMAC)"
        return "drop", f"direct identifier ({subtype or 'id'}): removed"
    if role == "technical_identifier":
        if needed or subtype == "RECORD_ID":
            return "pseudonym", "technical identifier needed for joins: keyed pseudonym"
        return "drop", f"technical identifier ({subtype or 'id'}) not needed: removed"
    if role == "free_text":
        if pol.text == "redact" and needed:
            return "redact", "free text needed: identifiers redacted with typed placeholders"
        if pol.text == "keep":
            return "keep", "free text kept unchanged (policy)"
        return "drop", "free text: removed (not needed by the scenario)"
    if role == "quasi_identifier":
        if subtype == "BIRTH_DATE":
            return "age_band", f"birth date -> age at event in {pol.age_band}-year bands (top-coded {pol.age_top}+)"
        if subtype == "AGE":
            return "age_band", f"age -> {pol.age_band}-year bands"
        if subtype in ("TIMESTAMP", "DATE"):
            if not needed:
                return "drop", "timestamp not needed by the scenario: removed"
            if pol.time_res == "drop":
                return "drop", "timestamp removed (policy)"
            return "time_round", f"timestamp rounded to {pol.time_res}"
        if subtype == "POSTCODE":
            if pol.postcode_digits > 0 and needed:
                return "postcode_prefix", f"postcode truncated to {pol.postcode_digits} digits"
            return "drop", "postcode: removed (identifying with few other fields)"
        if subtype == "STREET_ADDRESS":
            return "drop", "street address: removed"
        if subtype == "GPS_COORDINATE":
            return "drop", "coordinates: removed (no geo task in this scenario)"
        if subtype in ("MUNICIPALITY", "LOCATION_NAME", "ORG_NAME", "OCCUPATION", "LANGUAGE", "NATIONALITY", "CATEGORY", "SEX"):
            if needed or pol.keep_qi_categoricals:
                return "rare_to_other", f"categorical quasi-identifier kept; values rarer than {pol.rare_min} -> 'other'"
            return "drop", "categorical quasi-identifier not needed: removed"
        if subtype in ("DEVICE_ID", "EMPLOYEE_ID", "CARD_ID"):
            return "drop", "identifier-like quasi-identifier: removed"
        if needed:
            return "rare_to_other", "quasi-identifier kept with rare values generalised"
        return "drop", "quasi-identifier not needed: removed"
    if role == "sensitive_attribute":
        if subtype == "DIAGNOSIS_CODE":
            return "code_truncate", f"diagnosis code truncated to {pol.code_chars} characters (hides rare sub-codes)"
        if kind == "numerical" and pol.measure_round:
            return "round", f"measurement rounded to {pol.measure_round}"
        return "keep", "sensitive attribute kept: the analysis needs it, link to the person is broken"
    return "keep", "non-sensitive: kept"


def apply(df: pd.DataFrame, col: str, action: str, pol: Policy, arrival_col: Optional[str]) -> tuple[Optional[str], Optional[pd.Series]]:
    """Return (new column name, values) or (None, None) to drop."""
    s = df[col]
    if action == "drop":
        return None, None
    if action == "keep":
        return col, s
    if action == "pseudonym":
        return col, pseudonym(s, pol.key)
    if action == "age_band":
        if arrival_col and arrival_col in df.columns and "birth" in col.lower():
            age = (Pz.to_datetime(df[arrival_col]) - Pz.to_date(s)).dt.days / 365.25
        elif "birth" in col.lower():
            age = Pz.age_from_birth(s)
        else:
            age = Pz.parse_numeric(s)
        return "age", age_band(age.astype(float), pol.age_band, pol.age_top)
    if action == "time_round":
        return col, round_time(s, pol.time_res)
    if action == "postcode_prefix":
        return col, Pz.postcode_prefix(s, pol.postcode_digits).fillna("")
    if action == "code_truncate":
        return col, s.astype(str).str.replace(".", "", regex=False).str[:pol.code_chars].where(s.notna(), "")
    if action == "rare_to_other":
        return col, rare_to_other(s, pol.rare_min)
    if action == "round":
        x = Pz.parse_numeric(s)
        return col, (np.round(x / pol.measure_round) * pol.measure_round)
    if action == "redact":
        if pol.redactor is None:
            return None, None
        return col, pol.redactor(s.fillna("").astype(str))
    raise ValueError(action)


def local_suppression(df: pd.DataFrame, qis: list, k: int, order: list, log: list) -> pd.DataFrame:
    """Blank quasi-identifier values of rows in equivalence classes smaller than k, one column at a time."""
    from pets_eval import privacy
    df = df.copy()
    qis = [q for q in qis if q in df.columns]
    if not qis:
        return df
    cols = [c for c in order if c in qis] + [c for c in qis if c not in order]
    for c in cols:
        ks = privacy.class_sizes(df, qis)
        small = ks < k
        if not small.any():
            break
        n = int(small.sum())
        df.loc[small, c] = ""
        log.append({"step": "local_suppression", "column": c, "rows_blanked": n, "k": k})
    ks = privacy.class_sizes(df, qis)
    log.append({"step": "k_check", "qis": qis, "min_k": int(ks.min()), "share_k_lt_k": round(float((ks < k).mean()), 4)})
    return df


# ------------------------------------------------------------------ main entry
def protect(domain: str, tables: dict, labels: pd.DataFrame, pol: Policy) -> dict:
    """labels: dataset, table, column, privacy_role, subtype[, data_kind]. Returns
    {"tables": {name: df}, "rowmaps": {name: df}, "decisions": [...], "log": [...]}"""
    lab = labels[labels.dataset == domain]
    out_tables, rowmaps, decisions, log = {}, {}, [], []
    release = pol.release_tables or [t for t in tables if t in pol.needs]
    for name in release:
        if name not in tables:
            log.append({"step": "missing_table", "table": name})
            continue
        df = tables[name]
        tl = lab[lab.table == name].set_index("column")
        needed = set(pol.needs.get(name, set()))
        arrival_col = next((c for c in df.columns if c in ("arrival_ts", "visit_ts", "event_ts", "started_ts", "ts")), None)
        rk = next((c for c in tl.index if tl.loc[c, "privacy_role"] == "record_key" and c in df.columns), None)
        new = {}
        qi_kept = []
        for col in df.columns:
            role = tl.loc[col, "privacy_role"] if col in tl.index else "non_sensitive"
            subtype = tl.loc[col, "subtype"] if col in tl.index else ""
            kind = tl.loc[col, "data_kind"] if (col in tl.index and "data_kind" in tl.columns) else ""
            action, reason = decide(col, role, subtype, kind, col in needed, pol, arrival_col is not None)
            newcol, vals = apply(df, col, action, pol, arrival_col)
            if action == "pseudonym" and role == "direct_identifier":
                newcol = "person_pseudonym" if str(subtype).upper() == "NATIONAL_ID" else f"{col}_pseudonym"
            decisions.append({"table": name, "column": col, "role": role, "subtype": subtype, "action": action,
                              "output_column": newcol, "reason": reason})
            if newcol is not None:
                new[newcol] = vals.values if hasattr(vals, "values") else vals
                if role == "quasi_identifier" and newcol == col:
                    qi_kept.append(col)
        p = pd.DataFrame(new, index=df.index)
        p = local_suppression(p, qi_kept, pol.k, order=["arrival_mode", "ambulance_unit", "sex", "language",
                                                         "municipality", "postcode", "arrival_ts", "sampled_ts",
                                                         "written_ts", "discharge_ts"], log=log)
        if pol.shuffle:
            perm = np.random.default_rng(pol.seed).permutation(len(p))
            p = p.iloc[perm].reset_index(drop=True)
            orig_ids = df[rk].astype(str).values[perm] if rk else None
        else:
            orig_ids = df[rk].astype(str).values if rk else None
        out_tables[name] = p
        if rk is not None:
            rowmaps[name] = pd.DataFrame({"protected_row": np.arange(len(p)), "original_record_id": orig_ids})
        log.append({"step": "released", "table": name, "rows": int(len(p)), "columns": list(p.columns)})
    for name in tables:
        if name not in release:
            log.append({"step": "withheld", "table": name, "reason": "no utility need in this scenario"})
    return {"tables": out_tables, "rowmaps": rowmaps, "decisions": decisions, "log": log}


def write_release(res: dict, out_dir: Path, pol: Policy) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, df in res["tables"].items():
        df.to_csv(out_dir / f"{name}.csv", index=False)
    for name, rm in res["rowmaps"].items():
        rm.to_csv(out_dir / f"rowmap_{name}.csv", index=False)  # scoring only, never published
    (out_dir / "decisions.json").write_text(json.dumps({"policy": pol.describe(), "decisions": res["decisions"],
                                                        "log": res["log"]}, indent=2, ensure_ascii=False),
                                            encoding="utf-8", newline="\n")
