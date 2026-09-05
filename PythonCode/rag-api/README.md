# RAG API

A dual-mode question-answering API built with **FastAPI**, **LangChain**, **ChromaDB**, and **Anthropic Claude**.

The API automatically routes every question to the right engine:

| Question type | Engine | How it works |
|---|---|---|
| Aggregation / analytics | **Text-to-SQL** | CSVs → SQLite → LLM generates SQL → executes → narrates answer |
| Contextual / semantic | **RAG** | Documents → ChromaDB vector search → LLM answers from retrieved context |

---

## Features

- **Smart auto-router** — detects whether a question needs aggregation (SUM, MAX, GROUP BY …) or semantic search and picks the right engine automatically, with an option to force either mode
- **Text-to-SQL analytics** — loads CSV files into an in-memory SQLite database at startup; the LLM writes precise SQL, which is executed and the results are narrated back as a natural-language answer
- **RAG pipeline** — documents are chunked, embedded with `sentence-transformers/all-MiniLM-L6-v2`, stored in ChromaDB, and retrieved by cosine similarity before being passed to Claude
- **Multi-format ingestion** — PDF, TXT, MD, DOCX, DOC, and CSV via REST upload or drop-in to the `data/` folder
- **Interactive API docs** — Scalar UI at `/scalar`, Swagger at `/documentation`

---

## Project Structure

```
rag-api/
├── data/                   # Drop source files here
│   └── chroma/             # Persisted ChromaDB vector store
├── src/
│   ├── __init__.py
│   ├── config.py           # Pydantic settings (reads .env)
│   ├── database.py         # ChromaDB client + collection management
│   ├── ingestion.py        # Document loading, CSV parsing, chunking
│   ├── retriever.py        # Similarity search + RAG chain
│   └── analytics.py        # Text-to-SQL engine (SQLite + Claude)
├── main.py                 # FastAPI app, routing logic, all endpoints
├── requirements.txt
└── .env                    # API keys and runtime settings
```

---

## Quick Start

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure environment

Edit `.env` and fill in your API keys:

```env
ANTHROPIC_API_KEY=sk-ant-...
ANTHROPIC_MODEL=claude-haiku-4-5-20251001
```

All other settings have working defaults — see the [Configuration](#configuration) section.

### 3. Add data files

Drop any supported files into `data/`:

```
data/
├── account.csv          # structured data — goes to Text-to-SQL
├── transaction.csv
├── bank.csv
├── policy.pdf           # unstructured docs — goes to RAG
└── notes.md
```

> **CSV files** are loaded directly into the analytics engine (SQLite). Each CSV becomes a table; each row becomes a searchable document in the vector store as well.

### 4. Start the server

```bash
# Development (auto-reload)
uvicorn main:app --reload

# Production
uvicorn main:app --host 0.0.0.0 --port 8000
```

On startup the server will:
1. Create the `data/` directory if it doesn't exist
2. Load all CSVs in `data/` into the in-memory SQLite analytics database

### 5. Ingest documents into the vector store

```bash
curl -X POST http://localhost:8000/ingest
```

This only needs to be run once (or whenever new files are added). The analytics engine picks up CSVs automatically on every restart.

---

## Usage Examples

### Analytics query (auto-detected)

```bash
curl -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{"question": "What is the maximum spend by account_id?"}'
```

```json
{
  "answer": "The account with the highest single spend is 032ec225-… at $4,945,474.41 …",
  "query_type": "analytics",
  "sql": "SELECT account_id, MAX(ABS(transaction_amount)) AS maximum_spend FROM tbl_transaction WHERE transaction_amount < 0 GROUP BY account_id LIMIT 100;",
  "row_count": 100,
  "data": [
    {"account_id": "032ec225-...", "maximum_spend": 4945474.41},
    ...
  ],
  "sources": []
}
```

### Semantic query (auto-detected)

```bash
curl -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{"question": "What is a UTR number?"}'
```

```json
{
  "answer": "A UTR (Unique Transaction Reference) number is …",
  "query_type": "semantic",
  "sources": [
    {"content": "…", "metadata": {"source": "policy.pdf"}, "similarity": 0.61}
  ],
  "sql": null,
  "data": null
}
```

### Force a specific engine

Set `query_type` to `"analytics"` or `"semantic"` to bypass auto-detection:

```bash
curl -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{"question": "List all HDFC transactions", "query_type": "analytics"}'
```

### Upload and ingest a file

```bash
curl -X POST http://localhost:8000/ingest/upload \
  -F "files=@/path/to/report.pdf"
```

Supported types: `.pdf` `.txt` `.md` `.docx` `.doc` `.csv`

### Similarity search without LLM

```bash
curl -X POST http://localhost:8000/search \
  -H "Content-Type: application/json" \
  -d '{"question": "ATM withdrawal", "top_k": 3}'
```

---

## API Reference

### Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Liveness check — returns version |
| `POST` | `/query` | **Unified query endpoint** (auto-routes to analytics or RAG) |
| `POST` | `/search` | Raw vector similarity search, no LLM |
| `POST` | `/ingest` | Ingest all files in `data/` into the vector store |
| `POST` | `/ingest/upload` | Upload files and ingest them |
| `POST` | `/ingest/text` | Ingest raw strings with metadata |
| `DELETE` | `/collection/{name}` | Delete and recreate a named collection |
| `GET` | `/collections` | List all ChromaDB collections |
| `GET` | `/scalar` | Interactive Scalar API docs |
| `GET` | `/documentation` | Swagger UI |

### `POST /query` — request

```json
{
  "question":   "What is the total credit per account?",
  "query_type": "auto",
  "top_k":      5,
  "threshold":  0.3,
  "filter":     null
}
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `question` | string | required | Natural-language question |
| `query_type` | string | `"auto"` | `"auto"` \| `"analytics"` \| `"semantic"` |
| `top_k` | int | `5` | Max documents to retrieve (semantic path) |
| `threshold` | float | `0.3` | Min cosine similarity to keep (semantic path) |
| `filter` | object | `null` | ChromaDB metadata filter (semantic path) |

### `POST /query` — response

```json
{
  "answer":     "The account with the highest credit is …",
  "query_type": "analytics",
  "sql":        "SELECT account_id, SUM(transaction_amount) …",
  "row_count":  42,
  "data":       [{"account_id": "…", "total_credit": 123456.78}],
  "sources":    []
}
```

| Field | Present when | Description |
|-------|-------------|-------------|
| `answer` | always | Natural-language answer from Claude |
| `query_type` | always | Which engine was used (`analytics` or `semantic`) |
| `sql` | analytics | The SQL query that was generated and executed |
| `row_count` | analytics | Total number of rows returned by the SQL |
| `data` | analytics | Raw result rows (capped at 100) |
| `sources` | semantic | Retrieved documents with similarity scores |

---

## How the Auto-Router Works

When `query_type` is `"auto"` (the default), the router checks the question for:

- **Aggregation keywords** — `total`, `sum`, `average`, `max`, `min`, `count`, `highest`, `lowest`, `rank`, `trend`, `spending`, `balance`, `credit`, `debit`, `transaction` …
- **Aggregation phrases** — `how many`, `how much`, `per account`, `by account`, `by date`, `over time` …

A match on either list routes to the **analytics (Text-to-SQL) engine**. No match routes to the **semantic (RAG) engine**. You can always override with an explicit `query_type`.

---

## How the Analytics Engine Works

```
Question
   │
   ▼
LLM generates SQL
(schema + question → Claude → raw SQL)
   │
   ▼
SQLite executes SQL
(exact arithmetic, no hallucination)
   │
   ▼
LLM narrates results
(SQL + result rows → Claude → natural-language answer)
   │
   ▼
Response: answer + sql + row_count + data
```

All CSV files in `data/` are loaded into **in-memory SQLite** at startup:

| File | SQLite table |
|------|--------------|
| `account.csv` | `account` |
| `bank.csv` | `bank` |
| `transaction.csv` | `tbl_transaction` |

> Files named after SQLite reserved words (e.g. `transaction`) are automatically prefixed with `tbl_`.

---

## How the RAG Pipeline Works

```
Ingest
  Files in data/ → loaded → chunked → embedded (all-MiniLM-L6-v2) → ChromaDB

Query
  Question → embedded → cosine similarity search → top-K docs
      → context assembled → Claude answers citing sources
```

CSV rows are each stored as a separate document (no chunking) so individual records remain retrievable by semantic similarity.

---

## Configuration

All settings are read from `.env`. Every variable has a default except `ANTHROPIC_API_KEY`.

### API / Server

| Variable | Default | Description |
|----------|---------|-------------|
| `API_HOST` | `0.0.0.0` | Server bind address |
| `API_PORT` | `8000` | Server port |
| `DEBUG` | `false` | Enable uvicorn auto-reload |
| `CORS_ORIGINS` | `["*"]` | Allowed CORS origins |

### LLM (Anthropic)

| Variable | Default | Description |
|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | — | **Required.** Anthropic API key |
| `ANTHROPIC_MODEL` | `claude-haiku-4-5-20251001` | Claude model for both SQL generation and RAG |

### Embeddings

| Variable | Default | Description |
|----------|---------|-------------|
| `OPENAI_EMBEDDING_MODEL` | `text-embedding-3-small` | Reserved for future use (currently uses `all-MiniLM-L6-v2` locally) |

### Vector Store (ChromaDB)

| Variable | Default | Description |
|----------|---------|-------------|
| `CHROMA_COLLECTION_NAME` | `documents` | Default collection name |
| `CHROMA_PERSIST_DIR` | `./data/chroma` | Where ChromaDB stores data on disk |

### Ingestion

| Variable | Default | Description |
|----------|---------|-------------|
| `DATA_DIR` | `./data` | Directory scanned for source files |
| `CHUNK_SIZE` | `1000` | Characters per text chunk (non-CSV docs) |
| `CHUNK_OVERLAP` | `200` | Overlap between consecutive chunks |

### Retrieval

| Variable | Default | Description |
|----------|---------|-------------|
| `SIMILARITY_TOP_K` | `5` | Number of documents retrieved per query |
| `SIMILARITY_THRESHOLD` | `0.3` | Minimum cosine similarity score to include a result |

---

## Dependencies

| Package | Purpose |
|---------|---------|
| `fastapi` | REST API framework |
| `uvicorn` | ASGI server |
| `langchain` / `langchain-community` | LLM orchestration, document loaders |
| `langchain-anthropic` | Claude integration |
| `chromadb` | Persistent vector store |
| `sentence-transformers` | Local embeddings (`all-MiniLM-L6-v2`) |
| `pandas` | CSV parsing and SQL result formatting |
| `pydantic-settings` | `.env`-backed settings |
| `pypdf` | PDF loading |
| `python-docx` | DOCX loading |
| `scalar-fastapi` | Interactive API documentation UI |

---

## License

MIT