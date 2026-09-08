.PHONY: install up down migrate migrate-test test lint dev api worker scheduler evals web-dev web-test web-e2e

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

# Seeds fixture Fivetran-shaped data so sync/activate work without a live connector.
# The Playwright run itself still needs a real, signed-in Clerk session -- see the
# Known limitations section in README.md.
web-e2e:
	uv run python scripts/seed_dev_fivetran.py && cd web && npx playwright install chromium && npm run test:e2e
