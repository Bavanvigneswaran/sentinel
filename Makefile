.PHONY: up down dev dev-backend dev-frontend test agent logs install \
        migrate migrate-down revision db-shell redis-shell reset-db lint typecheck \
        agent-enroll agent-sample agent-status agent-install-service agent-uninstall-service \
        mobile mobile-android mobile-prebuild mobile-test mobile-collector-test \
        mobile-collector-logs

install:
	cd backend && python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
	cd agent && python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
	cd web && npm install
	cd mobile && npm install

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
	cd mobile && npm test
	$(MAKE) mobile-collector-test

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
	cd mobile && npm run lint

typecheck:
	cd web && npx tsc -b --noEmit
	cd mobile && npx tsc --noEmit

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

# --- mobile (Phase 10a Android viewer) ---------------------------------------
# Point mobile/.env at a backend the *device* can reach before the first run:
# an Android emulator sees this machine as 10.0.2.2, a physical phone needs
# your LAN IP. See mobile/README.md.

# Metro, for an already-installed dev build.
mobile:
	cd mobile && npx expo start --dev-client

# Build, install and launch the dev build on the running emulator/device.
# The first run compiles the Android project and takes a while.
mobile-android:
	cd mobile && npx expo run:android

# Regenerate android/ from app.config.ts. Needed after changing a config
# plugin, the package name, or adding google-services.json.
mobile-prebuild:
	cd mobile && npx expo prebuild --platform android --clean

mobile-test:
	cd mobile && npm test

# --- collector (Phase 10b: the phone as a monitored device) -------------------
# The Kotlin foreground-service collector lives in mobile/modules/sentinel-collector.
# Its JVM unit tests need no emulator: everything they cover is deliberately pure
# (aggregation, batching, counter differentiation, /proc parsing, JSON encoding).
#
# Gradle still needs an Android SDK to compile against, so this falls back to the
# usual macOS location when ANDROID_HOME is unset and *skips loudly* when there
# is no SDK at all — `make test` must stay runnable on a machine that has never
# built the app, rather than failing on a toolchain the other suites do not need.
ANDROID_SDK ?= $(or $(ANDROID_HOME),$(HOME)/Library/Android/sdk)

mobile-collector-test:
	@if [ ! -d "$(ANDROID_SDK)" ]; then \
		echo "SKIP mobile-collector-test: no Android SDK at $(ANDROID_SDK)."; \
		echo "     Set ANDROID_HOME to run the Kotlin collector tests."; \
	else \
		cd mobile/android && ANDROID_HOME="$(ANDROID_SDK)" ./gradlew :sentinel-collector:testDebugUnitTest; \
	fi

# Follow the collector on a connected device. The service logs under its own
# tags, which are the only view of it once the app's task has been swiped away.
mobile-collector-logs:
	adb logcat -s SentinelCollector:V SentinelSocket:V SentinelService:V SentinelBoot:V
