"""Deterministic vendor/counterparty intelligence.

Counterparties are extracted from transaction descriptions using the dataset's
own channel formats (UPI/<name>/..., "NEFT - <ref> - <acct> - <name>", etc.).
Everything here is computed from actual database rows — no hardcoded vendor
statistics, no LLM involvement.

This module feeds:
  - vendor profiles (Phase: Financial Twin)
  - the deterministic anomaly engine (bonus)
"""
from __future__ import annotations

import re

# Canonical counterparty vocabulary for the CURRENT dataset (seed=42 uses
# app.services.seed_data.COUNTERPARTIES). Matching is suffix/tail-based so
# official data with the same conventions also extracts; unknown tails are
# returned verbatim as their own counterparty rather than dropped.
_KNOWN_CPS: set[str] = set()


def set_known_counterparties(names: list[str]) -> None:
    global _KNOWN_CPS
    _KNOWN_CPS = {n.upper() for n in names}


# Import-time registration from the seed vocabulary (harmless for official
# data — extraction falls back to format-based parsing).
try:  # pragma: no cover - trivial
    from .seed_data import COUNTERPARTIES as _SEED_CPS

    set_known_counterparties(_SEED_CPS)
except Exception:  # pragma: no cover
    pass

# Channel-format patterns, most specific first. Each captures the
# counterparty name.
_PATTERNS = [
    re.compile(r"^UPI/([^/]+)/"),              # UPI/<name>/<ref>/<bank>
    re.compile(r"^NEFT\s+-\s+\S+\s+-\s+\S+\s+-\s+(.+)$"),   # NEFT - a - b - <name>
    re.compile(r"^IMPS/([^/]+)/"),             # IMPS/<name>/<ref>/<bank>
    re.compile(r"^IMPS OW/\S+/([^/]+)/"),      # IMPS OW/<ref>/<name>/<bank>/<acct>
    re.compile(r"^FT\s*-\s*\S+\s*-\s*\S+\s*-\s+(.+)$"),      # FT - ref - acct - <name>
    re.compile(r"^RTGS/([^/]+)/"),
    re.compile(r"^NEFT/[^/]+/[^/]+/(.+)$"),    # NEFT/<ref>/<bank>/<name>
]

# Known non-counterparty tails to discard
_NOISE = {"DAHISAR EAST", "SELECT CITY SAKET DELHI"}


def extract_counterparty(description: str | None) -> str | None:
    """Extract the counterparty name from a transaction description.

    Deterministic format parsing; returns None when nothing sensible matches.
    """
    if not description:
        return None
    d = description.strip()
    for pat in _PATTERNS:
        m = pat.match(d)
        if m:
            name = m.group(1).strip()
            # strip trailing location noise
            for noise in _NOISE:
                if name.upper().endswith(noise):
                    name = name[: -len(noise)].strip().rstrip("- ")
            if len(name) >= 3:
                return name.upper()
            return None
    return None


def build_vendor_profile(rows: list[dict]) -> dict | None:
    """Aggregate one vendor's transactions into a profile.

    ``rows`` are dicts with keys: description, transaction_amount,
    transaction_date, transaction_type. All values computed from the rows —
    nothing hardcoded.
    """
    if not rows:
        return None
    amounts = [float(r["transaction_amount"] or 0) for r in rows]
    dates = [r["transaction_date"] for r in rows if r.get("transaction_date")]
    return {
        "transaction_count": len(rows),
        "total_spend": sum(amounts),
        "average_transaction": sum(amounts) / len(amounts),
        "largest_transaction": max(amounts),
        "last_transaction": max(dates) if dates else None,
    }
