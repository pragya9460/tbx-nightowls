.PHONY: help install seed test eval api frontend docker-up docker-down clean

help:
	@echo "Artha — AI Finance Assistant"
	@echo ""
	@echo "  make install     Install backend + frontend dependencies"
	@echo "  make seed        Generate and load synthetic seed data into MySQL"
	@echo "  make test        Run the backend test suite (SQLite, no services needed)"
	@echo "  make eval        Run the evaluation harness against the LLM/rule provider"
	@echo "  make api         Run the backend locally on :8000"
	@echo "  make frontend    Run the Vite dev server on :5173"
	@echo "  make docker-up   Start the full stack (Postgres + API + UI) with Docker"
	@echo "  make docker-down Stop and remove the Docker stack"
	@echo "  make clean       Remove build artifacts"

install:
	cd backend && pip install -r requirements.txt
	cd frontend && npm install

seed:
	cd backend && python scripts/load_data.py --generate --drop

test:
	cd backend && python -m pytest -q

eval:
	cd backend && python ../evaluation/run_eval.py

api:
	cd backend && uvicorn app.main:app --reload --port 8000

frontend:
	cd frontend && npm run dev

docker-up:
	docker compose up --build -d
	@echo ""
	@echo "UI:      http://localhost:5173"
	@echo "API:     http://localhost:8000/api/health"

docker-down:
	docker compose down

clean:
	rm -rf frontend/dist frontend/node_modules/.vite backend/.pytest_cache backend/**/__pycache__
	find . -name "__pycache__" -type d -prune -exec rm -rf {} +
