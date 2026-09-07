.PHONY: install up down migrate migrate-test test lint dev api worker scheduler evals web-dev web-test web-e2e demo

install:
	uv sync --extra dev

up:
	docker compose up -d

down:
	docker compose down

migrate:
	dbmate --no-dump-schema --migrations-dir db/migrations up

migrate-test:
	dbmate --env DATABASE_URL_TEST --no-dump-schema --migrations-dir db/migrations up

test: migrate-test
	uv run pytest -q

lint:
	uv run ruff check . && uv run ruff format --check .

api:
	uv run python -m cs.api

worker:
	uv run python -m cs.worker

scheduler:
	uv run python -m cs.scheduler

evals:
	uv run python scripts/eval_tagging.py --min-agreement 0.7

web-dev:
	cd web && npm run dev

web-test:
	cd web && npm test

web-e2e:
	AUTH_MODE=dev uv run python scripts/seed_dev_fivetran.py && cd web && npx playwright install chromium && npm run test:e2e

demo:
	CS_FAKE_CONNECTOR=1 AUTH_MODE=dev uv run python scripts/seed_dev_fivetran.py
	CS_FAKE_CONNECTOR=1 AUTH_MODE=dev $(MAKE) -j3 api worker scheduler
