"""Central advisor parameter store.

All tunable thresholds live in configs/advisor.yaml; code reads them through
`get("dotted.path", default)` where the default is the original hardcoded
value — so a missing file or key never breaks anything, it just means
"factory settings".

Shadow mode: `override(partial_dict)` lets daily_brief re-run the signal
pipeline with candidate parameters (configs/advisor.shadow.yaml) WITHOUT
touching the published output — the two-week shadow-before-switch discipline
for any threshold change. Overrides are consulted before the file; both fall
back to the code default.

Governance rule (see configs/PARAMS-CHANGELOG.md): no threshold changes
without a changelog entry, and no live switch without a shadow period.
"""
from contextlib import contextmanager
from pathlib import Path
import yaml

CONFIG_PATH = Path(__file__).resolve().parents[2] / "configs" / "advisor.yaml"

_file_cache: dict | None = None
_override: dict | None = None


def _load_file() -> dict:
    global _file_cache
    if _file_cache is None:
        if CONFIG_PATH.exists():
            _file_cache = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
        else:
            _file_cache = {}
    return _file_cache


def _walk(d: dict, parts: list[str]):
    cur = d
    for p in parts:
        if not isinstance(cur, dict) or p not in cur:
            return None, False
        cur = cur[p]
    return cur, True


def get(path: str, default):
    """Dotted-path lookup: override dict -> advisor.yaml -> code default."""
    parts = path.split(".")
    if _override is not None:
        val, ok = _walk(_override, parts)
        if ok:
            return val
    val, ok = _walk(_load_file(), parts)
    if ok:
        return val
    return default


@contextmanager
def override(partial: dict):
    """Temporarily layer candidate parameters on top (shadow mode)."""
    global _override
    prev = _override
    _override = partial or {}
    try:
        yield
    finally:
        _override = prev


def reload() -> None:
    """Drop the file cache (tests / long-lived processes)."""
    global _file_cache
    _file_cache = None
