from .entities import (  # noqa: F401
    Account,
    Bank,
    Transaction,
)

# Canonical enum values mirrored for the semantic layer / seed generator.
TRANSACTION_TYPES = ("credit", "debit")
