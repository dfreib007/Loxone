.PHONY: help install format lint typecheck test audit build clean

help:  ## Show this help.
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  %-12s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

install:  ## Install runtime + dev dependencies into a uv-managed venv.
	uv sync --all-groups

format:  ## Auto-format code with ruff.
	uv run ruff format src tests

lint:  ## Run ruff (lint + format check).
	uv run ruff check src tests
	uv run ruff format --check src tests

typecheck:  ## Run mypy in strict mode.
	uv run mypy

test:  ## Run pytest with coverage gate.
	uv run pytest

audit:  ## Run pip-audit on the resolved production dependency tree.
	uv export --format requirements-txt --no-hashes --no-dev --no-emit-project --quiet -o /tmp/loxone-voice-audit.txt
	uv run pip-audit --strict -r /tmp/loxone-voice-audit.txt --disable-pip --no-deps

build: lint typecheck test  ## Run the full build pipeline (blocks on any failure).
	@echo "build: OK"

clean:  ## Remove caches and build artifacts.
	rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov dist build
	find . -type d -name __pycache__ -exec rm -rf {} +
