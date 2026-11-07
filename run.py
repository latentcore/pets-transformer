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
                        k=a.k, keep_qi_categoricals=a.keep_qi_cats, text=a.text, suppress_order=a.suppress_order,
                        k_extra_qis=("age",) if a.k_include_age else (),
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
    items = a.release or ["health:HEALTH-S1:week", "health:HEALTH-S2:dp_eps3"]
    rel = list(dict.fromkeys(tuple(item.split(":")) for item in items))  # argparse 'append' keeps the default: dedupe
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


def cmd_dp(a):
    from pets_transformer import dp as DP
    root = package_root()
    visits = io.read_table(root / "data" / "health" / "ed_visits.csv")
    if a.sweep:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        pts = []
        for e in [0.25, 0.5, 1.0, 2.0, 4.0, 8.0]:
            prot, answers, meta = DP.dp_release(visits, eps_hist=e, eps_hour=e / 5, eps_workload=2 * e, bound=a.bound)
            out = outputs_dir() / "health" / "HEALTH-S2" / f"dp_sweep_eps{e}"
            DP.write(out, prot, answers, meta)
            rep = run_scorecard(root / "data" / "health", out, scenario="HEALTH-S2", root=root)
            s = rep["summary"]
            u = {x["id"]: x["score"] for x in rep["utility"]}
            pts.append({"eps_total": meta["epsilon_total"], "eps_hist": e, "privacy": s["privacy"], "utility": s["utility"],
                        "composite": s["composite"], "tasks": u})
            print(f"  eps_total {meta['epsilon_total']:5.2f}  utility {s['utility']:.3f}  H-U1 {u.get('H-U1')}  H-U8 {u.get('H-U8')}  H-DP {u.get('H-DP')}", flush=True)
        sd = outputs_dir() / "search" / "health" / "HEALTH-S2"
        sd.mkdir(parents=True, exist_ok=True)
        (sd / "eps_sweep.json").write_text(json.dumps(pts, indent=2), encoding="utf-8", newline="\n")
        fig, ax = plt.subplots(figsize=(7.5, 4.6), dpi=150)
        xs = [p["eps_total"] for p in pts]
        ax.plot(xs, [p["utility"] for p in pts], "-o", c="#1565c0", label="utility (mean of H-U1, H-U8, H-DP)")
        for tid, c in (("H-U1", "#ef6c00"), ("H-U8", "#2e7d32"), ("H-DP", "#6a1b9a")):
            ax.plot(xs, [p["tasks"].get(tid) or 0 for p in pts], "--", c=c, alpha=.8, label=tid)
        ax.set_xscale("log")
        ax.set_xlabel("total privacy budget ε (person-level, contribution bound 4)")
        ax.set_ylabel("utility retained")
        ax.set_ylim(0, 1.05)
        ax.grid(alpha=.3)
        ax.set_title("HEALTH-S2 public dashboard: utility vs ε (Laplace mechanism)")
        ax.legend(fontsize=8)
        fig.tight_layout()
        fig.savefig(sd / "eps_frontier.png")
        print(f"wrote {sd / 'eps_frontier.png'}")
        return
    t0 = time.time()
    prot, answers, meta = DP.dp_release(visits, eps_hist=a.eps_hist, eps_hour=a.eps_hour, eps_workload=a.eps_workload, bound=a.bound)
    out = outputs_dir() / "health" / "HEALTH-S2" / a.variant
    DP.write(out, prot, answers, meta)
    t1 = time.time()
    rep = run_scorecard(root / "data" / "health", out, scenario="HEALTH-S2", root=root)
    s = rep["summary"]
    print(f"[{a.variant}] eps_total {meta['epsilon_total']}  privacy {s['privacy']}  utility {s['utility']}  composite {s['composite']}  "
          f"(release {t1 - t0:.1f}s, scorecard {time.time() - t1:.1f}s, {meta['rows_released']} synthetic rows)")
    for k, v in rep["risk_components"].items():
        print(f"  risk {v:6.3f}  {k}")
    for u in rep["utility"]:
        sc = " n/a" if u["score"] is None else f"{u['score']:.2f}"
        print(f"  util {sc}  {u['id']}  {u['description'][:60]}  {u.get('metric', '')[:50]}")
    (out / "summary.json").write_text(json.dumps({"variant": a.variant, "dp": meta, "summary": s, "risks": rep["risk_components"],
                                                   "utility": {u["id"]: u["score"] for u in rep["utility"]}}, indent=2),
                                      encoding="utf-8", newline="\n")


def cmd_report(a):
    from pets_transformer import report as Rp
    path = Rp.build(a.dataset, a.scenario, a.variant, labels_path=a.labels, title=a.title)
    print(f"wrote {path}")


def cmd_fiware(a):
    from pets_transformer import fiware
    fiware.run(a.release, a.broker, a.tenant, a.consume, a.min_composite)


def cmd_voice(a):
    from pets_transformer import voice
    voice.run(a.n, a.select_only, a.reuse, a.offline, a.max_chars, a.total_chars, a.subdir)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    fw = sub.add_parser("fiware", help="publish privacy-assessed releases as NGSI-LD entities (Smart Data Models DCAT-AP)")
    fw.add_argument("--release", action="append", default=None, help="dataset:scenario:variant (default: health S1 week + S2 dp_eps3)")
    fw.add_argument("--broker", default=None, help="NGSI-LD broker base URL (or env NGSI_LD_BROKER); omit for a dry run")
    fw.add_argument("--tenant", default=None, help="NGSILD-Tenant header")
    fw.add_argument("--consume", action="store_true", help="only run the recipient-side query")
    fw.add_argument("--min-composite", type=float, default=0.0)
    fw.set_defaults(fn=cmd_fiware)
    vo = sub.add_parser("voice", help="voice-channel privacy: ElevenLabs TTS -> Scribe STT -> redactor -> leakage scores")
    vo.add_argument("--n", type=int, default=28, help="customer turns in the sample")
    vo.add_argument("--max-chars", type=int, default=220)
    vo.add_argument("--total-chars", type=int, default=3600, help="cap on characters sent to text-to-speech")
    vo.add_argument("--select-only", action="store_true", help="choose the sample and score the text channel, no API calls")
    vo.add_argument("--reuse", action="store_true", help="keep saved audio/transcripts, only fill the gaps")
    vo.add_argument("--offline", action="store_true", help="recompute scores from saved transcripts")
    vo.add_argument("--subdir", default="voice", help="outputs/<subdir>/: 'voice' (Whisper run) or 'voice_scribe' (tools/scribe_stt.py)")
    vo.set_defaults(fn=cmd_voice)
    dpp = sub.add_parser("dp", help="differentially private public release for HEALTH-S2")
    dpp.add_argument("--variant", default="dp_eps3")
    dpp.add_argument("--eps-hist", type=float, default=1.0)
    dpp.add_argument("--eps-hour", type=float, default=0.2)
    dpp.add_argument("--eps-workload", type=float, default=2.0)
    dpp.add_argument("--bound", type=int, default=4)
    dpp.add_argument("--sweep", action="store_true", help="utility vs epsilon curve")
    dpp.set_defaults(fn=cmd_dp)
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
    sm.add_argument("--release", action="append", default=None, help="dataset:scenario:variant (default: health S1 week + S2 dp_eps3)")
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
    p.add_argument("--suppress-order", default="categorical_first", choices=["categorical_first", "time_first", "age_first"])
    p.add_argument("--k-include-age", action="store_true", help="hold the derived age band to k as well (scenario target)")
    p.add_argument("--text", default="drop", choices=["drop", "keep", "redact"])
    p.set_defaults(fn=cmd_protect)
    a = ap.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
