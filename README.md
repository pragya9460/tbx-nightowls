# tbx-nightowls

The original synthetic banking CSVs are in [`dataset/`](dataset/).

An extended finance-assistant dataset is available in [`dataset/extended_v1/`](dataset/extended_v1/README.md). It preserves the 20,000 original transactions and adds explicitly synthetic vendors, invoices, payouts, and reconciliation scenarios, with 80 golden evaluation questions.

- [Ready-to-query SQLite database](dataset/extended_v1/finance.sqlite)
- [Extended CSV tables](dataset/extended_v1/csv/)
- [Golden questions and expected results](dataset/extended_v1/golden_cases.csv)
- [80 human-facing questions and expected chat answers](dataset/extended_v1/human_answers/golden_human_answers.md)
- [Business data dictionary](dataset/extended_v1/DATA_DICTIONARY.md)
- [Problem-statement coverage and limits](dataset/extended_v1/COVERAGE.md)

Run `python dataset/extended_v1/validate.py` to validate the packaged data and replay the reference queries. The package passed 171 checks and 65 SQL case replays; this does not measure assistant accuracy or performance at 20 million rows.
