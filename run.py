"""One command: protect a dataset for a sharing scenario and measure it.

    python run.py protect --dataset health --scenario HEALTH-S1 --variant week --time-res week
    python run.py protect --dataset health --scenario HEALTH-S1 --variant hour --time-res hour

Outputs: outputs/<dataset>/<scenario>/<variant>/  (protected tables, row maps for scoring, decisions.json, scorecard)
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pets_eval import io  # noqa: E402
from pets_eval.scorecard import run_scorecard  # noqa: E402

from pets_transformer import engine, scenarios  # noqa: E402
from pets_transformer.config import outputs_dir, package_root  # noqa: E402


def cmd_protect(a):
    root = package_root()
    domain = a.dataset
    labels = io.load_catalog(root)  # TODO: replace with the detector's output (--labels)
    if a.labels:
        import pandas as pd
        labels = pd.read_csv(a.labels, dtype=str).fillna("")
    tables = io.load_tables(root / "data" / domain)
    pol = engine.Policy(time_res=a.time_res, age_band=a.age_band, code_chars=a.code_chars, rare_min=a.rare_min,
                        k=a.k, keep_qi_categoricals=a.keep_qi_cats, text=a.text,
                        needs=scenarios.needs_for(a.scenario, root))
    t0 = time.time()
    res = engine.protect(domain, tables, labels, pol)
    out = outputs_dir() / domain / a.scenario / a.variant
    engine.write_release(res, out, pol)
    t1 = time.time()
    rep = run_scorecard(root / "data" / domain, out, scenario=a.scenario, root=root)
    t2 = time.time()
    s = rep["summary"]
    print(f"[{a.variant}] privacy {s['privacy']}  utility {s['utility']}  composite {s['composite']}   "
          f"(protect {t1 - t0:.1f}s, scorecard {t2 - t1:.1f}s)")
    for k, v in rep["risk_components"].items():
        print(f"  risk {v:6.3f}  {k}")
    for u in rep["utility"]:
        sc = " n/a" if u["score"] is None else f"{u['score']:.2f}"
        print(f"  util {sc}  {u['id']}  {u['description'][:60]}")
    (out / "summary.json").write_text(json.dumps({"variant": a.variant, "policy": pol.describe(), "summary": s,
                                                   "risks": rep["risk_components"],
                                                   "utility": {u["id"]: u["score"] for u in rep["utility"]}},
                                                  indent=2), encoding="utf-8", newline="\n")
    print(f"wrote {out}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("protect")
    p.add_argument("--dataset", required=True)
    p.add_argument("--scenario", required=True)
    p.add_argument("--variant", default="v1")
    p.add_argument("--labels", help="detector output CSV (default: field catalog = oracle labels)")
    p.add_argument("--time-res", default="week", choices=["hour", "day", "week", "month", "drop"])
    p.add_argument("--age-band", type=int, default=10)
    p.add_argument("--code-chars", type=int, default=3)
    p.add_argument("--rare-min", type=int, default=20)
    p.add_argument("--k", type=int, default=5)
    p.add_argument("--keep-qi-cats", action="store_true")
    p.add_argument("--text", default="drop", choices=["drop", "keep", "redact"])
    p.set_defaults(fn=cmd_protect)
    a = ap.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
