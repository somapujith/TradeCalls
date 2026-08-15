"""Shared FastAPI dependencies and small helpers for the app/api/ routers."""
from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path


def generate_strategy_version() -> str:
    """Short git commit hash of the repo, or a UTC-timestamp fallback.

    Reproducibility key per docs/db.md — must uniquely tag a backtest run
    even when git isn't available at runtime (e.g. a bare deployment without
    a .git directory).
    """
    repo_root = Path(__file__).resolve().parents[3]
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
        commit_hash = result.stdout.strip()
        if commit_hash:
            return f"git-{commit_hash}"
    except (subprocess.SubprocessError, OSError, FileNotFoundError):
        pass

    return f"ts-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"


def parse_json_field(raw: str | None) -> dict | list | None:
    """score_breakdown / breakdown_by_* columns are JSON-encoded text — see
    docs/db.md. Falls back to the raw string wrapped as-is if it isn't valid
    JSON, rather than raising and taking down the whole response.
    """
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None


def targets_to_list(
    target_1r: float,
    target_1_5r: float,
    target_2r: float,
    target_3r: float,
    nearest_structural_target: float | None,
) -> list[float | None]:
    """Reassembles trade_setups' five target Numeric columns into the flat
    `targets` array documented in docs/api.md's GET /api/backtests/{strategy_version}/trades
    and GET /api/calls responses (order: 1R, 1.5R, 2R, 3R, nearest structural
    resistance) — matches what the frontend (TradeList.jsx) expects via
    Array.isArray(targets)/targets.map(...), even though storage is now
    column-per-target rather than a single JSON field.
    """
    return [target_1r, target_1_5r, target_2r, target_3r, nearest_structural_target]
