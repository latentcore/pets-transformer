"""Assemble pets_submission/ for the organisers' optional checks (docs/EVALUATION.md):

- unseen_field_labels.csv                 column, privacy_role, subtype   (scored against private labels)
- customer_service_eval_redacted.csv      turn_id, text                    (scored against private gold spans)
- customer_service_eval_spans.jsonl       record_id, spans
- <dataset>/                              protected tables + row maps + decisions.json of the chosen release

Row maps are for scoring only. They are published to the organisers through this folder, never anywhere else.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pandas as pd

from pets_eval import io

from . import redact as Rd
from .config import PROJECT, outputs_dir, package_root


def build(releases: dict[str, tuple[str, str]], out: Path | None = None) -> Path:
    """releases: {dataset: (scenario, variant)} chosen releases to include."""
    root = package_root()
    out = out or (PROJECT / "pets_submission")
    out.mkdir(exist_ok=True)
    # 1. unseen labels
    u = pd.read_csv(outputs_dir() / "detect" / "unseen_labels.csv", dtype=str)
    u[["column", "privacy_role", "subtype"]].to_csv(out / "unseen_field_labels.csv", index=False)
    # 2. customer_service eval split, redacted with the same redactor
    gaz = Rd.TextGazetteers.from_root(root)
    red = Rd.Redactor(gaz)
    turns = io.read_table(root / "data" / "customer_service" / "turns.csv")
    conv = io.read_table(root / "data" / "customer_service" / "conversations.csv")[["conversation_id", "split"]]
    turns = turns.merge(conv, on="conversation_id", how="left")
    turns = turns.sort_values(["conversation_id", "seq"], key=lambda s: s.astype(str).str.zfill(8))
    red_df, spans = Rd.redact_table(turns, "text", "turn_id", red, conv_col="conversation_id", seq_col="seq")
    ev = red_df[red_df.split == "eval"]
    ev[["turn_id", "text"]].to_csv(out / "customer_service_eval_redacted.csv", index=False)
    ev_ids = set(ev.turn_id.astype(str))
    Rd.write_spans({k: v for k, v in spans.items() if k in ev_ids}, out / "customer_service_eval_spans.jsonl")
    # unseen free-text column: spans and redacted text, as the organisers' README allows
    u_red = outputs_dir() / "redact" / "unseen__export_2025_q4__c16"
    if (u_red / "spans.jsonl").exists():
        shutil.copy(u_red / "spans.jsonl", out / "unseen_c16_spans.jsonl")
        shutil.copy(u_red / "export_2025_q4.csv", out / "unseen_export_2025_q4_redacted.csv")
    # 3. chosen releases
    for ds, (scen, variant) in releases.items():
        src = outputs_dir() / ds / scen / variant
        dst = out / ds
        if dst.exists():
            shutil.rmtree(dst)
        dst.mkdir()
        for f in src.glob("*.csv"):
            shutil.copy(f, dst / f.name)
        for f in ("decisions.json", "scorecard.json", "scorecard.html", "summary.json"):
            if (src / f).exists():
                shutil.copy(src / f, dst / f)
        (dst / "README.md").write_text(f"# {ds} — {scen} ({variant})\n\nProtected release produced by PETs Transformer. "
                                       f"`rowmap_*.csv` map protected rows to original record ids for scoring only.\n",
                                       encoding="utf-8", newline="\n")
    manifest = {"releases": releases, "files": sorted(str(p.relative_to(out)) for p in out.rglob("*") if p.is_file())}
    (out / "MANIFEST.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8", newline="\n")
    return out
