"""Print false negatives / false positives of a redaction run with context, per label (for tuning)."""
import json
import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from collections import defaultdict
from pathlib import Path

import pandas as pd

root = Path(sys.argv[1]); dataset, table, column = sys.argv[2], sys.argv[3], sys.argv[4]
out = Path(sys.argv[5]); labels = sys.argv[6].split(","); n_show = int(sys.argv[7]) if len(sys.argv) > 7 else 8
gold = {}
for line in open(root / "metadata" / "pii_spans" / f"{dataset}__{table}__{column}.jsonl", encoding="utf-8"):
    r = json.loads(line); gold[str(r["record_id"])] = r["spans"]
pred = {}
for line in open(out / "spans.jsonl", encoding="utf-8"):
    r = json.loads(line); pred[str(r["record_id"])] = r["spans"]
df = pd.read_csv(root / "data" / dataset / f"{table}.csv", dtype=str).fillna("")
idc = [c for c in df.columns if c.endswith("_id")][0]
texts = dict(zip(df[idc], df[column]))
fn, fp = defaultdict(list), defaultdict(list)
for rid, ps in pred.items():
    gs = gold.get(rid, [])
    t = texts.get(rid, "")
    for g in gs:
        if not any(p["label"] == g["label"] and p["start"] < g["end"] and g["start"] < p["end"] for p in ps):
            fn[g["label"]].append((t[g["start"]:g["end"]], t[max(0, g["start"] - 40):g["end"] + 25].replace("\n", " ")))
    for p in ps:
        if not any(g["start"] < p["end"] and p["start"] < g["end"] for g in gs):
            fp[p["label"]].append((t[p["start"]:p["end"]], t[max(0, p["start"] - 40):p["end"] + 25].replace("\n", " ")))
for lab in labels:
    print(f"\n### {lab}: FN {len(fn[lab])}  FP {len(fp[lab])}")
    for v, c in fn[lab][:n_show]:
        print(f"  FN {v!r:35}  …{c}…")
    for v, c in fp[lab][:n_show]:
        print(f"  FP {v!r:35}  …{c}…")
