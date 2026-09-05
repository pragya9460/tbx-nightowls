"""Deterministic synthetic seed data matching the TBX schema exactly.

Reproducible via a fixed random seed (default 42). Generates:
  - 10 banks with canonical IFSC-prefix codes and formal names (from the
    authoritative schema's sample data)
  - 25 accounts across those banks (mixed balances, incl. negative overdrafts
    seen in production data)
  - 8,000 transactions over 12 months ending the last completed calendar
    month, with realistic NEFT/IMPS/UPI/FT description formats, plaintext
    transaction_reference_id values, and encrypted-looking UTR numbers.

The CSVs (data/*.csv) double as the format contract for loading the official
dataset later — see scripts/load_data.py.
"""
from __future__ import annotations

import datetime as dt
import random
import uuid
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Reference data (from TBX - Database Schema.md)
# ---------------------------------------------------------------------------

BANKS = [
    ("HDFC", "HDFC BANK LIMITED"),
    ("ICIC", "ICICI BANK LIMITED"),
    ("SBIN", "STATE BANK OF INDIA"),
    ("UTIB", "AXIS BANK LIMITED"),
    ("KKBK", "KOTAK MAHINDRA BANK LIMITED"),
    ("CNRB", "CANARA BANK"),
    ("UBIN", "UNION BANK OF INDIA"),
    ("AUBL", "AU SMALL FINANCE BANK LIMITED"),
    ("TMBL", "TAMILNAD MERCANTILE BANK LIMITED"),
    ("RATN", "RBL BANK LIMITED"),
]

# Counterparty names in the same convention as the production sample.
COUNTERPARTIES = [
    "SELECTION ELECTRONICS", "SELECTRICITY TWO PRIVATE LIMITED",
    "UMANG SELECTIONHAPURBPES DPF10129", "SELECTION MOBILE",
    "RELIANCEDIGITAL RETAIL LTD", "NAVYUG SELECTION",
    "PARESH VIKRANT GHASE", "GAUTAM SINGH", "SHREE TRADERS",
    "MEHTA ENTERPRISES", "GLOBAL LOGISTICS LLP", "SPARK FACILITY SERVICES",
    "VERTEX SOFTWARE PVT LTD", "ANAND TRANSPORT", "KIRANA SUPPLIERS",
    "OM SAI ENTERPRISES", "BLUE STAR COOLING", "PRIME CEMENT AGENCIES",
    "DESIRED SOFTWARE CO", "ZENITH MARKETING",
]

DESCRIPTION_TEMPLATES = [
    "FT -  {ref9} -  {acct} - {cp}   DAHISAR EAST",
    "UPI-{cp}-XXXXXX{acct4}-AUBL0002125-{ref12}",
    "NEFT  - {ifsc} - {ref8} - {acct9} - {cp}",
    "IMPS/P2A/{ref12}/UTIB/{acct15}/00/INET/{ref4}/{cp}/ZBFLCTP5L2PBL{ref8}/INWD48",
    "NEFT/{ref9}/{bank4}/{cp}",
    "IMPS OW/{ref12}/{person}/SBIN/{acct10}",
    "R/{ratnref}/ZBFLCTP405PBL{ref8}//{cp}/{ratnref} ",
    "UPI/CR/{ref12}/{bank4}/{acct15}/{cp}",
]

CHANNEL_PREFIXES = ["FT", "NEFT", "IMPS", "UPI", "RTGS"]


@dataclass
class SeedBundle:
    banks: list[dict]
    accounts: list[dict]
    transactions: list[dict]


def _utc_cipher(rng: random.Random) -> str:
    """Encrypted-looking UTR string (like the production sample)."""
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
    length = rng.choice((56, 64))
    return "".join(rng.choice(alphabet) for _ in range(length))


def generate(seed: int = 42, n_accounts: int = 25, n_transactions: int = 8000,
             start_date: dt.date | None = None,
             end_date: dt.date | None = None) -> SeedBundle:
    rng = random.Random(seed)
    today = dt.date.today()

    # 12-month window ending the last completed calendar month.
    if end_date is None:
        first_of_current = today.replace(day=1)
        end_date = first_of_current - dt.timedelta(days=1)
    if start_date is None:
        start_date = (end_date.replace(day=1) - dt.timedelta(days=365))

    total_days = (end_date - start_date).days

    banks = [
        {"bank_code": code, "bank_name": name}
        for code, name in BANKS
    ]

    accounts: list[dict] = []
    for i in range(n_accounts):
        code, _ = BANKS[i % len(BANKS)]
        balance = round(rng.uniform(-30_000_000, 250_000_000), 2)
        accounts.append({
            "account_id": str(uuid.UUID(int=rng.getrandbits(128), version=4)),
            "entity_id": str(uuid.UUID(int=rng.getrandbits(128))),
            "account_number": f"{rng.randint(2, 9)}{rng.randint(10**11, 10**12 - 1)}",
            "program_id": rng.choice([4, 21, 46]),
            "available_balance": balance,
            "bank_code": code,
        })

    transactions: list[dict] = []
    for i in range(n_transactions):
        acct = rng.choice(accounts)
        is_debit = rng.random() < 0.62
        day_offset = int(rng.betavariate(1.4, 1.0) * (total_days - 1)) \
            if rng.random() < 0.35 else rng.randint(0, total_days - 1)
        tdate = start_date + dt.timedelta(days=day_offset)
        hour = rng.randint(0, 23)
        minute = rng.randint(0, 59)
        second = rng.randint(0, 59)

        channel = rng.choice(CHANNEL_PREFIXES)
        cp = rng.choice(COUNTERPARTIES)
        bank4 = acct["bank_code"]
        acct_num = acct["account_number"]
        ref9 = str(rng.randint(10**8, 10**9 - 1))
        desc = (
            f"{channel} - {ref9} - {acct_num} - {cp}"
            if channel in ("FT", "NEFT")
            else f"{channel}/{cp}/{ref9}/{bank4}"
        )
        amount = round(rng.lognormvariate(9.2, 1.1), 2)  # skewed to small, long tail
        ref_id = str(rng.randint(10**9, 10**10 - 1)) if rng.random() < 0.9 else \
            f"S{rng.randint(10**6, 10**7 - 1)}"
        utr = _utc_cipher(rng) if rng.random() < 0.55 else None

        transactions.append({
            "transaction_id": str(uuid.UUID(int=rng.getrandbits(128))),
            "account_id": acct["account_id"],
            "transaction_date": dt.datetime(
                tdate.year, tdate.month, tdate.day, hour, minute, minute,
            ).isoformat(sep=" "),
            "transaction_type": "debit" if is_debit else "credit",
            "description": desc[:500],
            "transaction_amount": amount,
            "transaction_reference_id": ref_id,
            "utr_number": utr,
        })

    return SeedBundle(banks=banks, accounts=accounts, transactions=transactions)


def to_csv_rows(bundle: SeedBundle) -> dict[str, list[list]]:
    """Rows ready for CSV writing, in the load-order the loader expects."""
    banks = [[b["bank_code"], b["bank_name"]] for b in bundle.banks]
    accounts = [[
        a["account_id"], a["entity_id"], a["account_number"],
        a["program_id"], f"{a['available_balance']:.2f}", a["bank_code"],
    ] for a in bundle.accounts]
    transactions = [[
        t["transaction_id"], t["account_id"], t["transaction_date"],
        t["transaction_type"], t["description"],
        f"{t['transaction_amount']:.2f}",
        t["transaction_reference_id"] or "", t["utr_number"] or "",
    ] for t in bundle.transactions]
    return {
        "banks.csv": banks,
        "accounts.csv": accounts,
        "transactions.csv": transactions,
    }


CSV_COLUMNS = {
    "banks.csv": ["bank_code", "bank_name"],
    "accounts.csv": ["account_id", "entity_id", "account_number", "program_id",
                     "available_balance", "bank_code"],
    "transactions.csv": ["transaction_id", "account_id", "transaction_date",
                         "transaction_type", "description", "transaction_amount",
                         "transaction_reference_id", "utr_number"],
}
