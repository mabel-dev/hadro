VENV := .venv
PYTHON := python3
PORT ?= 8080

.PHONY: install run test lint clean

$(VENV):
	$(PYTHON) -m venv $(VENV)

install: $(VENV)
	$(VENV)/bin/pip install --upgrade pip
	$(VENV)/bin/pip install -e '.[test,gcs]'

run: install
	$(VENV)/bin/hadro data --port $(PORT)

test: install
	$(VENV)/bin/pytest

lint:
	$(VENV)/bin/pip install --quiet ruff
	$(VENV)/bin/ruff check --fix src tests
	$(VENV)/bin/ruff format src tests

clean:
	rm -rf $(VENV) build dist src/*.egg-info
