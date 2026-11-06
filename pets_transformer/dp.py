"""Differentially private public release for HEALTH-S2 (open weekly dashboard).

Mechanism (person-level, ε-DP, δ = 0):
- contribution bound: at most `bound` visits per patient are kept (earliest first);
- histogram H1 over (ISO week, 10-year age band, diagnosis group): Laplace noise, scale bound/ε1;
- histogram H2 over hour of day: Laplace noise, scale bound/ε2;
- the released `ed_visits.csv` is synthetic microdata expanded from H1 (one row per noisy count, week-start date)
  with hours sampled independently from H2 — so the weekly ILI/age/diagnosis cells and the hour curve are preserved,
  their joint is not (stated limitation);
- `dp_answers.csv` answers the 254-query workload directly with Laplace noise, ε3 split over the five query families
  (counts: sensitivity bound; means: noisy clipped sum / noisy count).
Total privacy budget ε = ε1 + ε2 + ε3 (sequential composition). No row map: nothing links a released row to a person.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from pets_eval import parse as Pz
from pets_eval.utility import health as H


def _laplace(rng, scale, size=None):
    return rng.laplace(0.0, scale, size)


def dp_release(visits: pd.DataFrame, eps_hist: float = 1.0, eps_hour: float = 0.2, eps_workload: float = 2.0,
               bound: int = 4, seed: int = 1453) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    rng = np.random.default_rng(seed)
    v = visits.copy()
    v = v[v.disposition.astype(str) != "cancelled_duplicate"]
    v["_arr"] = Pz.to_datetime(v["arrival_ts"])
    v = v.sort_values("_arr")
    has_id = v.patient_hetu.fillna("").astype(str) != ""
    v["_pid"] = np.where(has_id, v.patient_hetu.astype(str), "anon_" + v.visit_id.astype(str))
    v = v.groupby("_pid", sort=False).head(bound)            # contribution bound
    d = H.derive(v)
    # --- H1: week x age band x diagnosis group
    cells = d.groupby([d.week, d.age_band, d.diagnosis_group], dropna=True).size()
    noisy = np.clip(np.rint(cells.values + _laplace(rng, bound / eps_hist, len(cells))), 0, None).astype(int)
    h1 = pd.DataFrame({"week": [c[0] for c in cells.index], "age": [c[1] for c in cells.index],
                       "diagnosis_group": [c[2] for c in cells.index], "n": noisy})
    # --- H2: hour of day
    hours = d.hour.dropna().astype(int).value_counts().reindex(range(24), fill_value=0)
    h2 = np.clip(hours.values + _laplace(rng, bound / eps_hour, 24), 0, None)
    p_hour = h2 / h2.sum() if h2.sum() > 0 else np.full(24, 1 / 24)
    # --- expand to synthetic rows
    rows = []
    for _, r in h1.iterrows():
        if r.n <= 0:
            continue
        monday = pd.Timestamp.fromisocalendar(int(r.week[:4]), int(r.week.split("W")[1]), 1)
        hs = rng.choice(24, size=int(r.n), p=p_hour)
        for h in hs:
            rows.append((f"{monday:%Y-%m-%d} {h:02d}:00", r.age, r.diagnosis_group))
    prot = pd.DataFrame(rows, columns=["arrival_ts", "age", "diagnosis_group"])
    prot = prot.sample(frac=1.0, random_state=seed).reset_index(drop=True)
    # --- DP answers to the workload
    wl = H.build_workload(visits)
    true = H.evaluate_workload(v, wl)
    fam = {"HQ-ILI": [], "HQ-M": [], "HQ-LOS": [], "HQ-MG": [], "HQ-ADM": []}
    for q in wl["queries"]:
        key = next(k for k in fam if q["id"].startswith(k) and not (k == "HQ-M" and q["id"].startswith("HQ-MG")))
        fam[key].append(q)
    eps_f = eps_workload / len(fam)
    answers = {}
    for key, qs in fam.items():
        for q in qs:
            t = true.get(q["id"], np.nan)
            if q["type"] == "count":
                answers[q["id"]] = max(0.0, float(np.rint(t + _laplace(rng, bound / eps_f)))) if not np.isnan(t) else np.nan
            else:
                lo, hi = q.get("bounds", [0, 1])
                m = pd.Series(True, index=d.index)
                for k, val in q["filters"].items():
                    m &= d[k].astype(str) == str(val)
                col = d.loc[m, q["value"]].dropna().clip(lo, hi)
                s_noisy = float(col.sum() + _laplace(rng, bound * (hi - lo) / (eps_f / 2)))
                n_noisy = float(len(col) + _laplace(rng, bound / (eps_f / 2)))
                answers[q["id"]] = float(np.clip(s_noisy / n_noisy, lo, hi)) if n_noisy > 1 else np.nan
    dp_answers = pd.DataFrame({"id": list(answers), "value": [answers[k] for k in answers]})
    meta = {"mechanism": "Laplace, person-level DP", "epsilon_total": eps_hist + eps_hour + eps_workload,
            "epsilon": {"hist_week_age_dx": eps_hist, "hist_hour": eps_hour, "workload": eps_workload, "workload_per_family": eps_f},
            "delta": 0, "unit_of_privacy": "person", "contribution_bound_visits": bound, "cells_h1": int(len(h1)),
            "rows_released": int(len(prot)), "rows_original": int(len(visits)), "note": "hours sampled independently of (week, age, dx)"}
    return prot, dp_answers, meta


def write(out: Path, prot: pd.DataFrame, dp_answers: pd.DataFrame, meta: dict) -> None:
    out.mkdir(parents=True, exist_ok=True)
    prot.to_csv(out / "ed_visits.csv", index=False)
    dp_answers.to_csv(out / "dp_answers.csv", index=False)
    (out / "dp_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8", newline="\n")
