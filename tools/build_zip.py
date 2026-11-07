"""Build the source zip for the sinceai.app submission form.

The zip = the committed source tree (git archive HEAD) + the organiser-only `pets_submission/` folder.
`pets_submission/` is git-ignored on purpose: it holds the row maps (`rowmap_*.csv`, scoring only, never published)
and outputs on the organisers' unseen data. It goes to the organisers inside this zip and nowhere else.

    python tools/build_zip.py            # -> outputs/pets_transformer_source.zip
"""
from __future__ import annotations

import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs" / "pets_transformer_source.zip"
SUB = ROOT / "pets_submission"
LIMIT = 100 * 1024 * 1024  # form limit


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    if OUT.exists():
        OUT.unlink()
    subprocess.run(["git", "archive", "--format=zip", "-o", str(OUT), "HEAD"], cwd=ROOT, check=True)
    added = 0
    if SUB.is_dir():
        with zipfile.ZipFile(OUT, "a", compression=zipfile.ZIP_DEFLATED) as z:
            for p in sorted(SUB.rglob("*")):
                if p.is_file():
                    z.write(p, p.relative_to(ROOT).as_posix())
                    added += 1
    else:
        print("WARNING: pets_submission/ missing - run `python run.py submission ...` first", file=sys.stderr)
    with zipfile.ZipFile(OUT) as z:
        names = z.namelist()
    bad = [n for n in names if n.endswith(".env") or n.startswith(("materials/", "outputs/", ".venv/"))]
    rowmaps_outside = [n for n in names if "rowmap_" in n and not n.startswith("pets_submission/")]
    size = OUT.stat().st_size
    print(f"{OUT.relative_to(ROOT)}: {size:,} bytes, {len(names)} entries ({added} from pets_submission/)")
    if bad or rowmaps_outside or size > LIMIT:
        print(f"PROBLEM: forbidden={bad} rowmaps_outside={rowmaps_outside} size_over_limit={size > LIMIT}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
