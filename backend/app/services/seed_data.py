"""Deterministic synthetic seed data for development.

Reproducible via a fixed random seed. Replace with the official hackathon
dataset later: the generator writes CSVs into data/, and scripts/load_data.py
ingests any CSVs with the same column names — synthetic or official.
"""
from __future__ import annotations

import datetime as dt
import random
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Reference data
# ---------------------------------------------------------------------------

VENDOR_CATEGORIES = [
    "Raw Materials", "Logistics", "IT Services", "Professional Services",
    "Facilities", "Marketing", "Travel", "Manufacturing", "Utilities",
    "Packaging",
]

VENDOR_NAMES = [
    ("ABC Suppliers", "Raw Materials"), ("Acme Industrial Ltd", "Manufacturing"),
    ("Bharat Logistics Pvt Ltd", "Logistics"), ("CloudNine Systems", "IT Services"),
    ("Delhi Steel Works", "Raw Materials"), ("Elite Movers", "Logistics"),
    ("Falcon Consulting", "Professional Services"), ("GreenBuild Corp", "Facilities"),
    ("Hitech Solutions", "IT Services"), ("Indus Packaging", "Packaging"),
    ("Jupiter Marketing", "Marketing"), ("Kaveri Agro", "Raw Materials"),
    ("Lumina Power", "Utilities"), ("Metro Transport", "Logistics"),
    ("Nova Print Media", "Marketing"), ("Orbit Software Labs", "IT Services"),
    ("Precision Tooling Co", "Manufacturing"), ("Quantum Analytics", "Professional Services"),
    ("Rajesh Hardware", "Raw Materials"), ("Sunrise Interiors", "Facilities"),
    ("Triveni Chemicals", "Raw Materials"), ("United Freight", "Logistics"),
    ("Vista Ad Agency", "Marketing"), ("Wavelength Telecom", "IT Services"),
    ("Xpress Courier", "Logistics"), ("Yamuna Textiles", "Manufacturing"),
    ("Zenith Security", "Facilities"), ("Aravalli Cements", "Raw Materials"),
    ("Blue Ocean Traders", "Professional Services"), ("Chennai Auto Parts", "Manufacturing"),
    ("Deccan Foods", "Raw Materials"), ("Everest Electricals", "Utilities"),
    ("First Mile Logistics", "Logistics"), ("Ganga Paper Mills", "Packaging"),
    ("Himalaya Travel Desk", "Travel"), ("Infotech Staffing", "Professional Services"),
    ("Jetset Corporate Travel", "Travel"), ("Kiran Stationers", "Facilities"),
    ("Lotus Facility Care", "Facilities"), ("Meridian Legal Advisors", "Professional Services"),
]

CATEGORY_ACCOUNTS = {
    "Raw Materials": "Purchases",
    "Logistics": "Operations",
    "IT Services": "Technology",
    "Professional Services": "Admin & Professional",
    "Facilities": "Operations",
    "Marketing": "Marketing",
    "Travel": "Travel",
    "Manufacturing": "Purchases",
    "Utilities": "Utilities",
    "Packaging": "Purchases",
}

ACCOUNTS = [
    "Purchases", "Operations", "Technology", "Admin & Professional",
    "Marketing", "Travel", "Utilities", "Bank Charges",
]

NON_VENDOR_CATEGORIES = [
    "Bank Charges", "Office Supplies", "Salaries & Benefits", "Rent",
]

TXN_DESCRIPTIONS = [
    "Invoice payment", "Quarterly retainer", "Monthly service charge",
    "Bulk order advance", "Final settlement", "Repair and maintenance",
    "Subscription renewal", "Freight charges", "Utility bill", "Annual contract",
]


@dataclass
class SeedBundle:
    vendors: list[dict]
    transactions: list[dict]
    payouts: list[dict]
    reconciliation: list[dict]


def generate(seed: int = 42, n_vendors: int = 40, n_transactions: int = 8000,
             start_date: dt.date | None = None,
             end_date: dt.date | None = None) -> SeedBundle:
    """Generate a deterministic SeedBundle.

    Dates span 12 months ending the last day of the month before today
    (so "last month" queries always have data), by default.
    """
    rng = random.Random(seed)

    today = dt.date.today()
    if end_date is None:
        # last completed month's final day
        first_of_current = today.replace(day=1)
        end_date = first_of_current - dt.timedelta(days=1)
    if start_date is None:
        # go back 12 full months from end_date
        y, m = end_date.year, end_date.month
        for _ in range(12):
            m -= 1
            if m == 0:
                m = 12
                y -= 1
        start_date = dt.date(y, m, 1)
    total_days = (end_date - start_date).days + 1

    # --- vendors -----------------------------------------------------------
    vendors = [
        {
            "vendor_id": f"V{i + 1:04d}",
            "vendor_name": name,
            "category": category,
        }
        for i, (name, category) in enumerate(VENDOR_NAMES[:n_vendors])
    ]
    vendor_ids = [v["vendor_id"] for v in vendors]
    vendor_weights = [rng.random() for _ in vendor_ids]

    # --- transactions -------------------------------------------------------
    transactions: list[dict] = []
    recon_rows: list[dict] = []
    for i in range(n_transactions):
        # Uniform spread across the whole window; a slight uptick toward
        # recent months keeps the demo's "last month" questions meaningful.
        if rng.random() < 0.35:
            day_offset = int(rng.betavariate(1.4, 1.0) * (total_days - 1))
        else:
            day_offset = rng.randint(0, total_days - 1)
        date = start_date + dt.timedelta(days=day_offset)

        # ~78% of transactions are vendor debits, rest are misc expenses/credits
        is_vendor_txn = rng.random() < 0.78
        vendor_id = None
        category = None
        account = None
        amount = None
        txn_type = "debit"

        if is_vendor_txn:
            vendor_id = rng.choices(vendor_ids, weights=vendor_weights)[0]
            category = next(v["category"] for v in vendors if v["vendor_id"] == vendor_id)
            account = CATEGORY_ACCOUNTS.get(category, "Operations")
            # lognormal-ish amount in INR
            amount = round(min(max(rng.lognormvariate(10.2, 0.9), 500.0), 2_500_000.0), 2)
        else:
            category = rng.choice(NON_VENDOR_CATEGORIES)
            account = CATEGORY_ACCOUNTS.get(category, "Bank Charges")
            if category == "Bank Charges":
                amount = round(rng.uniform(50, 3000), 2)
            elif category in ("Rent", "Salaries & Benefits"):
                amount = round(rng.uniform(150_000, 900_000), 2)
            else:
                amount = round(rng.uniform(1_000, 150_000), 2)
            if rng.random() < 0.04:
                txn_type = "credit"  # e.g. refunds/adjustments

        rec = rng.random()
        if rec < 0.72:
            recon_status = "reconciled"
        elif rec < 0.9:
            recon_status = "unreconciled"
        else:
            recon_status = "pending"

        txn = {
            "transaction_id": f"TXN{i + 1:06d}",
            "transaction_date": date.isoformat(),
            "vendor_id": vendor_id,
            "amount": amount,
            "category": category,
            "account": account,
            "transaction_type": txn_type,
            "reconciliation_status": recon_status,
            "description": rng.choice(TXN_DESCRIPTIONS),
            "currency": "INR",
        }
        transactions.append(txn)

        recon_rows.append({
            "reconciliation_id": f"REC{i + 1:06d}",
            "transaction_id": txn["transaction_id"],
            "status": recon_status,
            "reconciled_date": (
                (date + dt.timedelta(days=rng.randint(1, 20))).isoformat()
                if recon_status == "reconciled" else ""
            ),
        })

    # --- vendor payouts -----------------------------------------------------
    # Payouts settle a subset of vendor transactions, shifted 1–15 days later.
    vendor_txn_indices = [
        i for i, t in enumerate(transactions)
        if t["vendor_id"] and t["transaction_type"] == "debit"
    ]
    payout_candidates = rng.sample(
        vendor_txn_indices, k=min(len(vendor_txn_indices), int(n_transactions * 0.18))
    )
    payouts: list[dict] = []
    for j, idx in enumerate(sorted(payout_candidates)):
        t = transactions[idx]
        payout_date = dt.date.fromisoformat(t["transaction_date"]) + dt.timedelta(
            days=rng.randint(1, 15)
        )
        if payout_date > end_date:
            payout_date = end_date
        ps = rng.random()
        status = "paid" if ps < 0.85 else ("pending" if ps < 0.96 else "failed")
        payouts.append({
            "payout_id": f"PO{j + 1:06d}",
            "payout_date": payout_date.isoformat(),
            "vendor_id": t["vendor_id"],
            "amount": t["amount"],
            "status": status,
            "transaction_id": t["transaction_id"],
            "currency": "INR",
        })

    return SeedBundle(
        vendors=vendors,
        transactions=transactions,
        payouts=payouts,
        reconciliation=recon_rows,
    )


CSV_COLUMNS = {
    "vendors.csv": [
        "vendor_id", "vendor_name", "category",
    ],
    "transactions.csv": [
        "transaction_id", "transaction_date", "vendor_id", "amount", "category",
        "account", "transaction_type", "reconciliation_status", "description",
        "currency",
    ],
    "vendor_payouts.csv": [
        "payout_id", "payout_date", "vendor_id", "amount", "status",
        "transaction_id", "currency",
    ],
    "reconciliation.csv": [
        "reconciliation_id", "transaction_id", "status", "reconciled_date",
    ],
}


def to_csv_rows(bundle: SeedBundle) -> dict[str, list[list[str]]]:
    """Rows in the documented CSV column order (for the data/ folder)."""
    out: dict[str, list[list[str]]] = {}
    for fname, cols in CSV_COLUMNS.items():
        src = {
            "vendors.csv": bundle.vendors,
            "transactions.csv": bundle.transactions,
            "vendor_payouts.csv": bundle.payouts,
            "reconciliation.csv": bundle.reconciliation,
        }[fname]
        rows = []
        for rec in src:
            rows.append([str(rec.get(c, "") or "") for c in cols])
        out[fname] = rows
    return out
