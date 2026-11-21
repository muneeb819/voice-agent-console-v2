# MMA-VIE developer commands. See README.md.
PY ?= python3
DB ?= postgresql://postgres@127.0.0.1:5432/mmavie

.PHONY: install lint test test-speech up down smoke local local-stop migrate

install:            ## install shared kit + dev tools into the current environment
	$(PY) -m pip install -e packages/mmavie-common "psycopg[binary]==3.2.3" pytest anyio ruff numpy soundfile soxr huggingface_hub
	$(PY) -m pip install -r services/speech/requirements-test.txt

lint:
	ruff check .

test:               ## unit + contract tests (set MMAVIE_TEST_DATABASE_URL for DB tests)
	$(PY) -m pytest

test-speech:
	cd services/speech && $(PY) -m pytest -q

up:                 ## full dev stack in Docker (CPU, fake models)
	docker compose up -d --build

down:
	docker compose down

smoke:              ## end-to-end checks against a running stack
	$(PY) scripts/smoke.py

local:              ## dev stack as local processes (no Docker); needs Postgres at $(DB)
	MMAVIE_DATABASE_URL=$(DB) scripts/run-local.sh start

local-stop:
	scripts/run-local.sh stop

migrate:
	MMAVIE_DATABASE_URL=$(DB) $(PY) db/migrate.py
