DATABASE_URL ?= postgresql+psycopg://carrier_pool:carrier_pool@localhost:5432/carrier_pool
DEMO_DATABASE_NAME := carrier_pool_demo
DEMO_DATABASE_URL := postgresql+psycopg://carrier_pool:carrier_pool@localhost:5432/$(DEMO_DATABASE_NAME)
FREIGHTFLOW_TENANT_ID ?= 11111111-1111-4111-8111-111111111111
HAULDESK_TENANT_ID ?= 22222222-2222-4222-8222-222222222222
BROKEROS_TENANT_ID ?= 33333333-3333-4333-8333-333333333333

.PHONY: setup generate validate db-up migrate seed-demo-tenants ingest rebuild rebuild-projections decisions demo-reset reset demo correction-demo test test-unit test-integration e2e backtest api-types api-types-check format format-check lint typecheck build down check

setup:
	cd backend && uv sync --all-groups
	cd frontend && pnpm install --frozen-lockfile

generate:
	cd backend && uv run carrier-pool generate --data-root ../data

validate:
	cd backend && uv run carrier-pool validate-data --data-root ../data

migrate: db-up
	cd backend && DATABASE_URL="$(DATABASE_URL)" uv run alembic upgrade head

seed-demo-tenants: migrate
	cd backend && DATABASE_URL="$(DATABASE_URL)" uv run carrier-pool seed-demo-tenants

ingest: generate validate seed-demo-tenants
	cd backend && DATABASE_URL="$(DATABASE_URL)" uv run carrier-pool ingest-all --data-root ../data --freightflow-tenant-id "$(FREIGHTFLOW_TENANT_ID)" --hauldesk-tenant-id "$(HAULDESK_TENANT_ID)" --brokeros-tenant-id "$(BROKEROS_TENANT_ID)"

decisions: ingest
	cd backend && DATABASE_URL="$(DATABASE_URL)" uv run carrier-pool decide-active

demo-reset: db-up
	test "$(DEMO_DATABASE_NAME)" = "carrier_pool_demo"
	docker compose --env-file .env.example exec -T db dropdb --if-exists --force --username=carrier_pool $(DEMO_DATABASE_NAME)
	docker compose --env-file .env.example exec -T db createdb --username=carrier_pool $(DEMO_DATABASE_NAME)

reset:
	@echo "Resetting only the dedicated $(DEMO_DATABASE_NAME) database."
	$(MAKE) demo-reset

demo: demo-reset generate validate
	cd backend && DATABASE_URL="$(DEMO_DATABASE_URL)" uv run alembic upgrade head
	cd backend && DATABASE_URL="$(DEMO_DATABASE_URL)" uv run carrier-pool seed-demo-tenants
	cd backend && DATABASE_URL="$(DEMO_DATABASE_URL)" uv run carrier-pool ingest-all --data-root ../data --freightflow-tenant-id "$(FREIGHTFLOW_TENANT_ID)" --hauldesk-tenant-id "$(HAULDESK_TENANT_ID)" --brokeros-tenant-id "$(BROKEROS_TENANT_ID)"
	cd backend && DATABASE_URL="$(DEMO_DATABASE_URL)" uv run carrier-pool decide-active
	APP_DATABASE_NAME="$(DEMO_DATABASE_NAME)" docker compose --env-file .env.example up --detach --build backend frontend
	@echo "Carrier Pool UI:  http://localhost:5173"
	@echo "Carrier Pool API: http://localhost:8000/docs"
	@echo "Demo brokers: North Star Freight, Alamo Brokerage, Gulf Bridge Logistics"

correction-demo: migrate
	cd backend && DATABASE_URL="$(DATABASE_URL)" uv run pytest -q tests/ingestion/test_generated_corrections_integration.py

rebuild-projections:
	test -n "$(TENANT_ID)"
	cd backend && uv run carrier-pool rebuild-projections --tenant-id "$(TENANT_ID)"

rebuild: rebuild-projections

backtest: decisions
	cd backend && DATABASE_URL="$(DATABASE_URL)" uv run carrier-pool rate-backtest --artifacts-dir ../artifacts

api-types:
	cd backend && uv run python scripts/export_openapi.py ../frontend/openapi.json
	cd frontend && pnpm exec openapi-typescript openapi.json -o src/api/generated.ts
	cd frontend && pnpm exec prettier --write openapi.json src/api/generated.ts

api-types-check: api-types
	git diff --exit-code -- frontend/openapi.json frontend/src/api/generated.ts

format:
	cd backend && uv run ruff format .
	cd frontend && pnpm format

format-check:
	cd backend && uv run ruff format --check .
	cd frontend && pnpm format:check

lint:
	cd backend && uv run ruff check .
	cd frontend && pnpm lint

typecheck:
	cd backend && uv run pyright
	cd frontend && pnpm typecheck

test-unit:
	cd backend && uv run pytest
	cd frontend && pnpm test

test-integration: db-up
	cd backend && DATABASE_URL="$(DATABASE_URL)" uv run pytest tests/db/test_persistence_integration.py tests/db/test_rls_direct_sql_integration.py tests/ingestion/test_freightflow_persistence_integration.py tests/ingestion/test_hauldesk_persistence_integration.py tests/ingestion/test_brokeros_persistence_integration.py

test: test-unit test-integration

e2e:
	cd frontend && pnpm e2e

build:
	cd backend && uv build
	cd frontend && pnpm build

db-up:
	docker compose --env-file .env.example up --detach --wait db

down:
	docker compose --env-file .env.example down

check: format-check lint typecheck test-unit build
