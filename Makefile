.PHONY: setup generate validate ingest rebuild-projections format format-check lint typecheck test-unit test-integration build db-up down check

setup:
	cd backend && uv sync --all-groups
	cd frontend && pnpm install --frozen-lockfile

generate:
	cd backend && uv run carrier-pool generate --data-root ../data

validate:
	cd backend && uv run carrier-pool validate-data --data-root ../data

ingest:
	test -n "$(FREIGHTFLOW_TENANT_ID)" && test -n "$(HAULDESK_TENANT_ID)" && test -n "$(BROKEROS_TENANT_ID)"
	cd backend && uv run carrier-pool ingest-all --data-root ../data --freightflow-tenant-id "$(FREIGHTFLOW_TENANT_ID)" --hauldesk-tenant-id "$(HAULDESK_TENANT_ID)" --brokeros-tenant-id "$(BROKEROS_TENANT_ID)"

rebuild-projections:
	test -n "$(TENANT_ID)"
	cd backend && uv run carrier-pool rebuild-projections --tenant-id "$(TENANT_ID)"

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
	cd backend && DATABASE_URL=postgresql+psycopg://carrier_pool:carrier_pool@localhost:5432/carrier_pool uv run pytest tests/db/test_persistence_integration.py tests/db/test_rls_direct_sql_integration.py tests/ingestion/test_freightflow_persistence_integration.py tests/ingestion/test_hauldesk_persistence_integration.py tests/ingestion/test_brokeros_persistence_integration.py

build:
	cd backend && uv build
	cd frontend && pnpm build

db-up:
	docker compose --env-file .env.example up --detach db

down:
	docker compose --env-file .env.example down

check: format-check lint typecheck test-unit build
