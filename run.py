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


DEV_DATASETS = ["health", "finance", "cyber"]  # rules were tuned on these; the rest are held out


def cmd_detect(a):
    import pandas as pd
    from pets_eval.detect import score_detection
    from pets_transformer import detect as D
    root = package_root()
    gaz = D.Gazetteers.from_root(root)
    datasets = [d.name for d in sorted((root / "data").iterdir()) if d.is_dir()] if a.dataset == "all" else [a.dataset]
    out_dir = outputs_dir() / "detect"
    out_dir.mkdir(exist_ok=True)
    preds = []
    n_llm = 0
    for ds in datasets:
        t0 = time.time()
        tables = io.load_tables(root / "data" / ds)
        p = D.detect_tables(ds, tables, gaz)
        if a.llm != "none":
            from pets_transformer import llm as L
            p["rule_role"], p["rule_subtype"] = p.privacy_role, p.subtype
            for i, r in p[p.confidence.astype(float) < a.llm_threshold].iterrows():
                others = [c for c in tables[r.table].columns if c != r.column]
                ans = L.second_opinion(a.llm, r.table, int(r.n_rows), others, bool(r.person_table), r.column, int(r.n_unique),
                                       str(r.samples12).split(" | "), f"{r.privacy_role}/{r.subtype} ({r.evidence})", model=a.llm_model)
                n_llm += 1
                if ans and "privacy_role" in ans:
                    p.at[i, "privacy_role"], p.at[i, "subtype"] = ans["privacy_role"], ans["subtype"]
                    p.at[i, "evidence"] = f"{r.evidence} | LLM ({a.llm}): {ans['reason']}"
                    p.at[i, "confidence"] = 0.75
                else:
                    p.at[i, "evidence"] = f"{r.evidence} | LLM unavailable: {(ans or {}).get('error', '')}"
        suffix = "" if a.llm == "none" else f"_{a.llm}"
        p.to_csv(out_dir / f"{ds}_labels{suffix}.csv", index=False)
        preds.append(p)
        print(f"{ds}: {len(p)} columns in {time.time() - t0:.1f}s" + (f" ({n_llm} LLM calls so far)" if a.llm != "none" else ""))
    pred = pd.concat(preds, ignore_index=True)
    pred.to_csv(out_dir / ("all_labels.csv" if a.llm == "none" else f"all_labels_{a.llm}.csv"), index=False)
    cat = io.load_catalog(root)
    scored = [d for d in datasets if d in set(cat.dataset)]
    if not scored:
        return
    def show(title, dsets):
        r = score_detection(pred, cat, dsets)
        pr = {k: v["f1"] for k, v in r["per_role"].items()}
        print(f"{title}: macro-F1 {r['macro_f1']}  sensitive recall {r['sensitive_recall']}  accuracy {r['accuracy']}"
              f"  subtype acc {r.get('subtype_accuracy')}  per-role F1 {pr}")
        return r
    dev = [d for d in scored if d in DEV_DATASETS]
    held = [d for d in scored if d not in DEV_DATASETS]
    saved = {"dev_datasets": dev, "held_out_datasets": held}
    if dev:
        r = show("DEV   (" + ",".join(dev) + ")", dev)
        saved["dev"] = {k: v for k, v in r.items() if k not in ("errors", "confusion")}
        if a.errors:
            for e in r["errors"][:a.errors]:
                print(f"   miss {e['dataset']}/{e['table']}.{e['column']}: gold {e['privacy_role']} -> pred {e['predicted']}")
    if held:
        r = show("HELD-OUT (" + ",".join(held) + ")", held)
        saved["held_out"] = {k: v for k, v in r.items() if k not in ("errors", "confusion")}
    r = show("ALL", scored)
    saved["all"] = {k: v for k, v in r.items() if k not in ("errors", "confusion")}
    if a.dataset == "all":
        (out_dir / ("scores.json" if a.llm == "none" else f"scores_{a.llm}.json")).write_text(json.dumps(saved, indent=2), encoding="utf-8", newline="\n")


def cmd_submission(a):
    from pets_transformer import submission as Sb
    rel = {}
    for item in a.release:
        ds, scen, var = item.split(":")
        rel[ds] = (scen, var)
    out = Sb.build(rel)
    print(f"wrote {out}")


def cmd_search(a):
    from pets_transformer import search as S
    t0 = time.time()
    s = S.search(a.dataset, a.scenario, budget=a.budget, labels_path=a.labels)
    b = s["best"]
    print(f"best composite {b['composite']}  privacy {b['privacy']}  utility {b['utility']}  params {b['params']}")
    print(f"front: {[(round(r['privacy'], 2), round(r['utility'], 2)) for r in s['front']]}")
    print(f"{s['n_runs']} runs, {time.time() - t0:.0f}s; frontier.png written")


def cmd_redact(a):
    from pets_eval import text as T
    from pets_transformer import redact as Rd
    root = package_root()
    gaz = Rd.TextGazetteers.from_root(root)
    red = Rd.Redactor(gaz, clinical=(a.dataset == "health"))
    print(f"gazetteers: {len(gaz.first_names)} first names, {len(gaz.surnames)} surnames, {len(gaz.streets)} streets, "
          f"{len(gaz.municipalities)} municipalities, {len(gaz.places)} places")
    cat = io.load_catalog(root)
    df = io.read_table(root / "data" / a.dataset / f"{a.table}.csv")
    # customer_service has a hidden eval split: its gold spans are withheld, so score on the train split only
    if a.dataset == "customer_service" and a.table == "turns":
        conv = io.read_table(root / "data" / a.dataset / "conversations.csv")[["conversation_id", "split"]]
        df = df.merge(conv, on="conversation_id", how="left")
        n_eval = int((df.split == "eval").sum())
        df = df[df.split != "eval"].drop(columns=["split"])
        print(f"excluded {n_eval} eval-split turns from scoring (gold spans withheld)")
    if a.limit:
        df = df.head(a.limit)
    rk = io.record_key(cat, a.dataset, a.table, df) or (df.columns[0] if df[df.columns[0]].is_unique else None)
    if rk is None:
        df = df.reset_index().rename(columns={"index": "row_id"})
        rk = "row_id"
    t0 = time.time()
    if "conversation_id" in df.columns and "seq" in df.columns:
        df = df.sort_values(["conversation_id", "seq"], key=lambda s: s.astype(str).str.zfill(8))
    out_df, spans = Rd.redact_table(df, a.column, rk, red, conv_col="conversation_id", seq_col="seq")
    dt = time.time() - t0
    out = outputs_dir() / "redact" / f"{a.dataset}__{a.table}__{a.column}"
    out.mkdir(parents=True, exist_ok=True)
    out_df.to_csv(out / f"{a.table}.csv", index=False)
    Rd.write_spans(spans, out / "spans.jsonl")
    n_spans = sum(len(v) for v in spans.values())
    print(f"{len(df)} records, {n_spans} spans, {dt:.1f}s ({len(df) / max(dt, 1e-9):.0f} rec/s) -> {out}")
    gp = io.spans_path(root, a.dataset, a.table, a.column)
    if gp.exists():
        gold = io.load_spans(gp)
        gold = {k: v for k, v in gold.items() if k in spans} if a.limit else gold
        for mode in ("overlap", "exact"):
            r = T.span_scores(gold, spans, mode)
            m = r["micro"]
            print(f"{mode:8s} micro P {m['precision']:.3f} R {m['recall']:.3f} F2 {m['f2']:.3f}")
            if mode == "overlap":
                for lab, v in sorted(r["per_label"].items(), key=lambda x: -x[1]["support"] if "support" in x[1] else 0):
                    print(f"   {lab:14s} P {v['precision']:.3f} R {v['recall']:.3f} F2 {v['f2']:.3f}  (tp {v['tp']} fp {v['fp']} fn {v['fn']})")
        texts = dict(zip(out_df[rk].astype(str), out_df[a.column].fillna("").astype(str)))
        leak = T.redaction_leakage(gold, texts)
        print(f"leakage: all {leak['leakage_rate']:.4f}  identifying {leak['identifying_leakage_rate']:.4f}  "
              f"per label {{{', '.join(f'{k}:{v['rate']:.3f}' for k, v in leak['per_label'].items())}}}")
        for ex in leak["examples"][:8]:
            print(f"   leaked {ex['label']}: {ex['value']!r} in {ex['record_id']}")
        (out / "scores.json").write_text(json.dumps({"overlap": T.span_scores(gold, spans, 'overlap'),
                                                     "exact": T.span_scores(gold, spans, 'exact'), "leakage": leak},
                                                    indent=2, ensure_ascii=False), encoding="utf-8", newline="\n")


def cmd_report(a):
    from pets_transformer import report as Rp
    path = Rp.build(a.dataset, a.scenario, a.variant, labels_path=a.labels, title=a.title)
    print(f"wrote {path}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    rp = sub.add_parser("report")
    rp.add_argument("--dataset", required=True)
    rp.add_argument("--scenario", required=True)
    rp.add_argument("--variant", default="week")
    rp.add_argument("--labels")
    rp.add_argument("--title", default="PETs Transformer")
    rp.set_defaults(fn=cmd_report)
    r = sub.add_parser("redact")
    r.add_argument("--dataset", required=True)
    r.add_argument("--table", required=True)
    r.add_argument("--column", required=True)
    r.add_argument("--limit", type=int, default=0)
    r.set_defaults(fn=cmd_redact)
    s = sub.add_parser("search")
    s.add_argument("--dataset", required=True)
    s.add_argument("--scenario", required=True)
    s.add_argument("--budget", type=int, default=80)
    s.add_argument("--labels")
    s.set_defaults(fn=cmd_search)
    d = sub.add_parser("detect")
    d.add_argument("--dataset", default="all")
    d.add_argument("--errors", type=int, default=0, help="print up to N misclassified DEV columns")
    d.add_argument("--llm", default="none", choices=["none", "ollama", "gemini"], help="second opinion for low-confidence columns")
    d.add_argument("--llm-model", default=None)
    d.add_argument("--llm-threshold", type=float, default=0.6)
    d.set_defaults(fn=cmd_detect)
    sm = sub.add_parser("submission")
    sm.add_argument("--release", action="append", default=["health:HEALTH-S1:week"], help="dataset:scenario:variant")
    sm.set_defaults(fn=cmd_submission)
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
