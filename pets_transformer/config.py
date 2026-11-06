"""Paths. The proving-ground package is team-only material and lives outside the repository."""
from __future__ import annotations

import os
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
_DEFAULT_ROOT = PROJECT / "materials" / "PROVING_GROUND" / "TRUSTED_PETS_PROVING_GROUND_2026"


def package_root() -> Path:
    """Root of the PETs Proving Ground package: $PETS_ROOT or materials/ inside the project."""
    env = os.environ.get("PETS_ROOT")
    root = Path(env) if env else _DEFAULT_ROOT
    if not (root / "metadata" / "field_catalog.csv").exists():
        raise FileNotFoundError(f"PETs Proving Ground package not found at {root}; set PETS_ROOT")
    return root


def outputs_dir() -> Path:
    d = PROJECT / "outputs"
    d.mkdir(exist_ok=True)
    return d
