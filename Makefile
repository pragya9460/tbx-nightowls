.PHONY: help install seed test eval api frontend docker-up docker-dev docker-prod docker-down mysql-up clean

# Docker buildx reads OTEL_* env vars from the shell and only supports
# grpc/http-protobuf. The Polaris telemetry vars (http/json) make every
# `docker compose --build` fail with "unsupported otlp protocol http/json".
unexport OTEL_EXPORTER_OTLP_PROTOCOL OTEL_EXPORTER_OTLP_ENDPOINT
unexport OTEL_TRACES_EXPORTER OTEL_METRICS_EXPORTER OTEL_LOGS_EXPORTER

help:
	@echo "Artha — AI Finance Assistant (MySQL)"
	@echo ""
	@echo "  make install     Install backend + frontend dependencies"
	@echo "  make mysql-up    Start MySQL (and Redis) via Docker"
	@echo "  make seed        Load data/*.csv into MySQL"
	@echo "  make test        Run backend tests (needs MySQL)"
	@echo "  make eval        Run the evaluation harness"
	@echo "  make api         Run the backend locally on :8000"
	@echo "  make frontend    Run the Vite dev server on :5173"
	@echo "  make docker-dev  Start Docker with backend hot reload"
	@echo "  make docker-prod Build and start the production Docker stack"
	@echo "  make docker-up   Alias for docker-prod"
	@echo "  make docker-down Stop and remove the Docker stack"
	@echo "  make clean       Remove build artifacts"

install:
	cd backend && pip install -r requirements.txt
	cd frontend && npm install

mysql-up:
	docker compose up -d mysql redis

seed:
	cd backend && python scripts/load_data.py --data-dir ../data

test:
	cd backend && python -m pytest -q

eval:
	cd backend && python ../evaluation/run_eval.py

api:
	cd backend && uvicorn app.main:app --reload --port 8000

frontend:
	cd frontend && npm run dev

docker-dev:
	docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build

docker-prod:
	docker compose up --build -d
	@echo ""
	@echo "UI:      http://localhost:5173"
	@echo "API:     http://localhost:8000/api/health"

docker-up: docker-prod

docker-down:
	docker compose down

clean:
	rm -rf frontend/dist frontend/node_modules/.vite backend/.pytest_cache backend/**/__pycache__
	find . -name "__pycache__" -type d -prune -exec rm -rf {} +
