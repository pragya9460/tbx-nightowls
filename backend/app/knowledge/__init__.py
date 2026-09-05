"""Semantic knowledge layer (RAG) — ChromaDB + local MiniLM embeddings.

Kept deliberately separate from the grounded financial engine: questions
here are answered from unstructured documents, not from MySQL. Sensitive
columns (account_number, utr_number) are masked at INGESTION time so a
vector search can never surface raw PII — same one-way-mask rule as the
query engine boundary.
"""
