"""SQLAlchemy ORM entities — the TBX financial data model.

Three tables, exactly as in the authoritative schema (TBX - Database Schema.md):

    bank 1 ─── N account 1 ─── N transaction

Everything the assistant answers is grounded in these tables. There are no
vendor / payroll / invoice / reconciliation tables in the source data — the
semantic layer refuses questions that would require them.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Base


class Bank(Base):
    __tablename__ = "bank"

    bank_code: Mapped[str] = mapped_column(String(10), primary_key=True)
    bank_name: Mapped[str] = mapped_column(String(150), nullable=False)

    accounts: Mapped[list["Account"]] = relationship(back_populates="bank")


class Account(Base):
    __tablename__ = "account"

    account_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    entity_id: Mapped[str] = mapped_column(String(36), nullable=False)
    # Sensitive: mask before display/LLM — never selected raw into answers.
    account_number: Mapped[str] = mapped_column(String(20), nullable=False)
    program_id: Mapped[int] = mapped_column(Integer, nullable=False)
    available_balance: Mapped[float] = mapped_column(
        Numeric(15, 2), nullable=False, default=0.00
    )
    bank_code: Mapped[str] = mapped_column(
        String(10), ForeignKey("bank.bank_code"), nullable=False
    )

    bank: Mapped[Bank] = relationship(back_populates="accounts")
    transactions: Mapped[list["Transaction"]] = relationship(back_populates="account")

    __table_args__ = (
        Index("ix_account_bank", "bank_code"),
        Index("ix_account_entity", "entity_id"),
    )


class Transaction(Base):
    __tablename__ = "transaction"

    transaction_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    account_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("account.account_id"), nullable=False
    )
    # MySQL TIMESTAMP(6) in the real DDL; generic DateTime keeps tests on
    # SQLite working while the MySQL dialect renders TIMESTAMP(6).
    transaction_date: Mapped[dt.datetime] = mapped_column(DateTime, nullable=False)
    transaction_type: Mapped[str] = mapped_column(String(10), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    transaction_amount: Mapped[float] = mapped_column(
        Numeric(15, 2), nullable=False, default=0.00
    )
    # Plaintext, directly searchable.
    transaction_reference_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Sensitive: mask before display/LLM.
    utr_number: Mapped[str | None] = mapped_column(String(256), nullable=True)

    account: Mapped["Account"] = relationship(back_populates="transactions")

    __table_args__ = (
        CheckConstraint(transaction_type.in_(("credit", "debit")), name="ck_txn_type"),
        # Query-pattern indexes (see README "Scaling toward 20M records"): the
        # semantic layer only filters/groups on these columns, so only these
        # get indexes. No speculative indexes.
        Index("ix_txn_date", "transaction_date"),
        Index("ix_txn_account", "account_id"),
        Index("ix_txn_type", "transaction_type"),
        Index("ix_txn_reference", "transaction_reference_id"),
        Index("ix_txn_date_type", "transaction_date", "transaction_type"),
        Index("ix_txn_account_date", "account_id", "transaction_date"),
    )
