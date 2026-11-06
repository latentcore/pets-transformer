"""What each sharing scenario needs from the data (columns per table), derived from the toolkit's utility tasks
plus explicit overrides where the task registry's `needs` strings are ambiguous."""
from __future__ import annotations

import re

from pets_eval import io
from pets_eval.utility import all_tasks

# explicit needs per scenario: {table: {columns}}; the first alternative of a task's need is taken
OVERRIDES = {
    "HEALTH-S1": {
        "ed_visits": {"visit_id", "triage", "los_minutes", "municipality", "icd10_primary", "readmit_30d",
                      "date_of_birth", "temp_c", "arrival_ts", "patient_hetu"},
        "lab_results": {"visit_id", "lab_id", "test_code", "value", "unit"},
    },
    "HEALTH-S2": {
        "ed_visits": {"visit_id", "arrival_ts", "date_of_birth", "icd10_primary", "municipality", "triage",
                      "los_minutes", "disposition"},
    },
    "HEALTH-S3": {
        "ed_visits": {"visit_id", "disposition", "systolic_bp", "heart_rate", "resp_rate", "temp_c", "spo2", "pain_nrs",
                      "triage", "date_of_birth", "sex", "arrival_mode", "icd10_primary", "arrival_ts", "los_minutes"},
    },
}


def _parse_need(s: str) -> list[tuple[str | None, str]]:
    """'lab_results.visit_id' -> [('lab_results','visit_id')]; 'a|b' -> first alternative; 'a+b' -> both."""
    s = re.sub(r"\(.*?\)", "", s).strip()
    first = s.split("|")[0]
    out = []
    for part in first.split("+"):
        part = part.strip()
        if not part:
            continue
        if "." in part:
            t, c = part.split(".", 1)
            out.append((t, c))
        else:
            out.append((None, part))
    return out


def needs_for(scenario_id: str, root) -> dict:
    """{table: set(columns)} for a scenario: overrides first, else parsed from the utility task registry."""
    if scenario_id in OVERRIDES:
        return {t: set(c) for t, c in OVERRIDES[scenario_id].items()}
    scen = io.get_scenario(scenario_id, root)
    if not scen:
        raise KeyError(scenario_id)
    req = set(scen.get("utility_requirements", []))
    needs: dict = {}
    for t in all_tasks([scen["dataset"]]):
        if t.id in req or scenario_id in (t.scenarios or []):
            for tab, col in [x for n in t.needs for x in _parse_need(n)]:
                needs.setdefault(tab or t.table, set()).add(col)
    return needs
