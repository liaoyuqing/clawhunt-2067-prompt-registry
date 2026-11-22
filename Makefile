# Makefile: one-command workflow for the prompt/model registry.
# Every target reads configuration from the environment; nothing is hard-coded.
PY ?= python
PROMPT_DIR ?= prompts

.PHONY: setup setup-postgres migrate migrate-check dev-sqlite prompt-register prompt-register-dry test run checks clean

setup:
	$(PY) -m pip install -r requirements-dev.txt

setup-postgres:
	$(PY) -m pip install -r requirements-postgres.txt

# Alembic migration (PostgreSQL 16 dialect). Requires DATABASE_URL=postgresql+psycopg://...
migrate:
	$(PY) -m alembic upgrade head

migrate-check:
	$(PY) -m alembic current

# Offline SQL review: prints the DDL without connecting anywhere.
migrate-sql:
	$(PY) -m alembic upgrade head --sql

# Local demo database without PostgreSQL: builds the schema from the SQLAlchemy metadata.
# Local schema without PostgreSQL: same tables, built from the SQLAlchemy metadata.
migrate-sqlite:
	DATABASE_URL=$${DATABASE_URL:-sqlite:///./dev.sqlite3} $(PY) -m app.dbcheck init

dev-sqlite: migrate-sqlite

# Registers prompts/ into the database. CI runs this BEFORE the eval-lint step.
prompt-register:
	$(PY) -m app.cli_prompt_register --prompt-dir $(PROMPT_DIR)

prompt-register-dry:
	$(PY) -m app.cli_prompt_register --prompt-dir $(PROMPT_DIR) --dry-run

# Registers a model profile; only the alias is stored, never an address.
model-profile:
	$(PY) -m app.cli_model_profile --name $${PROFILE_NAME:-default} \
		--endpoint-alias $${PROFILE_ALIAS:-model.main} \
		--temperature $${PROFILE_TEMPERATURE:-0.2} \
		--max-tokens $${PROFILE_MAX_TOKENS:-512}

test:
	$(PY) -m pytest -q

run:
	$(PY) -m uvicorn app.main:app --host $${API_HOST:-127.0.0.1} --port $${API_PORT:-8000}

# Regenerates docs/checks/*.txt (real command output, never hand-edited).
checks:
	bash scripts/capture_checks.sh

clean:
	rm -rf .pytest_cache dev.sqlite3 build *.egg-info
