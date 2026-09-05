"""Financial Twin — deterministic domain model of the business's finances.

Provenance is a first-class field on every value. Four levels exist:

    OFFICIAL_DATASET   — read from the loaded dataset (bank/account/transaction)
    DERIVED            — computed deterministically from OFFICIAL_DATASET rows
    USER_PREFERENCE    — configured by the user (rules, reserves)
    SYNTHETIC_DEMO     — demo values shipped for the hackathon, clearly labelled

Everything in this module is computed/configured — no LLM involvement.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from ..query_engine.duckdb_engine import DuckDBQueryEngine
from .vendor_intel import extract_counterparty

# ---------------------------------------------------------------------------
# Domain records
# ---------------------------------------------------------------------------

PROVENANCE_LEVELS = ("OFFICIAL_DATASET", "DERIVED", "USER_PREFERENCE", "SYNTHETIC_DEMO")


@dataclass
class FinancialRule:
    rule_type: str          # minimum_cash_buffer | approval_threshold | preferred_vendor
    value: float | str
    enabled: bool = True
    source: str = "SYNTHETIC_DEMO"
    created_at: str = field(default_factory=lambda: dt.date.today().isoformat())
    updated_at: str = field(default_factory=lambda: dt.date.today().isoformat())

    def to_dict(self) -> dict:
        return {
            "rule_type": self.rule_type, "value": self.value,
            "enabled": self.enabled, "source": self.source,
            "created_at": self.created_at, "updated_at": self.updated_at,
        }


@dataclass
class Reserve:
    name: str
    amount: float
    purpose: str
    priority: int = 1
    protected: bool = True
    source: str = "SYNTHETIC_DEMO"

    def to_dict(self) -> dict:
        return {
            "name": self.name, "amount": self.amount, "purpose": self.purpose,
            "priority": self.priority, "protected": self.protected,
            "source": self.source,
        }


# Demo rules/reserves — explicitly SYNTHETIC_DEMO, never passed off as
# official dataset facts. Loaded via load_demo_rules() by the API layer.
DEMO_RULES: list[FinancialRule] = [
    FinancialRule("minimum_cash_buffer", 500_000.0),
    FinancialRule("approval_threshold", 200_000.0),
]

DEMO_RESERVES: list[Reserve] = [
    Reserve("Payroll reserve", 600_000.0, "Salaries for the next payroll cycle",
            priority=1),
    Reserve("GST reserve", 150_000.0, "Upcoming GST liability", priority=2),
]


# ---------------------------------------------------------------------------
# Twin engine
# ---------------------------------------------------------------------------

class FinancialTwinEngine:
    """Deterministic Financial Twin: accounts, vendors, rules, reserves,
    cash position, affordability, what-if simulation."""

    def __init__(self, engine: DuckDBQueryEngine):
        self.db = engine
        self.rules: list[FinancialRule] = []
        self.reserves: list[Reserve] = []
        self.load_demo_rules()

    # ----- configuration -----------------------------------------------------

    def load_demo_rules(self) -> None:
        """Load the SYNTHETIC_DEMO rules/reserves (clearly labelled)."""
        self.rules = list(DEMO_RULES)
        self.reserves = list(DEMO_RESERVES)

    def _rule(self, rule_type: str) -> FinancialRule | None:
        for r in self.rules:
            if r.rule_type == rule_type and r.enabled:
                return r
        return None

    # ----- 5.1 accounts --------------------------------------------------------

    def accounts_overview(self) -> dict:
        """Official dataset facts + provenance per account."""
        from ..query_engine.duckdb_builder import _ACCT_FROM  # same join contract

        con = self.db._con
        rows = con.execute(
            f"""
            SELECT a.account_id, a.account_number, a.available_balance,
                   a.program_id, b.bank_code, b.bank_name
            FROM {_ACCT_FROM}
            ORDER BY a.available_balance DESC
            """
        ).fetchall()
        cols = ["account_id", "account_number_masked", "available_balance",
                "program_id", "bank_code", "bank_name"]
        accounts = [dict(zip(cols, r)) for r in rows]

        from ..query_engine.duckdb_engine import _mask_record

        accounts = [_mask_record(a) for a in accounts]
        total = sum(a["available_balance"] for a in accounts)
        return {
            "accounts": accounts,
            "total_available_balance": total,
            "account_count": len(accounts),
            "provenance": "OFFICIAL_DATASET",
            "note": (
                "available_balance is the dataset's authoritative field — "
                "never reconstructed by summing transactions"
            ),
        }

    # ----- 5.2/5.5 rules + reserves ---------------------------------------------

    def rules_and_reserves(self) -> dict:
        return {
            "rules": [r.to_dict() for r in self.rules],
            "reserves": [r.to_dict() for r in self.reserves],
            "provenance": "SYNTHETIC_DEMO",
            "note": (
                "Rules and reserves are demo configuration, clearly labelled "
                "SYNTHETIC_DEMO — they are NOT facts from the official dataset"
            ),
        }

    def protected_amount(self) -> float:
        return sum(r.amount for r in self.reserves if r.protected)

    # ----- 5.3 vendors -----------------------------------------------------------

    def vendor_profiles(self, limit: int = 10) -> dict:
        """Per-counterparty profiles computed from actual transactions."""
        con = self.db._con
        rows = con.execute(
            """
            SELECT t.description, t.transaction_amount, t.transaction_date,
                   t.transaction_type
            FROM "transaction" t
            WHERE t.description IS NOT NULL
            """
        ).fetchall()
        cols = ["description", "transaction_amount", "transaction_date",
                "transaction_type"]
        txns = [dict(zip(cols, r)) for r in rows]

        by_cp: dict[str, list[dict]] = {}
        for t in txns:
            cp = extract_counterparty(t["description"])
            if cp:
                by_cp.setdefault(cp, []).append(t)

        profiles = []
        for cp, tlist in by_cp.items():
            amounts = [float(t["transaction_amount"] or 0) for t in tlist]
            debit_amounts = [float(t["transaction_amount"] or 0) for t in tlist
                             if t["transaction_type"] == "debit"]
            profiles.append({
                "vendor": cp,
                "transaction_count": len(tlist),
                "total_spend": sum(debit_amounts),
                "average_transaction": (sum(debit_amounts) / len(debit_amounts))
                if debit_amounts else 0.0,
                "largest_transaction": max(amounts) if amounts else 0.0,
                "last_transaction": max(
                    (t["transaction_date"] for t in tlist
                     if t["transaction_date"] is not None), default=None,
                ).isoformat() if any(
                    t["transaction_date"] is not None for t in tlist) else None,
            })
        profiles.sort(key=lambda p: p["total_spend"], reverse=True)
        return {
            "vendors": profiles[:limit],
            "vendor_count": len(profiles),
            "provenance": "DERIVED",
            "note": (
                "counterparty profiles derived deterministically from "
                "transaction descriptions"
            ),
        }

    # ----- 5.4 reconciliation -------------------------------------------------------
    # The current dataset has NO reconciliation table. This adapter returns the
    # honest answer and defines the interface the extended dataset can fill.

    def reconciliation_status(self) -> dict:
        return {
            "available": False,
            "unreconciled_count": None,
            "unreconciled_amount": None,
            "provenance": "OFFICIAL_DATASET",
            "note": (
                "the current dataset contains no reconciliation records — "
                "questions about unreconciled transactions are answered as "
                "'data not available'; the adapter interface below is ready "
                "for the extended dataset"
            ),
            "adapter_interface": {
                "expected_table": "reconciliation",
                "expected_columns": ["transaction_id", "status", "reconciled_date"],
                "status": "not_loaded",
            },
        }

    # ----- 6. cash engine ------------------------------------------------------------

    def cash_position(self) -> dict:
        """True available cash = balances − protected reserves.

        Components and provenance:
          available_balance  OFFICIAL_DATASET (authoritative field)
          protected_reserves SYNTHETIC_DEMO   (demo configuration)
        Restricted money and upcoming commitments have NO source in the
        current dataset — they are reported as 0/absent, never invented.
        """
        overview = self.accounts_overview()
        total = overview["total_available_balance"]
        protected = self.protected_amount()
        restricted = 0.0      # no dataset source — explicitly zero, not guessed
        commitments = 0.0     # no dataset source — explicitly zero

        true_cash = total - restricted - protected - commitments

        components = [
            {"component": "Available across accounts", "amount": total,
             "sign": "+", "provenance": "OFFICIAL_DATASET"},
            {"component": "Restricted funds", "amount": restricted,
             "sign": "-", "provenance": "OFFICIAL_DATASET",
             "note": "no restricted-funds data in the current dataset"},
            {"component": "Protected reserves", "amount": protected,
             "sign": "-", "provenance": "SYNTHETIC_DEMO",
             "items": [f"{r.name}: {r.amount:,.0f}" for r in self.reserves
                       if r.protected]},
            {"component": "Upcoming commitments", "amount": commitments,
             "sign": "-", "provenance": "OFFICIAL_DATASET",
             "note": "no commitments data in the current dataset"},
        ]
        return {
            "available_balance": total,
            "restricted_amount": restricted,
            "protected_reserves": protected,
            "upcoming_commitments": commitments,
            "true_available_cash": true_cash,
            "components": components,
            "account_count": overview["account_count"],
            "provenance": {
                "available_balance": "OFFICIAL_DATASET",
                "restricted_amount": "OFFICIAL_DATASET",
                "protected_reserves": "SYNTHETIC_DEMO",
                "upcoming_commitments": "OFFICIAL_DATASET",
                "true_available_cash": "DERIVED",
            },
            "no_double_counting": (
                "reserves are subtracted once, from balances; balances are "
                "read directly from account.available_balance, never summed "
                "from transactions"
            ),
        }

    # ----- 7. affordability ------------------------------------------------------------

    def can_i_afford(self, vendor: str, amount: float) -> dict:
        """Deterministic feasibility analysis. NEVER executes a payment."""
        cash = self.cash_position()
        true_cash = cash["true_available_cash"]
        buffer_rule = self._rule("minimum_cash_buffer")
        approval_rule = self._rule("approval_threshold")

        cash_after = true_cash - amount
        affordable = cash_after >= 0

        # reserve violation: paying must not eat into protected reserves
        reserve_violation = cash_after < 0 and cash["protected_reserves"] > 0

        # approval: amount above configured threshold
        approval_required = (
            approval_rule is not None and amount > float(approval_rule.value)
        )
        buffer_violation = (
            buffer_rule is not None and cash_after < float(buffer_rule.value)
        )

        reasons = []
        if affordable:
            reasons.append(
                f"True available cash ({true_cash:,.0f}) covers the "
                f"{amount:,.0f} payment, leaving {cash_after:,.0f}."
            )
        else:
            reasons.append(
                f"True available cash ({true_cash:,.0f}) does not cover "
                f"{amount:,.0f} — shortfall of {abs(cash_after):,.0f}."
            )
        if reserve_violation:
            reasons.append(
                "The payment would dip into protected reserves "
                f"({cash['protected_reserves']:,.0f} protected)."
            )
        if buffer_violation and affordable:
            reasons.append(
                f"After payment, cash ({cash_after:,.0f}) falls below the "
                f"minimum buffer ({float(buffer_rule.value):,.0f})."
            )
        if approval_required:
            reasons.append(
                f"Amount exceeds the approval threshold "
                f"({float(approval_rule.value):,.0f}) — approval required."
            )

        # vendor history from real data (context for the decision)
        vendor_history = self._vendor_history(vendor)

        return {
            "vendor": vendor,
            "requested_amount": amount,
            "affordable": affordable and not buffer_violation,
            "cash_before": true_cash,
            "cash_after": cash_after,
            "reserve_violation": reserve_violation,
            "buffer_violation": buffer_violation,
            "approval_required": approval_required,
            "reasons": reasons,
            "vendor_history": vendor_history,
            "rules_applied": [
                r.to_dict() for r in (buffer_rule, approval_rule) if r
            ],
            "provenance": {
                "cash_before": "DERIVED",
                "cash_after": "DERIVED",
                "rules_applied": "SYNTHETIC_DEMO",
                "vendor_history": "DERIVED",
            },
            "disclaimer": (
                "deterministic feasibility analysis only — no payment was "
                "executed and none can be from this system"
            ),
        }

    def _vendor_history(self, vendor: str) -> dict | None:
        profiles = self.vendor_profiles(limit=1000)
        wanted = vendor.upper().strip()
        for p in profiles["vendors"]:
            if wanted in p["vendor"] or p["vendor"] in wanted:
                return p
        return None

    # ----- 8. what-if simulation ----------------------------------------------------------

    def simulate_payment(self, vendor: str, amount: float) -> dict:
        """Deterministic scenario: current position → simulated payment →
        new position + rule outcomes. NOT a forecast; no money moves."""
        affordability = self.can_i_afford(vendor, amount)
        cash = self.cash_position()

        before = cash["true_available_cash"]
        after = before - amount

        buffer_rule = self._rule("minimum_cash_buffer")
        approval_rule = self._rule("approval_threshold")

        def _rule_state(ok: bool) -> str:
            return "✓ preserved" if ok else "⚠ violated"

        return {
            "vendor": vendor,
            "payment_amount": amount,
            "before": {
                "true_available_cash": before,
                "protected_reserves": cash["protected_reserves"],
            },
            "after": {
                "true_available_cash": after,
                "protected_reserves": cash["protected_reserves"],
            },
            "rules_outcome": {
                "payroll_reserve": _rule_state(after >= 0),
                "minimum_buffer": _rule_state(
                    buffer_rule is None or after >= float(buffer_rule.value)
                ),
                "approval": (
                    "required" if approval_rule and amount > float(approval_rule.value)
                    else "not required"
                ),
            },
            "affordable": affordability["affordable"],
            "assumptions": [
                "static simulation — no future inflows/outflows are modelled",
                "reserves and rules are SYNTHETIC_DEMO configuration",
                "no payment is executed; this is analysis only",
            ],
            "provenance": {
                "before": "DERIVED",
                "after": "DERIVED",
                "rules_outcome": "SYNTHETIC_DEMO",
            },
        }
