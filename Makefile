.PHONY: up down dev dev-backend dev-frontend test agent logs install

install:
	cd backend && python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
	cd web && npm install

up:
	docker compose up -d
	@echo "Waiting for services to be healthy..."
	@until docker compose ps timescaledb | grep -q healthy; do sleep 1; done
	@echo "TimescaleDB + Redis are up."

down:
	docker compose down

logs:
	docker compose logs -f

dev-backend:
	cd backend && .venv/bin/uvicorn app.main:app --reload --port 8000

dev-frontend:
	cd web && npm run dev

dev:
	@echo "Run 'make dev-backend' and 'make dev-frontend' in separate terminals."

test:
	cd backend && .venv/bin/pytest -q

agent:
	cd agent && python -m sentinel_agent.main
