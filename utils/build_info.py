"""Identificador corto y automático del código desplegado."""

from __future__ import annotations

import os
import re
import subprocess
from functools import lru_cache
from pathlib import Path


_ROOT = Path(__file__).resolve().parents[1]
_SHA_RE = re.compile(r"^[0-9a-fA-F]{7,64}$")


def _short_identifier(value: str) -> str:
    candidate = str(value or "").strip()
    if not _SHA_RE.fullmatch(candidate):
        return ""
    return candidate[:7].lower()


@lru_cache(maxsize=1)
def app_build_id() -> str:
    """Devuelve el commit corto de Railway o del checkout local."""
    railway_sha = _short_identifier(os.environ.get("RAILWAY_GIT_COMMIT_SHA", ""))
    if railway_sha:
        return railway_sha

    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short=7", "HEAD"],
            cwd=_ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=1.0,
        )
    except (OSError, subprocess.SubprocessError):
        return "local"
    return _short_identifier(result.stdout) or "local"
