-include .env
export

.PHONY: install up down migrate migrate-test test lint dev api worker scheduler

install:
	uv sync --extra dev

up:
	docker compose up -d

down:
	docker compose down

migrate:
	dbmate --url "$$DATABASE_URL" --migrations-dir db/migrations up

migrate-test:
	dbmate --url "$$DATABASE_URL_TEST" --migrations-dir db/migrations up

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
