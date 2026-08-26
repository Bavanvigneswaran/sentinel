.PHONY: up down dev dev-backend dev-frontend test agent logs install serve web-build \
        migrate migrate-down revision db-shell redis-shell reset-db lint typecheck \
        agent-enroll agent-sample agent-status agent-install-service agent-uninstall-service \
        agent-build agent-build-check agent-build-clean \
        mobile mobile-android mobile-prebuild mobile-test mobile-collector-test \
        mobile-apk \
        mobile-collector-logs

install:
	cd backend && python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
	cd agent && python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
	cd frontend/web && npm install
	cd frontend/mobile && npm install

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
	cd frontend/web && npm run dev

dev:
	@echo "Run 'make dev-backend' and 'make dev-frontend' in separate terminals."

# --- running it for real ------------------------------------------------------
# `make serve` is the one command that produces something usable from another
# machine. `dev-frontend` is a Vite dev server bound to localhost: correct for
# development, and no way to hand anyone a working install — a phone or a second
# PC cannot reach it at all. This builds the console and lets the API process
# serve it, so ONE origin is the whole product (console + REST + viewer socket)
# and there is no CORS to configure.
#
# Binds 0.0.0.0 on purpose: the default `--port 8000` listens on loopback only,
# which is why an Android device or another PC sees nothing. Print the LAN URL
# rather than making the user work it out, since it is also the value that has
# to go in frontend/mobile/.env before building an APK.
web-build:
	cd frontend/web && npm run build

serve: web-build
	@ip=$$(ipconfig getifaddr en0 2>/dev/null || hostname -I 2>/dev/null | awk '{print $$1}'); \
	echo ""; \
	echo "  Console + API:  http://$$ip:8000"; \
	echo "  On this Mac:    http://localhost:8000"; \
	echo ""; \
	echo "  Point frontend/mobile/.env at http://$$ip:8000 before building an APK."; \
	echo ""
	cd backend && .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 \
		--proxy-headers --forwarded-allow-ips=127.0.0.1

test:
	cd backend && .venv/bin/pytest -q
	cd agent && .venv/bin/pytest -q
	cd frontend/web && npm test
	cd frontend/mobile && npm test
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
	cd frontend/web && npm run lint
	cd frontend/mobile && npm run lint

typecheck:
	cd frontend/web && npx tsc -b --noEmit
	cd frontend/mobile && npx tsc --noEmit

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

# launchd on macOS, systemd on Linux, Task Scheduler on Windows. Add
# scope=system for a boot-time, machine-wide install on Linux or Windows;
# macOS is per-user only (see agent/sentinel_agent/service/launchd.py).
#   make agent-install-service scope=system
SCOPE ?= $(or $(scope),user)

agent-install-service:
	cd agent && .venv/bin/sentinel-agent --scope $(SCOPE) install-service

agent-uninstall-service:
	cd agent && .venv/bin/sentinel-agent --scope $(SCOPE) uninstall-service

# --- packaging (Phase 11) -----------------------------------------------------
# PyInstaller DOES NOT CROSS-COMPILE. `make agent-build` produces a binary for
# THIS machine and nothing else — it prints the target it is about to build so
# that is never a surprise. Windows and Linux binaries come from the CI matrix
# in .github/workflows/agent-build.yml, one runner per target. There is
# deliberately no `make agent-build-windows`: a target that silently only ever
# works on the machine it was written on is worse than no target.
# See docs/PACKAGING.md.

agent-build-check:
	cd agent && .venv/bin/python build/build.py --check

agent-build:
	cd agent && .venv/bin/pip install -q -e ".[build]" && .venv/bin/python build/build.py

agent-build-clean:
	cd agent && .venv/bin/pip install -q -e ".[build]" && \
		.venv/bin/python build/build.py --clean

# --- mobile (Phase 10a Android viewer) ---------------------------------------
# Point frontend/mobile/.env at a backend the *device* can reach before the first run:
# an Android emulator sees this machine as 10.0.2.2, a physical phone needs
# your LAN IP. See frontend/mobile/README.md.

# Metro, for an already-installed dev build.
mobile:
	cd frontend/mobile && npx expo start --dev-client

# Build, install and launch the dev build on the running emulator/device.
# The first run compiles the Android project and takes a while.
mobile-android:
	cd frontend/mobile && npx expo run:android

# Regenerate android/ from app.config.ts. Needed after changing a config
# plugin, the package name, or adding google-services.json.
mobile-prebuild:
	cd frontend/mobile && npx expo prebuild --platform android --clean

mobile-test:
	cd frontend/mobile && npm test

# A distributable release APK. Refuses to build without a real keystore rather
# than falling back to React Native's *public* debug key, which the generated
# android/app/build.gradle otherwise uses for the release buildType — an APK
# signed with a key everybody has lets anyone forge an update Android accepts
# as the same app. plugins/withReleaseSigning.js does the wiring; passwords come
# from the environment, never from -P properties (they show in the process
# table). Create a keystore once with:
#   keytool -genkeypair -v -keystore sentinel-release.jks -alias sentinel \
#           -keyalg RSA -keysize 4096 -validity 10000
# Gradle needs an Android SDK to compile against, and `expo prebuild --clean`
# regenerates android/ — including local.properties, which is where the SDK
# path would otherwise have been recorded. So every Gradle target has to pass
# it explicitly. Falls back to the usual macOS location when ANDROID_HOME is
# unset, because a machine that has built the app once already has an SDK there
# and failing with Gradle's own "SDK location not found" names neither the
# variable nor the fix.
ANDROID_SDK ?= $(or $(ANDROID_HOME),$(HOME)/Library/Android/sdk)

mobile-apk:
	@if [ ! -d "$(ANDROID_SDK)" ]; then \
		echo "No Android SDK at $(ANDROID_SDK). Set ANDROID_HOME."; \
		exit 1; \
	fi
	@missing=""; \
	for v in SENTINEL_ANDROID_KEYSTORE SENTINEL_ANDROID_KEYSTORE_PASSWORD \
	         SENTINEL_ANDROID_KEY_ALIAS SENTINEL_ANDROID_KEY_PASSWORD; do \
		eval "val=\$$$$v"; [ -n "$$val" ] || missing="$$missing $$v"; \
	done; \
	if [ -n "$$missing" ]; then \
		echo "Refusing to build a release APK: no keystore configured."; \
		echo "Unset:$$missing"; \
		echo "Without these the release build would be signed with React Native's"; \
		echo "public debug key — see docs/PACKAGING.md."; \
		exit 1; \
	fi; \
	cd frontend/mobile && npx expo prebuild --platform android --clean && \
		cd android && ANDROID_HOME="$(ANDROID_SDK)" ./gradlew assembleRelease && \
		echo "APK: frontend/mobile/android/app/build/outputs/apk/release/app-release.apk" && \
		echo "Publish it with: python agent/build/register_build.py --os android \\" && \
		echo "  --arch arm64 --version 0.1.0 --signed --signing '<your key>' \\" && \
		echo "  --file frontend/mobile/android/app/build/outputs/apk/release/app-release.apk"

# --- collector (Phase 10b: the phone as a monitored device) -------------------
# The Kotlin foreground-service collector lives in frontend/mobile/modules/sentinel-collector.
# Its JVM unit tests need no emulator: everything they cover is deliberately pure
# (aggregation, batching, counter differentiation, /proc parsing, JSON encoding).
#
# Gradle still needs an Android SDK to compile against; see ANDROID_SDK above.

mobile-collector-test:
	@if [ ! -d "$(ANDROID_SDK)" ]; then \
		echo "SKIP mobile-collector-test: no Android SDK at $(ANDROID_SDK)."; \
		echo "     Set ANDROID_HOME to run the Kotlin collector tests."; \
	else \
		cd frontend/mobile/android && ANDROID_HOME="$(ANDROID_SDK)" ./gradlew :sentinel-collector:testDebugUnitTest; \
	fi

# Follow the collector on a connected device. The service logs under its own
# tags, which are the only view of it once the app's task has been swiped away.
mobile-collector-logs:
	adb logcat -s SentinelCollector:V SentinelSocket:V SentinelService:V SentinelBoot:V
