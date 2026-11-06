"""Privacy–utility frontier search: try many protection policies, score each with pets-eval, keep the Pareto front.

The scorecard is the oracle (≈25 s per run), so the search is a small staged grid, cached in runs.jsonl so a
re-run never repeats a policy. The result is not one magic setting but the whole trade-off curve, with the
chosen point (max composite) and the reference baselines marked.
"""
from __future__ import annotations

import hashlib
import itertools
import json
import time
from pathlib import Path

import pandas as pd

from pets_eval import io
from pets_eval.scorecard import run_scorecard

from . import engine, scenarios
from .config import outputs_dir, package_root

# reference points from docs/BENCHMARK.md (HEALTH-S1 utility; dataset-level privacy)
BASELINES = {
    "health": {"B0 (drop ids)": (0.28, 1.00), "B2 (classic SDC)": (0.36, 0.79), "B2-k (k>=5)": (0.52, 0.40)},
}

GRID_STAGE1 = {
    "time_res": ["hour", "day", "week", "month"],
    "k": [1, 3, 5, 10],
    "suppress_order": ["categorical_first", "time_first"],
    "keep_qi_categoricals": [False, True],
}
GRID_STAGE2 = {"age_band": [5, 10, 20], "rare_min": [5, 20, 50], "code_chars": [3, 4]}

SEARCH_KEYS = ["time_res", "k", "suppress_order", "keep_qi_categoricals", "age_band", "rare_min", "code_chars", "text"]


def _pid(params: dict) -> str:
    return hashlib.sha1(json.dumps({k: params[k] for k in SEARCH_KEYS if k in params}, sort_keys=True).encode()).hexdigest()[:10]


def evaluate(domain: str, scenario: str, params: dict, root: Path, tables: dict, labels: pd.DataFrame,
             work: Path) -> dict:
    pol = engine.Policy(needs=scenarios.needs_for(scenario, root), **params)
    t0 = time.time()
    res = engine.protect(domain, tables, labels, pol)
    out = work / _pid(params)
    engine.write_release(res, out, pol)
    rep = run_scorecard(root / "data" / domain, out, scenario=scenario, root=root)
    s = rep["summary"]
    return {"id": _pid(params), "params": {k: params.get(k) for k in SEARCH_KEYS}, "privacy": s["privacy"],
            "utility": s["utility"], "composite": s["composite"], "risks": rep["risk_components"],
            "utility_tasks": {u["id"]: u["score"] for u in rep["utility"]}, "seconds": round(time.time() - t0, 1),
            "suppression": [l for l in res["log"] if l.get("step") in ("local_suppression", "k_check")]}


def pareto(runs: list) -> list:
    pts = sorted(runs, key=lambda r: (-(r["privacy"] or 0), -(r["utility"] or 0)))
    front, best_u = [], -1.0
    for r in pts:
        if (r["utility"] or 0) > best_u:
            front.append(r)
            best_u = r["utility"] or 0
    return front


def search(domain: str, scenario: str, budget: int = 80, labels_path: str | None = None, base: dict | None = None) -> dict:
    root = package_root()
    labels = pd.read_csv(labels_path, dtype=str).fillna("") if labels_path else io.load_catalog(root)
    tables = io.load_tables(root / "data" / domain)
    work = outputs_dir() / "search" / domain / scenario / ("detected" if labels_path else "catalog")
    work.mkdir(parents=True, exist_ok=True)
    runs_file = work / "runs.jsonl"
    runs = [json.loads(l) for l in runs_file.read_text(encoding="utf-8").splitlines() if l.strip()] if runs_file.exists() else []
    done = {r["id"] for r in runs}
    base = dict(base or {})

    def run_grid(grid: dict, fixed: dict) -> None:
        nonlocal runs
        keys = list(grid)
        for combo in itertools.product(*[grid[k] for k in keys]):
            if len(runs) >= budget:
                return
            params = {**fixed, **dict(zip(keys, combo))}
            if _pid(params) in done:
                continue
            r = evaluate(domain, scenario, params, root, tables, labels, work)
            runs.append(r)
            done.add(r["id"])
            with runs_file.open("a", encoding="utf-8") as f:
                f.write(json.dumps(r) + "\n")
            print(f"  {r['composite']:.3f}  P {r['privacy']:.3f}  U {r['utility']:.3f}  {r['params']}  ({r['seconds']}s)", flush=True)

    stage1_fixed = {"age_band": 10, "rare_min": 20, "code_chars": 3, "text": "drop", **base}
    run_grid(GRID_STAGE1, stage1_fixed)
    best = max(runs, key=lambda r: r["composite"] or 0)
    stage2_fixed = {**best["params"], **base}
    run_grid(GRID_STAGE2, {k: v for k, v in stage2_fixed.items() if k not in GRID_STAGE2})
    def blanked(r):  # tie-break: among equal composites prefer the release that suppressed the fewest values
        return sum(l.get("rows_blanked", 0) for l in r.get("suppression", []))
    finer = {"hour": 4, "day": 3, "week": 2, "month": 1, "drop": 0}
    best = max(runs, key=lambda r: (round(r["composite"] or 0, 3), round(r["privacy"] or 0, 3),
                                    -blanked(r) > -1000, finer.get(r["params"].get("time_res"), 0), -blanked(r)))
    front = pareto(runs)
    summary = {"dataset": domain, "scenario": scenario, "n_runs": len(runs), "best": best, "front": front,
               "baselines": BASELINES.get(domain, {})}
    (work / "search_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8", newline="\n")
    plot(summary, work / "frontier.png")
    return summary


def plot(summary: dict, path: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    runs = summary["front"]
    fig, ax = plt.subplots(figsize=(7.5, 5.2), dpi=150)
    allr = [json.loads(l) for l in (path.parent / "runs.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    ax.scatter([r["privacy"] for r in allr], [r["utility"] for r in allr], s=18, c="#9aa5b1", label=f"{len(allr)} policies tried")
    fr = sorted(runs, key=lambda r: r["privacy"])
    ax.plot([r["privacy"] for r in fr], [r["utility"] for r in fr], "-o", c="#1565c0", ms=5, label="Pareto front")
    b = summary["best"]
    ax.scatter([b["privacy"]], [b["utility"]], s=160, marker="*", c="#c62828", zorder=5,
               label=f"chosen: composite {b['composite']:.2f} ({b['params']['time_res']}, k={b['params']['k']})")
    for name, (p, u) in summary.get("baselines", {}).items():
        ax.scatter([p], [u], marker="s", s=40, c="#2e7d32")
        ax.annotate(name, (p, u), textcoords="offset points", xytext=(5, -10), fontsize=8, color="#2e7d32")
    ax.set_xlabel("privacy score  (1 − risk, pets-eval)")
    ax.set_ylabel(f"utility retained  ({summary['scenario']} tasks)")
    ax.set_xlim(0, 1.05)
    ax.set_ylim(0, 1.05)
    ax.grid(alpha=.3)
    ax.set_title(f"Privacy–utility frontier — {summary['dataset']} / {summary['scenario']}")
    ax.legend(loc="lower left", fontsize=8)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
