# RAG API

A Retrieval-Augmented Generation API built with FastAPI, LangChain, and ChromaDB.

## Features

- Document ingestion (PDF, TXT, MD, DOCX)
- Vector embeddings with OpenAI
- Similarity search with configurable thresholds
- RAG query with source citations
- FastAPI REST API with automatic docs
- ChromaDB persistent vector storage

## Quick Start

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure Environment

Copy `.env` and add your OpenAI API key:

```bash
cp .env .env.local
# Edit .env.local with your OPENAI_API_KEY
```

### 3. Add Documents

Place documents in the `data/` directory:
```
data/
├── document1.pdf
├── document2.txt
└── notes.md
```

### 4. Run the API

```bash
# Development
uvicorn main:app --reload

# Production
uvicorn main:app --host 0.0.0.0 --port 8000
```

### 5. Ingest Documents

```bash
curl -X POST http://localhost:8000/ingest
```

### 6. Query the RAG System

```bash
curl -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{"question": "What is this document about?"}'
```

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/health` | Health check |
| POST | `/query` | RAG query with LLM answer |
| POST | `/search` | Similarity search only |
| POST | `/ingest` | Ingest documents from data/ |
| POST | `/ingest/upload` | Upload and ingest files |
| POST | `/ingest/text` | Ingest raw text with metadata |
| DELETE | `/collection/{name}` | Delete collection |
| GET | `/collections` | List collections |

## API Documentation

- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

## Project Structure

```
rag-api/
├── data/                 # Source documents
├── src/
│   ├── __init__.py
│   ├── config.py         # Settings management
│   ├── ingestion.py      # Document loading & chunking
│   ├── database.py       # ChromaDB connection
│   └── retriever.py      # Similarity search & RAG chain
├── main.py               # FastAPI app
├── requirements.txt
└── .env                  # Environment variables
```

## Configuration

Key settings in `.env`:

| Variable | Default | Description |
|----------|---------|-------------|
| `OPENAI_API_KEY` | required | OpenAI API key |
| `CHUNK_SIZE` | 1000 | Text chunk size |
| `CHUNK_OVERLAP` | 200 | Chunk overlap |
| `SIMILARITY_TOP_K` | 5 | Results to retrieve |
| `SIMILARITY_THRESHOLD` | 0.7 | Min similarity score |
| `CHROMA_PERSIST_DIR` | ./data/chroma | Vector store path |

## License

MIT