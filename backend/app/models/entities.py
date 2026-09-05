"""SQLAlchemy ORM entities — the financial data model."""
from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Base

# Single fictitious company / single currency for the MVP.
COMPANY_CURRENCY = "INR"


class Vendor(Base):
    __tablename__ = "vendors"

    vendor_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    vendor_name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    category: Mapped[str] = mapped_column(String(64), nullable=False)

    transactions: Mapped[list["Transaction"]] = relationship(back_populates="vendor")
    payouts: Mapped[list["VendorPayout"]] = relationship(back_populates="vendor")


class Transaction(Base):
    __tablename__ = "transactions"

    transaction_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    transaction_date: Mapped[dt.date] = mapped_column(Date, nullable=False)
    vendor_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("vendors.vendor_id"), nullable=True
    )
    amount: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False)
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    account: Mapped[str] = mapped_column(String(64), nullable=False)
    transaction_type: Mapped[str] = mapped_column(String(32), nullable=False)
    reconciliation_status: Mapped[str] = mapped_column(String(32), nullable=False)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    currency: Mapped[str] = mapped_column(String(8), default=COMPANY_CURRENCY)

    vendor: Mapped[Vendor | None] = relationship(back_populates="transactions")
    reconciliation: Mapped["Reconciliation | None"] = relationship(
        back_populates="transaction"
    )

    __table_args__ = (
        CheckConstraint(
            transaction_type.in_(("debit", "credit")), name="ck_txn_type"
        ),
        CheckConstraint(
            reconciliation_status.in_(("reconciled", "unreconciled", "pending")),
            name="ck_txn_rec_status",
        ),
        CheckConstraint(amount >= 0, name="ck_txn_amount_nonneg"),
        Index("ix_txn_date", "transaction_date"),
        Index("ix_txn_vendor", "vendor_id"),
        Index("ix_txn_status", "reconciliation_status"),
        Index("ix_txn_category", "category"),
        Index("ix_txn_date_status", "transaction_date", "reconciliation_status"),
    )


class VendorPayout(Base):
    __tablename__ = "vendor_payouts"

    payout_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    payout_date: Mapped[dt.date] = mapped_column(Date, nullable=False)
    vendor_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("vendors.vendor_id"), nullable=False
    )
    amount: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    transaction_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("transactions.transaction_id"), nullable=True
    )
    currency: Mapped[str] = mapped_column(String(8), default=COMPANY_CURRENCY)

    vendor: Mapped[Vendor] = relationship(back_populates="payouts")
    transaction: Mapped[Transaction | None] = relationship()

    __table_args__ = (
        CheckConstraint(
            status.in_(("paid", "pending", "failed")), name="ck_payout_status"
        ),
        CheckConstraint(amount >= 0, name="ck_payout_amount_nonneg"),
        Index("ix_payout_date", "payout_date"),
        Index("ix_payout_vendor", "vendor_id"),
        Index("ix_payout_status", "status"),
        Index("ix_payout_vendor_date", "vendor_id", "payout_date"),
    )


class Reconciliation(Base):
    __tablename__ = "reconciliation"

    reconciliation_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    transaction_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("transactions.transaction_id"), nullable=False, unique=True
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    reconciled_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True)

    transaction: Mapped[Transaction] = relationship(back_populates="reconciliation")

    __table_args__ = (
        CheckConstraint(
            status.in_(("reconciled", "unreconciled", "pending")), name="ck_rec_status"
        ),
        Index("ix_rec_status", "status"),
        Index("ix_rec_txn", "transaction_id"),
    )


# Canonical enum values mirrored for the semantic layer / seed generator.
class TransactionType:
    DEBIT = "debit"
    CREDIT = "credit"


class ReconciliationStatus:
    RECONCILED = "reconciled"
    UNRECONCILED = "unreconciled"
    PENDING = "pending"


class PayoutStatus:
    PAID = "paid"
    PENDING = "pending"
    FAILED = "failed"


def utc_now() -> dt.datetime:
    return dt.datetime.now(tz=dt.timezone.utc)


def server_default_now():
    return func.now()
