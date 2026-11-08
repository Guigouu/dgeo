# SPDX-License-Identifier: AGPL-3.0-or-later
.PHONY: up down logs test demo shell local-ui local-test

up:            ## build and start the UI on http://localhost:8501
	mkdir -p data && docker compose up -d --build
	@echo "UI: http://localhost:8501"

down:
	docker compose down

logs:
	docker compose logs -f

test:          ## run the tests inside the container
	docker compose run --rm --no-deps dgeo python -m pytest -q tests

demo:          ## synthetic CLI demo inside the container (outputs in ./data/demo)
	docker compose run --rm --no-deps dgeo python -m dgeo demo --out-dir /data/demo

shell:
	docker compose run --rm --no-deps dgeo bash

local-ui:      ## without Docker (virtualenv)
	DGEO_DATA=./data streamlit run app/app.py

local-test:
	python -m pytest -q tests
