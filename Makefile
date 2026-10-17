PY ?= python

.PHONY: install lint format test eval demo links secrets check run docker-build docker-run

install:
	$(PY) -m pip install -r requirements-dev.txt

lint:
	$(PY) -m ruff check .

format:
	$(PY) -m ruff format .

test:
	$(PY) -m pytest -q

eval:
	$(PY) -m evals.run

demo:
	$(PY) -m uvicorn api.main:app --host 127.0.0.1 --port 8000

links:
	$(PY) scripts/check_links.py

secrets:
	$(PY) scripts/check_no_secrets.py

check: lint test links secrets

run:
	$(PY) -m uvicorn api.main:app --host 127.0.0.1 --port 8000 --reload

docker-build:
	docker build -t agent-wallet:dev .

docker-run:
	docker run --rm -p 8000:8000 --env-file .env agent-wallet:dev
