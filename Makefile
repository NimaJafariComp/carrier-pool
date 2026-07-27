.PHONY: setup format format-check lint typecheck test-unit build db-up down check

setup:
	cd backend && uv sync --all-groups
	cd frontend && pnpm install --frozen-lockfile

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

build:
	cd backend && uv build
	cd frontend && pnpm build

db-up:
	docker compose --env-file .env.example up --detach db

down:
	docker compose --env-file .env.example down

check: format-check lint typecheck test-unit build
