.PHONY: up down dev dev-backend dev-frontend test agent logs install \
        migrate migrate-down revision db-shell redis-shell reset-db lint typecheck \
        agent-enroll agent-sample agent-status agent-install-service agent-uninstall-service

install:
	cd backend && python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
	cd agent && python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
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
	cd agent && .venv/bin/pytest -q

# --- database ---------------------------------------------------------------
# Alembic runs as the owner role (ADMIN_DATABASE_URL); the app itself connects
# as the restricted sentinel_app role so RLS is enforced.
migrate:
	cd backend && .venv/bin/alembic upgrade head

migrate-down:
	cd backend && .venv/bin/alembic downgrade -1

# usage: make revision m="add widgets table"
revision:
	cd backend && .venv/bin/alembic revision --autogenerate -m "$(m)"

db-shell:
	docker compose exec -it timescaledb psql -U sentinel -d sentinel

redis-shell:
	docker compose exec -it redis redis-cli

reset-db:
	docker compose down -v
	$(MAKE) up
	$(MAKE) migrate

# --- quality ----------------------------------------------------------------
lint:
	cd backend && .venv/bin/ruff check .
	cd agent && .venv/bin/ruff check .
	cd web && npm run lint

typecheck:
	cd web && npx tsc -b --noEmit

# --- agent ------------------------------------------------------------------
# Enrol first: mint a code in the UI (or POST /enrollment-codes), then
#   make agent-enroll code=X4T9-K2QM-7PDR
agent-enroll:
	cd agent && .venv/bin/sentinel-agent enroll --code "$(code)"

agent:
	cd agent && .venv/bin/sentinel-agent run

# Print one real sample from this machine without connecting to anything.
agent-sample:
	cd agent && .venv/bin/sentinel-agent sample

agent-status:
	cd agent && .venv/bin/sentinel-agent status

# macOS only for now; systemd and Windows Service arrive in Phase 11.
agent-install-service:
	cd agent && .venv/bin/sentinel-agent install-service

agent-uninstall-service:
	cd agent && .venv/bin/sentinel-agent uninstall-service
