# Bank — commandes courantes.  make help
SHELL := /bin/bash
SP ?= backend/.venv/bin
.PHONY: help secrets up down logs build test lint fiscal-test front-test dev-api dev-web demo backup restore-check check

help: ## Liste des commandes
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-12s %s\n", $$1, $$2}'

secrets: ## Génère ./secrets (ne remplace jamais un secret existant)
	@bash scripts/gen-secrets.sh

build: ## Construit les 3 images (EXTRA_CA=chemin/ca.crt derrière un proxy TLS)
	@for s in "api backend" "fiscal fiscal-engine" "web frontend"; do set -- $$s; \
	  docker build $${EXTRA_CA:+--secret id=extra_ca,src=$$EXTRA_CA} -t bank-$$1:latest $$2 || exit 1; done

up: ## Lance la pile sur nimbus
	@test -f .env || (echo "Copie d'abord .env.example en .env" && exit 1)
	@test -s secrets/master_key || (echo "Lance d'abord : make secrets" && exit 1)
	docker compose up -d --no-build
	@echo "Jeton d'installation : $$(cat secrets/setup_token)"

down: ## Arrête la pile
	docker compose down

logs: ## Journaux (dont l'ancrage de la chaîne d'audit)
	docker compose logs -f --tail=100

backup: ## Sauvegarde chiffrée à chaud dans le volume (/data/backups) et le second emplacement s'il est réglé
	docker compose exec api python -m scripts.backup

restore-check: ## Vérifie que la dernière sauvegarde se relit (FILE=bank-….bank pour en choisir une)
	docker compose exec api sh -c 'python -m scripts.restore --check /data/backups/$${FILE:-$$(ls /data/backups/*.bank | tail -1 | xargs basename)}'

lint: ## Ruff, mypy strict, bandit, shellcheck
	cd backend && .venv/bin/ruff check app tests && .venv/bin/ruff format --check app tests && .venv/bin/mypy app && .venv/bin/bandit -q -r app -c pyproject.toml
	@if command -v shellcheck >/dev/null; then shellcheck scripts/*.sh integrations/hermes/install.sh; else echo "shellcheck absent : scripts non vérifiés"; fi

test: ## Tests backend + couverture
	cd backend && .venv/bin/pytest --cov=app

front-test: ## Typage + tests + build du front
	cd frontend && npm run typecheck && npm test && npm run build

fiscal-test: ## Tests contre le vrai OpenFisca (venv dédié dans fiscal-engine/.venv)
	cd fiscal-engine && (test -d .venv || (python3 -m venv .venv && .venv/bin/pip install -r requirements.txt pytest httpx2)) && .venv/bin/pytest test_engine.py -q

check: lint test front-test ## Tout ce que la CI vérifie (hors images)

dev-api: ## API en local (http://127.0.0.1:8000), base data/dev.db
	cd backend && mkdir -p data && BANK_ENV=dev BANK_DATABASE_URL=sqlite:///./data/dev.db \
	  BANK_MASTER_KEY=$$(cat ../secrets/master_key) BANK_SETUP_TOKEN=$$(cat ../secrets/setup_token) \
	  BANK_PUBLIC_URL=http://localhost:5173 BANK_FISCAL_ENGINE_URL=http://127.0.0.1:8001 \
	  BANK_FISCAL_ENGINE_TOKEN=$$(cat ../secrets/fiscal_token) BANK_SYNC_INTERVAL_HOURS=0 \
	  .venv/bin/uvicorn app.main:app_factory --factory --reload --port 8000

dev-web: ## Front en local (http://localhost:5173)
	cd frontend && npm run dev

demo: ## Base de démonstration (4 mois d'opérations fictives) dans backend/data/demo.db
	cd backend && mkdir -p data && BANK_ENV=dev BANK_DATABASE_URL=sqlite:///./data/demo.db \
	  BANK_MASTER_KEY=$$(cat ../secrets/master_key) .venv/bin/python -m scripts.demo_seed
