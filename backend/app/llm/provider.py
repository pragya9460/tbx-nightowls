"""LLM abstraction for query understanding.

LLMProvider
    ├── OllamaProvider       (local qwen2.5-coder etc. — preferred for hackathon)
    ├── AnthropicProvider    (claude-haiku — optional cloud)
    └── RuleBasedProvider    (deterministic fallback, no LLM)

The provider's ONLY job is to map a user question + conversation context to a
structured FinancialQuery JSON. It never produces SQL or numbers; SQL is
compiled by duckdb_builder and numbers come from DuckDB.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from ..schemas.query import (
    Aggregation,
    ComparisonSpec,
    FinancialQuery,
    Intent,
    Metric,
    QueryFilters,
    parse_month_vs_month,
    today as app_today,
    resolve_date_range,
)


@dataclass
class QueryUnderstanding:
    """What a provider returns: a raw dict to be validated as FinancialQuery,
    or an explicit refusal/clarification."""

    query: dict | None = None
    refusal_reason: str | None = None      # unsupported | ambiguous | invalid
    refusal_message: str | None = None
    suggestions: list[str] = field(default_factory=list)
    model_used: str | None = None
    provider_used: str | None = None
    latency_ms: int | None = None


class LLMProvider(ABC):
    name = "base"

    @abstractmethod
    def understand(self, question: str, context: dict | None = None) -> QueryUnderstanding:
        ...


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

# Domains with NO table in the current schema — refuse, don't guess.
# Note: "vendor expenses/spend" is supported as debit transaction spend
# (counterparties live in transaction.description). Only true vendor-ledger
# questions (owing, vendor master list) are unsupported.
UNSUPPORTED_DOMAINS = [
    (r"salary|salaries|payroll|employees?|wages?", "employee payroll data"),
    (r"tax(es)?|gst|tds|income tax", "tax data"),
    (r"revenue|sales figures", "revenue/sales data"),
    (r"profit|margin|balance sheet|p&l", "profit/loss data"),
    (r"invoice[s]?\b|overdue|receivable|payable", "invoice or receivables data"),
    (r"owe (vendors?|suppliers?)|vendor (list|master|directory)|supplier (list|master)",
     "a vendor master / payables ledger"),
    (r"escrow|mandate|beneficiar", "escrow/mandate/beneficiary data"),
    (r"customers?\b|kyc", "customer/KYC data"),
    (r"forecast|projection", "forecasting"),
    (r"loan|emi|credit score", "loan/credit data"),
]

_FINANCE_SUGGESTIONS = [
    "What is my total available balance?",
    "How much did I spend last month?",
    "Which bank holds the most money?",
]

_VENDOR_MENTION = re.compile(r"\b(vendors?|suppliers?|payouts?)\b")
_VENDOR_SPEND_CUES = re.compile(
    r"\b(expense|expenses|spend|spent|spending|paid|payment|payments|cost|costs)\b"
)
_VENDOR_BREAKDOWN = re.compile(
    r"\b(break\s*down|by vendor|per vendor|top vendors?|which vendors?|each vendor)\b"
)

# Identity / chit-chat — must not fall through to a transaction total.
_IDENTITY = re.compile(
    r"\b("
    r"what(?:['’]?s| is) your name"
    r"|who are you"
    r"|what are you(?: called)?"
    r"|tell me your name"
    r")\b"
)
_CHITCHAT = re.compile(
    r"^(hi+|hello|hey|yo|thanks|thank you|good (morning|afternoon|evening)|ok|okay)"
    r"[\s!.?]*$"
)
_TXN_QUESTION = re.compile(
    r"\b("
    r"transaction|transactions|spend|spent|spending|paid|debit|credit|"
    r"received|incoming|inflow|outgoing|withdrew|withdraw|"
    r"how much|how many|amount|rupees?|money|inr"
    r")\b|₹"
)

_MONTH_NAMES = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8,
    "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

_BANK_ALIASES = {
    "hdfc": "HDFC", "icici": "ICIC", "sbi": "SBIN", "axis": "UTIB",
    "kotak": "KKBK", "canara": "CNRB", "union": "UBIN", "au": "AUBL",
    "au small finance": "AUBL", "tamilnad": "TMBL", "rbl": "RATN",
}


def _extract_bank(question: str) -> str | None:
    ql = question.lower()
    # longest alias match first
    for alias in sorted(_BANK_ALIASES, key=len, reverse=True):
        if re.search(rf"\b{re.escape(alias)}\b", ql):
            return _BANK_ALIASES[alias]
    return None


# Deterministic ambiguous-spend phrasing: "spend/spent" with no type cue.
_AMBIGUOUS_SPEND = re.compile(
    r"\b(how much did i spend|what did i spend|my spending|total spend)\b"
)
_DEBIT_CUES = re.compile(r"\b(spent|spend|spending|paid|debit|debited?|debits?|outgoing|withdraw\w*)\b")
_CREDIT_CUES = re.compile(r"\b(received|incoming|credited?|inflow|earned|deposited|came in|coming in|got)\b")


class RuleBasedProvider(LLMProvider):
    """Deterministic question→query mapper. Guarantees a working demo without
    an API key and doubles as the evaluation baseline."""

    name = "rule_based"

    def understand(self, question: str, context: dict | None = None) -> QueryUnderstanding:
        q = question.lower().strip()
        context = context or {}
        today = app_today()

        # 1. Unsupported domains — refuse, never guess.
        for pat, what in UNSUPPORTED_DOMAINS:
            if re.search(pat, q):
                return QueryUnderstanding(
                    refusal_reason="unsupported",
                    refusal_message=(
                        f"I can't answer that because {what} is not available in the "
                        "current dataset, which covers bank accounts and transactions only."
                    ),
                    suggestions=list(_FINANCE_SUGGESTIONS),
                    provider_used=self.name,
                )

        # 1a. Vendor/supplier *expenses* → debit transaction spend.
        # There is no vendor table; counterparties live in transaction.description.
        if _VENDOR_MENTION.search(q) and (
            _VENDOR_SPEND_CUES.search(q) or _VENDOR_BREAKDOWN.search(q)
        ):
            return self._vendor_expense_intent(q, today)

        # 1b. Identity / greetings — never guess a financial total.
        if _IDENTITY.search(q) or _CHITCHAT.search(q):
            return self._off_topic_refusal()

        # 1c. Explicit "July vs August" two-month comparison.
        pair = parse_month_vs_month(q)
        if pair and (
            re.search(r"compare|versus|\bvs\b|expense|spend|spent|paid|debit|credit|received", q)
        ):
            m1, m2 = pair
            txn_type = None
            if _DEBIT_CUES.search(q) or re.search(r"\bexpense", q):
                txn_type = "debit"
            elif _CREDIT_CUES.search(q):
                txn_type = "credit"
            filters = self._txn_filters(q, txn_type, None)
            return self._validated(
                intent=Intent.COMPARISON,
                metric=Metric.TRANSACTION_AMOUNT,
                aggregation=Aggregation.SUM,
                filters=filters,
                range_spec={"type": "calendar_month", "month": m1},
                comparison={"against": "named_month", "month": m2},
            )

        # 2. Reference / UTR lookup — must come before spend detection, and
        #    must use the ORIGINAL question: reference ids and UTRs are
        #    case-sensitive values.
        if re.search(r"\butr\b", q):
            utr = self._after_keyword(question, "utr")
            if utr:
                return self._lookup({"utr_number": utr}, today)
            return QueryUnderstanding(
                refusal_reason="ambiguous",
                refusal_message="Which UTR number should I look for? Please provide the full value.",
                provider_used=self.name,
            )
        if re.search(r"reference|ref no|ref number|ref id", q) or re.search(
            r"\b(find|show|search|look up)\b.*\b(\d{6,}|\bs?\d{7,})\b", q
        ):
            ref = self._extract_reference(question)
            if ref:
                return self._lookup({"reference_id": ref}, today)

        # 2b. "What about July?" — a month-swap follow-up: same metric/type,
        #     new named month. Distinct from an explicit comparison request.
        month_swap = re.search(
            r"what about|how about|and (?:in|for) (\w+)|in (\w+) \?", q
        )
        if month_swap and context.get("last_intent") and re.search(
            r"\b(january|february|march|april|may|june|july|august|september|"
            r"october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|oct|nov|dec)\b",
            q,
        ):
            metric = (Metric.TRANSACTION_COUNT
                      if context.get("last_metric") == "transaction_count"
                      else Metric.TRANSACTION_AMOUNT)
            filters = dict(context.get("last_filters") or {})
            filters.pop("date_range", None)
            return self._validated(
                intent=Intent.TRANSACTION_SUMMARY,
                metric=metric,
                aggregation=Aggregation.SUM if metric == Metric.TRANSACTION_AMOUNT
                else Aggregation.COUNT,
                filters=filters,
                range_spec=self._date_spec(q, today),
            )

        # 3. Balance / account / bank intelligence.
        #    Transaction questions that mention a bank ("from my SBI
        #    accounts") must NOT be captured by the account-list gate below.
        mentions_transactions = re.search(r"transaction|spend|spent|paid|debit|credit", q)
        if re.search(r"balance|how much (money|do i have)|have in\b|which bank holds|holds the most money", q) \
           and not _CREDIT_CUES.search(q) and not mentions_transactions:
            return self._balance_intent(q, context, today)
        # "how many accounts ... per/with each bank" BEFORE the generic list gate
        if re.search(r"how many accounts", q) or (
                not mentions_transactions
                and re.search(r"accounts?.*(each bank|per bank)", q)):
            return self._validated(
                intent=Intent.BANK_ACCOUNT_COUNT,
                metric=Metric.TRANSACTION_COUNT,
                aggregation=Aggregation.COUNT,
                filters=self._bank_filter(q),
                range_spec={"type": "all_time"},
            )
        if not mentions_transactions and re.search(
                r"my accounts|all accounts|accounts do i have|show.*accounts", q):
            return self._account_list(q, context, today)

        # 4. Transaction intelligence.
        return self._transaction_intent(question, context, today)

    # ----- intent builders ----------------------------------------------------

    def _vendor_expense_intent(self, q: str, today: dt.date) -> QueryUnderstanding:
        """Map vendor/supplier expense questions onto debit transaction spend.

        No vendor master exists in the TBX schema — we sum (or list) debit
        transactions for the asked period. Breakdown-by-vendor is answered as
        a top-description listing (description = counterparty text).
        """
        if _VENDOR_BREAKDOWN.search(q):
            # List recent/large debit counterparties for the period.
            return self._validated(
                intent=Intent.TRANSACTION_LIST,
                metric=Metric.TRANSACTION_AMOUNT,
                aggregation=Aggregation.NONE,
                filters={"transaction_type": "debit"},
                range_spec=self._date_spec(q, today),
                limit=20,
            )
        return self._validated(
            intent=Intent.TRANSACTION_SUMMARY,
            metric=Metric.TRANSACTION_AMOUNT,
            aggregation=Aggregation.SUM,
            filters={"transaction_type": "debit"},
            range_spec=self._date_spec(q, today),
        )

    def _balance_intent(self, q: str, context: dict, today: dt.date) -> QueryUnderstanding:
        bank_code = self._extract_bank(q)
        highest = re.search(r"highest|most money|largest|top", q)
        total = re.search(r"total|across all|overall", q)
        filters = {"bank_code": bank_code} if bank_code else {}

        if highest and not bank_code:
            # "which bank holds the most money" → group by bank;
            # "which account has the highest balance" → group by account.
            by_bank = re.search(r"which bank|bank\b", q)
            return self._validated(
                intent=Intent.BANK_BALANCE if by_bank else Intent.ACCOUNT_BALANCE,
                metric=Metric.BALANCE,
                aggregation=Aggregation.SUM,
                filters={},
                range_spec={"type": "all_time"},
                group_by=["bank"] if by_bank else ["account"],
                limit=5,
            )
        if highest and bank_code:
            return self._validated(
                intent=Intent.BANK_BALANCE,
                metric=Metric.BALANCE,
                aggregation=Aggregation.MAX,
                filters=filters,
                range_spec={"type": "all_time"},
                group_by=["account"],
                limit=5,
            )
        if re.search(r"which bank", q):
            return self._validated(
                intent=Intent.BANK_BALANCE,
                metric=Metric.BALANCE,
                aggregation=Aggregation.SUM,
                filters=filters,
                range_spec={"type": "all_time"},
                group_by=["bank"],
                limit=10,
            )
        if re.search(r"my account|balance of", q) and not total and not bank_code:
            # single account — by masked number if present in the question
            num = re.search(r"\b(\d{12,14})\b", q)
            if num:
                return QueryUnderstanding(
                    refusal_reason="ambiguous",
                    refusal_message=(
                        "For security I can't look up accounts by their full number. "
                        "Ask for a bank's balance instead — e.g. 'What is my balance in HDFC?'"
                    ),
                    provider_used=self.name,
                )
        return self._validated(
            intent=Intent.ACCOUNT_BALANCE,
            metric=Metric.BALANCE,
            aggregation=Aggregation.SUM,
            filters=filters,
            range_spec={"type": "all_time"},
        )

    def _account_list(self, q: str, context: dict, today: dt.date) -> QueryUnderstanding:
        bank_code = self._extract_bank(q)
        filters = {"bank_code": bank_code} if bank_code else {}
        return self._validated(
            intent=Intent.ACCOUNT_LIST,
            metric=Metric.BALANCE,
            aggregation=Aggregation.NONE,
            filters=filters,
            range_spec={"type": "all_time"},
            limit=25,
        )

    def _transaction_intent(self, question: str, context: dict, today: dt.date) -> QueryUnderstanding:
        q = question.lower().strip()
        # ----- transaction type -------------------------------------------------
        if _CREDIT_CUES.search(q) and not _DEBIT_CUES.search(q):
            txn_type = "credit"
        elif _DEBIT_CUES.search(q) and not _CREDIT_CUES.search(q):
            txn_type = "debit"
        elif _AMBIGUOUS_SPEND.search(q):
            # Spec §6: make the debit interpretation explicit, don't guess silently.
            return QueryUnderstanding(
                refusal_reason="ambiguous",
                refusal_message=(
                    "I interpreted 'spent' as debit transactions (money out). "
                    "Is that what you meant?"
                ),
                suggestions=[
                    "How much did I spend (debit) last month?",
                    "How much money came in last month?",
                ],
                provider_used=self.name,
            )
        else:
            txn_type = None  # both types

        # ----- description search ------------------------------------------------
        desc = None
        m = re.search(
            r"(?:at|from|to|containing|with|by)\s+([A-Z][A-Za-z&. ]{3,40})", question
        ) or None
        if m:
            candidate = m.group(1).strip()
            # drop trailing stop words
            candidate = re.sub(
                r"\s+(last month|this month|last week|this week|in \w+|yesterday|today)$",
                "", candidate, flags=re.IGNORECASE,
            ).strip()
            if len(candidate) >= 3:
                # drop trailing punctuation ("Reliance.")
                candidate = candidate.rstrip(".,;:!?")
                desc = candidate.upper()

        # ----- count vs amount ----------------------------------------------------
        if re.search(r"how many|count of|number of", q):
            return self._validated(
                intent=Intent.TRANSACTION_SUMMARY,
                metric=Metric.TRANSACTION_COUNT,
                aggregation=Aggregation.COUNT,
                filters=self._txn_filters(q, txn_type, desc),
                range_spec=self._date_spec(q, today),
            )

        # ----- which month had the highest (BEFORE generic "highest") ----------
        if re.search(r"which month|what month|month had the highest|per month|by month", q):
            return self._validated(
                intent=Intent.MONTHLY_TREND,
                metric=Metric.TRANSACTION_AMOUNT,
                aggregation=Aggregation.SUM,
                filters=self._txn_filters(q, txn_type, desc),
                range_spec={"type": "last_n_months", "n_months": 12},
                group_by=["month"],
            )

        # ----- largest / top --------------------------------------------------------
        if re.search(r"largest|biggest|top \d+|highest", q):
            return self._validated(
                intent=Intent.TRANSACTION_LIST,
                metric=Metric.TRANSACTION_AMOUNT,
                aggregation=Aggregation.NONE,
                filters=self._txn_filters(q, txn_type, desc),
                range_spec=self._date_spec(q, today),
                limit=10,
            )

        # ----- top descriptions by spend ----------------------------------------------
        if re.search(r"top transaction descriptions|descriptions by spend|top spend", q):
            return self._validated(
                intent=Intent.TOP_DESCRIPTIONS,
                metric=Metric.TRANSACTION_AMOUNT,
                aggregation=Aggregation.SUM,
                filters=self._txn_filters(q, txn_type, "SELECTION"),
                range_spec=self._date_spec(q, today),
                group_by=[],
                limit=10,
            )

        # ----- transactions above/below an amount (BEFORE generic listing) -----------
        min_amount, max_amount = None, None
        m = re.search(r"(?:above|over|more than|greater than)\s*[₹]?\s*([\d,]+)", q)
        if m:
            min_amount = float(m.group(1).replace(",", ""))
        m = re.search(r"(?:below|under|less than)\s*[₹]?\s*([\d,]+)", q)
        if m:
            max_amount = float(m.group(1).replace(",", ""))

        if (min_amount or max_amount) and re.search(r"show|list|which|find", q):
            return self._validated(
                intent=Intent.TRANSACTION_LIST,
                metric=Metric.TRANSACTION_AMOUNT,
                aggregation=Aggregation.NONE,
                filters=self._txn_filters(q, txn_type, desc, min_amount, max_amount),
                range_spec=self._date_spec(q, today),
                limit=20,
            )

        # ----- "show (all my) transactions" — a listing, not a summary ---------------
        if re.search(r"show (all )?(my )?transactions|list transactions|transactions from", q):
            return self._validated(
                intent=Intent.TRANSACTION_LIST,
                metric=Metric.TRANSACTION_AMOUNT,
                aggregation=Aggregation.NONE,
                filters=self._txn_filters(q, txn_type, desc),
                range_spec=self._date_spec(q, today),
                limit=20,
            )

        # ----- "which bank contributed the most" — group by bank ----------------------
        if re.search(r"which bank|which account", q) and re.search(
                r"contribut|spend|spent|paid|received|most", q):
            # follow-up in a multi-turn thread: inherit the previous type
            if txn_type is None and context.get("last_filters", {}).get("transaction_type"):
                txn_type = context["last_filters"]["transaction_type"]
            return self._validated(
                intent=Intent.TRANSACTION_SUMMARY,
                metric=Metric.TRANSACTION_AMOUNT,
                aggregation=Aggregation.SUM,
                filters=self._txn_filters(q, txn_type, desc),
                range_spec=self._date_spec(q, today),
                group_by=["bank"],
                limit=10,
            )

        # ----- comparison follow-up -----------------------------------------------------
        if re.search(r"compare|versus|\bvs\b|month before|previous month|what about", q) \
           and context.get("last_intent") in (
                "transaction_summary", "monthly_trend", "comparison"):
            metric = Metric.TRANSACTION_AMOUNT
            if context.get("last_metric") == "transaction_count":
                metric = Metric.TRANSACTION_COUNT
            base_range = context.get("last_date_range") or {"type": "calendar_month"}
            return self._validated(
                intent=Intent.COMPARISON,
                metric=metric,
                aggregation=Aggregation.SUM if metric == Metric.TRANSACTION_AMOUNT
                else Aggregation.COUNT,
                filters=context.get("last_filters") or {},
                range_spec=base_range,
                comparison={"against": "previous_period"},
            )

        # ----- generic summary — only if this actually looks like a finance question.
        # Unmatched chit-chat used to fall through to "sum all transactions last
        # month" (e.g. "what is your name" → a crore figure). Refuse instead.
        if not self._is_transaction_question(q, txn_type, desc, min_amount, max_amount):
            return self._off_topic_refusal()

        return self._validated(
            intent=Intent.TRANSACTION_SUMMARY,
            metric=Metric.TRANSACTION_AMOUNT,
            aggregation=Aggregation.SUM,
            filters=self._txn_filters(q, txn_type, desc),
            range_spec=self._date_spec(q, today),
        )

    # ----- helpers ---------------------------------------------------------------

    def _off_topic_refusal(self) -> QueryUnderstanding:
        return QueryUnderstanding(
            refusal_reason="unsupported",
            refusal_message=(
                "I'm Artha, a finance assistant for your bank accounts and "
                "transactions. I can only answer questions the current dataset "
                "supports — balances, spend, inflows, and transaction lookups."
            ),
            suggestions=list(_FINANCE_SUGGESTIONS),
            provider_used=self.name,
        )

    def _is_transaction_question(
        self,
        q: str,
        txn_type: str | None,
        desc: str | None,
        min_amount: float | None,
        max_amount: float | None,
    ) -> bool:
        if txn_type or desc or min_amount is not None or max_amount is not None:
            return True
        return bool(_TXN_QUESTION.search(q))

    def _extract_bank(self, q: str) -> str | None:
        return _extract_bank(q)

    def _txn_filters(self, q: str, txn_type: str | None, desc: str | None,
                     min_amount: float | None = None,
                     max_amount: float | None = None) -> dict:
        filters: dict = {}
        bank = self._extract_bank(q)
        if bank:
            filters["bank_code"] = bank
        if txn_type:
            filters["transaction_type"] = txn_type
        if desc:
            filters["description_contains"] = desc
        if min_amount is not None:
            filters["min_amount"] = min_amount
        if max_amount is not None:
            filters["max_amount"] = max_amount
        return filters

    def _bank_filter(self, q: str) -> dict:
        bank = self._extract_bank(q)
        return {"bank_code": bank} if bank else {}

    def _date_spec(self, q: str, today: dt.date) -> dict:
        # Explicit dates first.
        m = re.search(r"between (\d{4}-\d{2}-\d{2}) and (\d{4}-\d{2}-\d{2})", q)
        if m:
            return {"type": "custom", "start": m.group(1), "end": m.group(2)}
        m = re.search(r"from (\w+ \d{1,2}) to (\w+ \d{1,2})", q)
        if m:
            try:
                s = dt.datetime.strptime(m.group(1), "%B %d")
                e = dt.datetime.strptime(m.group(2), "%B %d")
                year = today.year
                s = s.replace(year=year)
                e = e.replace(year=year)
                if s > e:
                    year -= 1
                    s = s.replace(year=year)
                return {"type": "custom", "start": s.date().isoformat(),
                        "end": e.date().isoformat()}
            except ValueError:
                pass

        # Named months ("in june", "august 2026", "june 2025").
        m = re.search(r"\b([a-z]+)\s+(\d{4})\b", q)
        if m and m.group(1) in _MONTH_NAMES and int(m.group(2)) <= today.year:
            return {"type": "calendar_month", "month": m.group(1),
                    "year": int(m.group(2))}
        for word in re.findall(r"[a-z]+", q):
            if word in _MONTH_NAMES:
                return {"type": "calendar_month", "month": word}

        if re.search(r"yesterday", q):
            return {"type": "yesterday"}
        if re.search(r"today", q):
            return {"type": "today"}
        if re.search(r"last week|past week|previous week", q):
            return {"type": "last_week"}
        if re.search(r"this week|current week", q):
            return {"type": "this_week"}
        if re.search(r"last (\d+) days?|past (\d+) days?", q):
            m = re.search(r"last (\d+) days?|past (\d+) days?", q)
            return {"type": "last_n_days", "n_days": int(m.group(1) or m.group(2))}
        if re.search(r"last (\d+) months?|past (\d+) months?", q):
            m = re.search(r"last (\d+) months?|past (\d+) months?", q)
            return {"type": "last_n_months", "n_months": int(m.group(1) or m.group(2))}
        if re.search(r"month before|two months ago|prior.*month", q):
            return {"type": "month_before_previous"}
        if re.search(r"this month|current month", q):
            return {"type": "this_month"}
        if re.search(r"this year|ytd", q):
            return {"type": "this_year"}
        if re.search(r"last year", q):
            return {"type": "last_year"}
        if re.search(r"all time|ever", q):
            return {"type": "all_time"}
        # default: last completed month
        return {"type": "calendar_month"}

    def _extract_reference(self, q: str) -> str | None:
        m = re.search(
            r"(?:reference|ref no|ref number|ref id)\s*(?:is|:|#)?\s*([A-Za-z0-9]{5,64})", q
        )
        if m:
            return m.group(1)
        m = re.search(r"\b(S\d{6,10}|\d{9,12})\b", q)
        return m.group(1) if m else None

    def _after_keyword(self, q: str, keyword: str) -> str | None:
        m = re.search(rf"{keyword}\s*(?:is|:|#|number)?\s*([A-Za-z0-9+=/]{{8,}})",
                      q, re.IGNORECASE)
        return m.group(1) if m else None

    def _lookup(self, filters: dict, today: dt.date) -> QueryUnderstanding:
        return self._validated(
            intent=Intent.REFERENCE_LOOKUP,
            metric=Metric.TRANSACTION_COUNT,
            aggregation=Aggregation.NONE,
            filters=filters,
            range_spec={"type": "all_time"},
        )

    def _validated(
        self, intent: Intent, metric: Metric, aggregation: Aggregation,
        filters: dict, range_spec: dict, group_by: list[str] | None = None,
        comparison: dict | None = None, limit: int | None = None,
    ) -> QueryUnderstanding:
        """Build the query payload, resolve dates, and pre-validate against
        the Pydantic schema — catching rule bugs at provider time, not
        execution time."""
        try:
            dr = resolve_date_range(app_today(), range_spec)
        except Exception:
            dr = resolve_date_range(app_today(), {"type": "calendar_month"})
        payload = {
            "intent": intent.value,
            "metric": metric.value,
            "aggregation": aggregation.value,
            "filters": filters,
            "date_range": dr.model_dump(mode="json", exclude_none=True),
            "group_by": group_by or [],
        }
        if limit is not None:
            payload["limit"] = limit
        if comparison:
            payload["comparison"] = comparison
        try:
            FinancialQuery.model_validate(payload)
        except Exception as e:
            return QueryUnderstanding(
                refusal_reason="invalid",
                refusal_message=f"I understood the question but couldn't build a valid query ({type(e).__name__}).",
                provider_used=self.name,
            )
        return QueryUnderstanding(query=payload, provider_used=self.name)


# ---------------------------------------------------------------------------
# Anthropic provider (loaded lazily so the module imports without the SDK)
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are the query-understanding module of a finance assistant.
Convert the user's question into a single structured financial query, as strict JSON.

The dataset has exactly three tables: bank (bank_code, bank_name), account
(account_id, account_number [sensitive], program_id, available_balance,
bank_code), and transaction (transaction_id, account_id, transaction_date,
transaction_type [credit|debit], description, transaction_amount,
transaction_reference_id, utr_number [sensitive]).

There is NO vendor master / payouts / reconciliation table. Vendor or supplier
*names* appear inside transaction.description. Treat "vendor expenses",
"supplier spend", or "payouts" as debit transaction spend (transaction_type
"debit") for the asked date range. For "break down by vendor" / "top vendors",
use intent transaction_list with transaction_type debit (descriptions are the
counterparty). Do NOT refuse those questions as unsupported.

Allowed intents: transaction_summary, transaction_list, top_descriptions,
monthly_trend, comparison, account_balance, account_list, bank_balance,
bank_account_count, reference_lookup.

Rules:
- If the question is identity, greeting, or unrelated to this financial
  dataset (e.g. "what is your name", "who are you", "hello", weather),
  refuse: {"refusal": "unsupported", "message": "I'm Artha, a finance
  assistant for your bank accounts and transactions. I can only answer
  questions the current dataset supports."}.
- If the question needs data outside these tables (payroll, taxes, invoices,
  vendor master / amounts owed to vendors, profit, forecasts), refuse:
  {"refusal": "unsupported", "message": "..."}.
- If the user says "spent" with no qualifier, that maps to transaction_type
  "debit" — do NOT ask for clarification; state the interpretation.
- If the question is missing the subject entirely (e.g. "how much moved?"),
  refuse: {"refusal": "ambiguous", "message": "..."}.
- Dates: NEVER compute dates yourself. Emit a date_range TYPE and, when the
  user names a month, set "month" (e.g. "august") and optionally "year".
  The backend resolves everything to absolute dates.
  Types: calendar_month (for named/last months), this_month, last_month ->
  also calendar_month, this_week, last_week, last_n_days (set n_days),
  last_n_months (set n_months), yesterday, today, this_year, last_year,
  custom (user gave explicit YYYY-MM-DD dates), all_time.
- transaction_reference_id and utr_number are DIFFERENT columns. A bare
  "reference number" means transaction_reference_id. Only utr_number when the
  user explicitly says "UTR".
- "spent"/"debit"/"expenses" → transaction_type "debit"; "received"/"inflow" → "credit".
- Balance questions → intent account_balance (or bank_balance with
  group_by ["bank"] for "which bank holds the most", ["account"] for highest
  account) with metric "balance", aggregation "sum" (or "max" for highest).
  NEVER filter transactions for balance questions — balance lives on account.
- Descriptions: use filters.description_contains with the counterparty text
  (e.g. "SELECTION ELECTRONICS"), UPPERCASE.
- Amount thresholds: filters.min_amount / filters.max_amount (numbers).
- For "which month had the highest X": intent monthly_trend,
  group_by ["month"], aggregation sum, range last_n_months 12.
- For comparisons ("what about July", "how does that compare"), use intent
  "comparison" with "comparison": {"against": "previous_period"}, reusing
  metric/type from the previous turn given in the context.
- For two named months ("July vs August", "compare expenses in july versus
  august"): intent "comparison", date_range.month = first month, and
  comparison = {"against": "named_month", "month": "<second month>"}.
  Do NOT use previous_period for that case.
- Output ONLY JSON. No prose, no markdown fences.

Example — "What are the expenses for july for vendors":
{
  "intent": "transaction_summary",
  "metric": "transaction_amount",
  "aggregation": "sum",
  "filters": {"transaction_type": "debit"},
  "date_range": {"type": "calendar_month", "month": "july"},
  "group_by": []
}

Example — "compare my expense in july vs august":
{
  "intent": "comparison",
  "metric": "transaction_amount",
  "aggregation": "sum",
  "filters": {"transaction_type": "debit"},
  "date_range": {"type": "calendar_month", "month": "july"},
  "comparison": {"against": "named_month", "month": "august"},
  "group_by": []
}

Example — "What is my total available balance?":
{
  "intent": "account_balance",
  "metric": "balance",
  "aggregation": "sum",
  "filters": {},
  "date_range": {"type": "all_time"},
  "group_by": []
}

IMPORTANT field names:
- metric must be exactly one of: transaction_amount, transaction_count, balance
  (never "amount")
- date_range is a TOP-LEVEL object (never nested under filters)
- filters may only contain: bank_code, bank_name, account_id, transaction_type,
  description_contains, reference_id, utr_number, min_amount, max_amount
"""

QUERY_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "refusal": {"type": "string", "enum": ["unsupported", "ambiguous"]},
        "message": {"type": "string"},
        "intent": {"type": "string", "enum": [i.value for i in Intent]},
        "metric": {"type": "string", "enum": [m.value for m in Metric]},
        "aggregation": {
            "type": "string",
            "enum": [a.value for a in Aggregation if a != Aggregation.NONE],
        },
        "filters": {
            "type": "object",
            "properties": {
                "bank_code": {"type": "string"},
                "bank_name": {"type": "string"},
                "account_id": {"type": "string"},
                "transaction_type": {"type": "string", "enum": ["credit", "debit"]},
                "description_contains": {"type": "string"},
                "reference_id": {"type": "string"},
                "utr_number": {"type": "string"},
                "min_amount": {"type": "number"},
                "max_amount": {"type": "number"},
            },
            "additionalProperties": False,
        },
        "date_range": {
            "type": "object",
            "properties": {
                "type": {
                    "type": "string",
                    "enum": [d.value for d in __import__(
                        "app.schemas.query", fromlist=["DateRangeType"]
                    ).DateRangeType],
                },
                "n_months": {"type": "integer"},
                "n_days": {"type": "integer"},
                "month": {"type": "string"},
                "year": {"type": "integer"},
                "start": {"type": "string"},
                "end": {"type": "string"},
            },
            "additionalProperties": False,
        },
        "group_by": {
            "type": "array",
            "items": {"type": "string", "enum": ["bank", "account",
                                                 "transaction_type", "month"]},
        },
        "sort": {"type": "string", "enum": ["asc", "desc"]},
        "limit": {"type": "integer"},
        "comparison": {
            "type": "object",
            "properties": {
                "against": {
                    "type": "string",
                    "enum": [
                        "previous_period",
                        "previous_month",
                        "previous_year",
                        "named_month",
                    ],
                },
                "month": {"type": "string"},
                "year": {"type": "integer"},
            },
            "additionalProperties": False,
        },
    },
    "additionalProperties": False,
}


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(self, api_key: str, model: str, max_retries: int = 1, timeout: float = 30.0):
        import anthropic  # lazy import

        self.client = anthropic.Anthropic(
            api_key=api_key, timeout=timeout, max_retries=max_retries,
        )
        self.model = model

    def understand(self, question: str, context: dict | None = None) -> QueryUnderstanding:
        context = context or {}
        started = time.monotonic()

        context_block = ""
        if context and context.get("last_intent"):
            context_block = (
                "\nPrevious turn context (use for follow-ups like 'what about July' "
                "or 'how does that compare'):\n"
                + json.dumps(context, default=str)
            )

        messages = [
            {"role": "user", "content": f"{context_block}\n\nQuestion: {question}"},
        ]

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=1024,
                system=SYSTEM_PROMPT,
                messages=messages,
                output_config={"format": {"type": "json_schema", "schema": QUERY_JSON_SCHEMA}},
            )
        except Exception as e:  # network/auth errors — degrade to refusal, not crash
            return QueryUnderstanding(
                refusal_reason="unsupported",
                refusal_message=(
                    "The question-understanding service is temporarily unavailable. "
                    f"({type(e).__name__})"
                ),
                provider_used=self.name,
                latency_ms=int((time.monotonic() - started) * 1000),
            )

        text = next((b.text for b in response.content if b.type == "text"), "")
        latency = int((time.monotonic() - started) * 1000)

        try:
            data = json.loads(text)
        except Exception:
            return QueryUnderstanding(
                refusal_reason="unsupported",
                refusal_message="I couldn't interpret that question reliably.",
                provider_used=self.name,
                model_used=self.model,
                latency_ms=latency,
            )

        if data.get("refusal"):
            return QueryUnderstanding(
                refusal_reason=data["refusal"],
                refusal_message=data.get("message", "I can't answer that."),
                provider_used=self.name,
                model_used=self.model,
                latency_ms=latency,
            )

        return QueryUnderstanding(
            query=data,
            provider_used=self.name,
            model_used=self.model,
            latency_ms=latency,
        )


def _extract_json_object(text: str) -> dict:
    """Parse a JSON object from model output (tolerates markdown fences)."""
    text = (text or "").strip()
    if not text:
        raise ValueError("empty model response")
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start : end + 1])
        raise


class OllamaProvider(LLMProvider):
    """Local LLM via Ollama — generates FinancialQuery JSON (not SQL).

    Matches the architecture: LLM understands NL → structured query; DuckDB
    Text-to-SQL compiler turns that into SQL.
    """

    name = "ollama"

    def __init__(
        self,
        model: str,
        base_url: str | None = None,
        timeout: float = 60.0,
    ):
        self.model = model
        self.base_url = (base_url or "http://127.0.0.1:11434").rstrip("/")
        self.timeout = timeout

    def understand(self, question: str, context: dict | None = None) -> QueryUnderstanding:
        import urllib.error
        import urllib.request

        context = context or {}
        started = time.monotonic()

        context_block = ""
        if context and context.get("last_intent"):
            context_block = (
                "\nPrevious turn context (use for follow-ups):\n"
                + json.dumps(context, default=str)
            )

        user_content = (
            f"{context_block}\n\nQuestion: {question}\n\n"
            "Respond with ONLY a single JSON object matching the schema. "
            "No markdown, no explanation."
        )
        payload = {
            "model": self.model,
            "stream": False,
            "format": "json",
            "options": {"temperature": 0.0, "num_predict": 512},
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
        }
        req = urllib.request.Request(
            f"{self.base_url}/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            return QueryUnderstanding(
                refusal_reason="unsupported",
                refusal_message=(
                    "The local LLM (Ollama) is unavailable. "
                    f"Start Ollama and pull the model, or set ARTHA_LLM_PROVIDER=rule_based. "
                    f"({type(e).__name__}: {e})"
                ),
                provider_used=self.name,
                model_used=self.model,
                latency_ms=int((time.monotonic() - started) * 1000),
            )

        text = (body.get("message") or {}).get("content") or body.get("response") or ""
        latency = int((time.monotonic() - started) * 1000)

        try:
            data = _extract_json_object(text)
        except Exception:
            return QueryUnderstanding(
                refusal_reason="unsupported",
                refusal_message="I couldn't interpret that question reliably.",
                provider_used=self.name,
                model_used=self.model,
                latency_ms=latency,
            )

        if data.get("refusal"):
            return QueryUnderstanding(
                refusal_reason=data["refusal"],
                refusal_message=data.get("message", "I can't answer that."),
                provider_used=self.name,
                model_used=self.model,
                latency_ms=latency,
            )

        # Drop non-schema keys some models add
        data.pop("message", None)
        return QueryUnderstanding(
            query=data,
            provider_used=self.name,
            model_used=self.model,
            latency_ms=latency,
        )


def build_provider(
    provider_name: str, api_key: str, model: str,
    vendor_names: list[str] | None = None,
    max_retries: int = 1, timeout: float = 30.0,
    ollama_base_url: str | None = None,
) -> LLMProvider:
    if provider_name == "anthropic":
        return AnthropicProvider(api_key=api_key, model=model,
                                 max_retries=max_retries, timeout=timeout)
    if provider_name == "ollama":
        return OllamaProvider(
            model=model,
            base_url=ollama_base_url,
            timeout=timeout,
        )
    if provider_name == "rule_based":
        return RuleBasedProvider()
    raise ValueError(f"unknown provider: {provider_name}")
