.PHONY: up down dev dev-backend dev-frontend test agent logs install serve web-build \
        migrate migrate-down revision db-shell redis-shell reset-db lint typecheck \
        agent-enroll agent-sample agent-status agent-install-service agent-uninstall-service \
        agent-build agent-build-check agent-build-clean \
        train-novelty train-novelty-report \
        mobile mobile-android mobile-prebuild mobile-test mobile-collector-test \
        mobile-apk mobile-apk-test \
        mobile-collector-logs \
        deps-lock e2e-db e2e-serve e2e-code

install:
	cd backend && python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
	cd agent && python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
	cd frontend/web && npm install
	cd frontend/mobile && npm install

# Pin the dependency closure declared in backend/pyproject.toml into
# backend/requirements.txt. Vulnerability scanners cannot match a CVE against
# `fastapi>=0.115`; they need a version. NOT `pip freeze` — see the script.
deps-lock:
	cd backend && .venv/bin/python scripts/lock_dependencies.py

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
	funnel_url=$$(command -v tailscale >/dev/null 2>&1 && tailscale funnel status --json 2>/dev/null \
		| python3 -c "import json,sys; d=json.load(sys.stdin); w=list(d.get('Web') or {}); print('https://'+w[0].split(':')[0]) if w else None" 2>/dev/null); \
	echo ""; \
	echo "  Console + API:  http://$$ip:8000"; \
	echo "  On this Mac:    http://localhost:8000"; \
	if [ -n "$$funnel_url" ]; then \
		echo "  Public (Tailscale Funnel):  $$funnel_url"; \
	fi; \
	echo ""; \
	if [ -n "$$funnel_url" ]; then \
		echo "  Point frontend/mobile/.env at $$funnel_url before building an APK —"; \
		echo "  it won't go stale when this machine changes networks. Otherwise:"; \
		echo "  http://$$ip:8000 works too, but only on this network and until the"; \
		echo "  IP changes. See docs/PACKAGING.md's 'Serving the console' section."; \
	else \
		echo "  Point frontend/mobile/.env at http://$$ip:8000 before building an APK."; \
		echo "  This address is only reachable on this network and can change —"; \
		echo "  see docs/PACKAGING.md for using Tailscale Funnel for a stable one."; \
	fi; \
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

# --- layer-4 novelty models ---------------------------------------------------
# Trains one IsolationForest per device from its own real 1m history. Run by
# hand and read the report it prints: rows trained on, and how the newest real
# reading scores. NOVELTY_MODEL_DIR defaults to backend/var/models (gitignored);
# the API reports no score at all until this has been run, rather than
# inventing one. `train-novelty-report` scores against the models already on
# disk without retraining.
NOVELTY_MODEL_DIR ?= var/models
NOVELTY_DAYS ?= 7

train-novelty:
	cd backend && NOVELTY_MODEL_DIR=$(NOVELTY_MODEL_DIR) \
		.venv/bin/python scripts/train_novelty_model.py --days $(NOVELTY_DAYS)

train-novelty-report:
	cd backend && NOVELTY_MODEL_DIR=$(NOVELTY_MODEL_DIR) \
		.venv/bin/python scripts/train_novelty_model.py --report

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

# --- end-to-end suites (Selenium, Appium, load) -------------------------------
# These run against a *separate* stack from `make serve`, configured by
# backend/.env.ci. That is not tidiness: `make serve` runs ENVIRONMENT=prod,
# where rate limiting refuses the 11th login of a 300-case suite and
# COOKIE_SECURE=true means the refresh cookie is dropped over http://localhost.
# Both failures are reported by the browser as application bugs. See
# backend/.env.ci for the full list and docs/TESTING.md for how the suites use
# this.

E2E_ENV = SENTINEL_ENV_FILE=$(CURDIR)/backend/.env.ci

# 8000 matches what a generated suite defaults to, and collides with a running
# `make serve` — stop that first, or override: `make e2e-serve E2E_PORT=8001`.
E2E_PORT ?= 8000

# Reset the CI database and seed the account the suites sign in as. Destructive
# by design — the script refuses any database not named *_ci or *_test.
#
# The credentials are not ours to choose: a generated suite hardcodes whatever
# pair it was written with, and the account has to exist under *that* pair or
# every case fails at sign-in rather than at the thing it is testing. So:
#
#   make e2e-db SENTINEL_E2E_EMAIL=testuser@sentinel.dev \
#               SENTINEL_E2E_PASSWORD='SecureP@ssw0rd123'
e2e-db:
	cd backend && $(E2E_ENV) .venv/bin/python scripts/create_database.py --drop-existing
	cd backend && $(E2E_ENV) .venv/bin/alembic upgrade head
	cd backend && $(E2E_ENV) .venv/bin/python scripts/seed_e2e_account.py

# The console and the API on one origin, exactly as `make serve` does it, so a
# driver and a phone both see the shape a real deployment has. 0.0.0.0 because
# an Android emulator reaches the host at 10.0.2.2, never at localhost.
e2e-serve: web-build
	cd backend && $(E2E_ENV) .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port $(E2E_PORT)

# Mint an enrollment code against the seeded account, so a REAL agent can be
# enrolled and the suites get real metrics to assert against. There is no
# metric seeder and there will not be one: CLAUDE.md's first hard rule is that
# every number came from a real agent reading a real machine, and a fixture
# that fabricates CPU history is exactly what that forbids.
e2e-code:
	cd backend && $(E2E_ENV) .venv/bin/python scripts/seed_e2e_account.py --enrollment-code

# An APK for Appium, and nothing else.
#
# This has to be a RELEASE build, and finding that out took installing a debug
# one. `assembleDebug` produces a dev-client shell: `expo-dev-client` makes
# DevLauncherActivity the launcher activity and no JS bundle is embedded at
# all, so the APK boots into "waiting for Metro" and there is no app for a
# driver to drive. Verified by unzipping it — no index.android.bundle.
#
# So it is `assembleRelease`, which embeds the bundle and skips the dev
# launcher. That needs a signing key, and `make mobile-apk` correctly refuses
# to build without a real one. Rather than weaken that refusal, this target
# generates its own throwaway keystore.
#
# Why a generated keystore is not the thing withReleaseSigning.js exists to
# prevent: that guard is about React Native's *public* debug key, which is in
# every RN checkout on earth, so anyone can forge an update Android installs
# over the real app. This key is generated locally and exists only here. Its
# password is a literal below and that is fine — the password protects nothing
# once the file is on your disk; what makes a signing key dangerous to share is
# the key, and this one signs exactly one thing that is never published.
#
# The output is deliberately NOT left at android/app/build/outputs/apk/release/.
# That is the path a distributable comes from, and an APK signed with a
# regenerable key sitting there is one `register_build.py --file` away from
# being published as an update nobody can ever install over.
TEST_KEYSTORE = $(CURDIR)/frontend/mobile/.test-keystore/appium.jks
TEST_KEYSTORE_PASS = appium-test-only-never-published
TEST_APK = $(CURDIR)/frontend/mobile/build/app-appium.apk

mobile-apk-test:
	@if [ ! -d "$(ANDROID_SDK)" ]; then \
		echo "No Android SDK at $(ANDROID_SDK). Set ANDROID_HOME."; \
		exit 1; \
	fi
	@if [ ! -f "$(TEST_KEYSTORE)" ]; then \
		echo "Generating a throwaway keystore for test builds..."; \
		mkdir -p $(dir $(TEST_KEYSTORE)); \
		keytool -genkeypair -v -keystore "$(TEST_KEYSTORE)" -alias appium \
			-keyalg RSA -keysize 2048 -validity 3650 \
			-storepass "$(TEST_KEYSTORE_PASS)" -keypass "$(TEST_KEYSTORE_PASS)" \
			-dname "CN=Sentinel Appium Test, OU=Testing, O=Sentinel, C=GB" 2>&1 | tail -2; \
	fi
	cd frontend/mobile && \
		SENTINEL_ANDROID_KEYSTORE="$(TEST_KEYSTORE)" \
		SENTINEL_ANDROID_KEYSTORE_PASSWORD="$(TEST_KEYSTORE_PASS)" \
		SENTINEL_ANDROID_KEY_ALIAS=appium \
		SENTINEL_ANDROID_KEY_PASSWORD="$(TEST_KEYSTORE_PASS)" \
		npx expo prebuild --platform android --clean && \
		cd android && \
		SENTINEL_ANDROID_KEYSTORE="$(TEST_KEYSTORE)" \
		SENTINEL_ANDROID_KEYSTORE_PASSWORD="$(TEST_KEYSTORE_PASS)" \
		SENTINEL_ANDROID_KEY_ALIAS=appium \
		SENTINEL_ANDROID_KEY_PASSWORD="$(TEST_KEYSTORE_PASS)" \
		ANDROID_HOME="$(ANDROID_SDK)" ./gradlew assembleRelease
	@mkdir -p $(dir $(TEST_APK))
	@mv frontend/mobile/android/app/build/outputs/apk/release/app-release.apk "$(TEST_APK)"
	@echo ""
	@echo "  Appium APK: $(TEST_APK)"
	@echo ""
	@echo "  Signed with a generated throwaway key, for an emulator that is"
	@echo "  destroyed with the job. NEVER publish it: the key is regenerable,"
	@echo "  so an update signed with a later one could not install over it."
	@echo "  'make mobile-apk' is the distributable. See docs/PACKAGING.md."
	@echo ""
	@echo "  It carries the same applicationId as the real app, so a device"
	@echo "  holding a differently-signed build must uninstall first:"
	@echo "    adb uninstall com.sentinel.viewer"
	@echo ""

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
