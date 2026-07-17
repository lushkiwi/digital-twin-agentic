# Digital Twin Agentic Layer — dev workflow
# Copy .env.example to .env first: make env
-include .env
export

COMPOSE := docker compose -f docker/docker-compose.ditto.yml
SIM_PORT := $(or $(SIM_DEBUG_PORT),9001)
PY := .venv/bin/python

.PHONY: env venv ditto-up ditto-down ditto-reset things thing sim backend frontend \
        fault-bearing fault-overheat fault-leak fault-stuck fault-clear sim-state demo help

env: ## Create .env from template if missing
	@test -f .env || (cp .env.example .env && echo "Created .env — add your API key(s)")

venv: ## Create python venv and install backend + sim deps
	python3 -m venv .venv
	.venv/bin/pip install -q --upgrade pip
	.venv/bin/pip install -q -r backend/requirements.txt -r device-sim/requirements.txt
	@echo "venv ready"

ditto-up: ## Start Eclipse Ditto stack and wait until the API answers
	$(COMPOSE) up -d
	@echo "Waiting for Ditto gateway ..."
	@for i in $$(seq 1 90); do \
	  code=$$(curl -s -o /dev/null -w '%{http_code}' -u ditto:ditto http://localhost:8080/api/2/things/org.acme:motor-01 || true); \
	  if [ "$$code" = "200" ] || [ "$$code" = "404" ]; then echo "Ditto is up ($$code)"; exit 0; fi; \
	  sleep 2; \
	done; echo "Ditto did not come up in time" >&2; exit 1

ditto-down: ## Stop Ditto stack (keeps data)
	$(COMPOSE) down

ditto-reset: ## Stop Ditto stack and wipe all data
	$(COMPOSE) down -v

things: ## Create/reset all four component twins in Ditto
	bash scripts/create_things.sh

thing: things ## Alias for things (v1 compat)

sim: ## Run the device simulator (foreground)
	$(PY) -m uvicorn sim:app --app-dir device-sim --host 127.0.0.1 --port $(SIM_PORT)

backend: ## Run the FastAPI backend (foreground)
	$(PY) -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port $(or $(BACKEND_PORT),8000)

frontend: ## Run the Vite dev server (foreground)
	cd frontend && npm run dev

fault-bearing: ## Inject motor bearing fault (the cascade demo)
	curl -s -X POST http://localhost:$(SIM_PORT)/fault/motor/bearing && echo

fault-overheat: ## Inject motor overheat fault
	curl -s -X POST http://localhost:$(SIM_PORT)/fault/motor/overheat && echo

fault-leak: ## Inject pump leak fault
	curl -s -X POST http://localhost:$(SIM_PORT)/fault/pump/leak && echo

fault-stuck: ## Inject valve stuck fault
	curl -s -X POST http://localhost:$(SIM_PORT)/fault/valve/stuck && echo

fault-clear: ## Clear all injected faults
	curl -s -X POST http://localhost:$(SIM_PORT)/fault/clear && echo

sim-state: ## Show simulator internal state
	curl -s http://localhost:$(SIM_PORT)/state | $(PY) -m json.tool

demo: env ditto-up things ## One-shot bring-up: Ditto + things (then run sim/backend/frontend in 3 terminals)
	@echo ""
	@echo "Ditto is ready. Now run in three terminals:"
	@echo "  make sim"
	@echo "  make backend"
	@echo "  make frontend"

help: ## Show targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'
