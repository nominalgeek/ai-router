.PHONY: help up down restart restart-all restart-gpu \
       logs logs-router logs-primary logs-ai \
       status health models gpu gpu-watch up-watch stats clean clean-all backup restore \
       venv test benchmark test-router test-primary pull update \
       shell-router shell-primary shell-ai validate network volumes prune review doc-review \
       boardroom-review

VENV_DIR := .venv
PYTHON := $(VENV_DIR)/bin/python
PIP := $(VENV_DIR)/bin/pip

help: ## Show this help message
	@echo 'Usage: make [target]'
	@echo ''
	@echo 'Available targets:'
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  %-15s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

venv: $(VENV_DIR)/bin/activate ## Create Python venv and install dependencies

$(VENV_DIR)/bin/activate: requirements.txt
	python3 -m venv $(VENV_DIR)
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt
	@touch $(VENV_DIR)/bin/activate
	@echo "Activate with: source $(VENV_DIR)/bin/activate"

up: ## Start all services (sequential GPU startup to avoid VRAM conflicts)
	@echo "Starting traefik..."
	docker compose up -d traefik
	@echo "Starting router model (small, loads first)..."
	docker compose up -d router
	@echo "Waiting for router to be healthy..."
	@until docker inspect --format='{{.State.Health.Status}}' vllm-router 2>/dev/null | grep -q healthy; do \
		sleep 5; echo "  router loading..."; \
	done
	@echo "✓ Router healthy — starting primary model..."
	docker compose up -d primary
	@echo "Waiting for primary to be healthy..."
	@until docker inspect --format='{{.State.Health.Status}}' vllm-primary 2>/dev/null | grep -q healthy; do \
		sleep 5; echo "  primary loading..."; \
	done
	@echo "✓ Primary healthy — starting ai-router..."
	docker compose up -d ai-router
	@echo "✓ All services started"

down: ## Stop all services (GPU containers first to release VRAM cleanly)
	docker compose down
	@echo "Waiting for GPU memory to release..."
	@while nvidia-smi --query-compute-apps=pid --format=csv,noheader 2>/dev/null | grep -q .; do \
		sleep 2; \
	done

restart: ## Restart ai-router only (no VRAM impact — use for code/prompt changes)
	docker compose restart ai-router

restart-all: down up ## Restart everything (sequential to avoid VRAM conflicts)

restart-gpu: ## Restart only GPU containers (sequential to avoid VRAM conflicts)
	@echo "Stopping GPU containers..."
	docker compose stop primary router
	@echo "Waiting for GPU memory to release..."
	@while nvidia-smi --query-compute-apps=pid --format=csv,noheader 2>/dev/null | grep -q .; do \
		sleep 2; \
	done
	@echo "GPU clear — starting router model..."
	docker compose start router
	@until docker inspect --format='{{.State.Health.Status}}' vllm-router 2>/dev/null | grep -q healthy; do \
		sleep 5; echo "  router loading..."; \
	done
	@echo "✓ Router healthy — starting primary model..."
	docker compose start primary

logs: ## Follow logs for all services
	docker compose logs -f

logs-router: ## Follow logs for router service
	docker compose logs -f router

logs-primary: ## Follow logs for primary service
	docker compose logs -f primary

logs-ai: ## Follow logs for ai-router service
	docker compose logs -f ai-router

status: ## Quick health check (one-line summary)
	@r=$$(curl -sf http://localhost/health 2>/dev/null | jq -r '.status // "unreachable"'); \
	 t=$$(curl -sf http://localhost:8080/api/overview >/dev/null 2>&1 && echo "up" || echo "down"); \
	 if [ "$$r" = "healthy" ] && [ "$$t" = "up" ]; then \
	   echo "✅ All systems healthy"; \
	 else \
	   echo "⚠️  router=$$r traefik=$$t"; \
	 fi

health: ## Check health of all services (verbose)
	@echo "=== Service Status ==="
	@docker compose ps
	@echo ""
	@echo "=== Health Checks ==="
	@echo "AI Router:"
	@curl -s http://localhost/health | jq . || echo "  ❌ Not responding"
	@echo ""
	@echo "Router Model:"
	@curl -s http://localhost/router/health | jq . || echo "  ❌ Not responding"
	@echo ""
	@echo "Primary Model:"
	@curl -s http://localhost/primary/health | jq . || echo "  ❌ Not responding"
	@echo ""
	@echo "Traefik Dashboard:"
	@curl -s http://localhost:8080/api/overview | jq . || echo "  ❌ Not responding"

models: ## List available models
	@echo "=== Router Models ==="
	@curl -s http://localhost/router/v1/models | jq .
	@echo ""
	@echo "=== Primary Models ==="
	@curl -s http://localhost/primary/v1/models | jq .

gpu: ## Show GPU status
	nvidia-smi

gpu-watch: ## Poll VRAM usage every 5s (Ctrl-C to stop)
	@echo "Polling VRAM every 5s — Ctrl-C to stop"
	@echo "timestamp,gpu_util%,vram_used_MiB,vram_total_MiB,vram_used%" ; \
	while true; do \
		nvidia-smi --query-gpu=timestamp,utilization.gpu,memory.used,memory.total,memory.free \
			--format=csv,noheader,nounits 2>/dev/null | \
		awk -F', ' '{used=$$3; total=$$4; pct=used/total*100; printf "%s  gpu=%s%%  vram=%s/%s MiB (%.1f%%)\n", $$1, $$2, $$3, $$4, pct}'; \
		sleep 5; \
	done

up-watch: ## Start all services while logging VRAM to logs/vram-startup.log
	@mkdir -p logs
	@echo "Starting VRAM monitor (logging to logs/vram-startup.log)..."
	@( echo "=== VRAM startup trace $$(date -Iseconds) ===" ; \
	   while true; do \
	     nvidia-smi --query-gpu=timestamp,memory.used,memory.total \
	       --format=csv,noheader,nounits 2>/dev/null | \
	     awk -F', ' '{pct=$$2/$$3*100; printf "%s  %s/%s MiB (%.1f%%)\n", $$1, $$2, $$3, pct}'; \
	     sleep 3; \
	   done ) > logs/vram-startup.log 2>&1 & \
	WATCH_PID=$$!; \
	echo "  monitor PID: $$WATCH_PID"; \
	echo "Starting traefik..."; \
	docker compose up -d traefik; \
	echo "Starting router model (small, loads first)..."; \
	docker compose up -d router; \
	echo "Waiting for router to be healthy..."; \
	until docker inspect --format='{{.State.Health.Status}}' vllm-router 2>/dev/null | grep -q healthy; do \
		sleep 5; echo "  router loading..."; \
	done; \
	echo "✓ Router healthy — VRAM after router:"; \
	tail -1 logs/vram-startup.log; \
	echo "Starting primary model..."; \
	docker compose up -d primary; \
	echo "Waiting for primary to be healthy..."; \
	until docker inspect --format='{{.State.Health.Status}}' vllm-primary 2>/dev/null | grep -q healthy; do \
		sleep 5; echo "  primary loading..."; \
	done; \
	echo "✓ Primary healthy — VRAM after primary:"; \
	tail -1 logs/vram-startup.log; \
	echo "Starting ai-router..."; \
	docker compose up -d ai-router; \
	echo "✓ All services started — stopping VRAM monitor"; \
	kill $$WATCH_PID 2>/dev/null; \
	echo ""; \
	echo "Full VRAM trace: logs/vram-startup.log"

stats: ## Show container resource usage
	docker stats --no-stream

clean: ## Remove containers and networks (keeps volumes)
	docker compose down

clean-all: ## Remove everything including volumes
	docker compose down -v
	@echo "⚠️  Cache cleared - next startup will re-download models"

backup: ## Backup Hugging Face cache
	@mkdir -p backups
	@echo "Creating backup..."
	@docker run --rm \
		-v ai-router_hf-cache:/data \
		-v $(PWD)/backups:/backup \
		ubuntu tar czf /backup/hf-cache-$$(date +%Y%m%d-%H%M%S).tar.gz /data
	@echo "✓ Backup created in backups/"

restore: ## Restore from latest backup
	@echo "Restoring from latest backup..."
	@docker run --rm \
		-v ai-router_hf-cache:/data \
		-v $(PWD)/backups:/backup \
		ubuntu bash -c "cd /data && tar xzf /backup/$$(ls -t /backup/*.tar.gz | head -1) --strip-components=1"
	@echo "✓ Restored"

test: ## Run full test suite
	./Test

benchmark: ## Run benchmark suite
	./Benchmark

review: ## Run session-review agent on accumulated logs
	$(PYTHON) agents/session-review/run.py

doc-review: ## Run doc-review agent to check docs against code
	$(PYTHON) agents/doc-review/run.py

boardroom-review: ## Run Improvement Board cycle (CEO → Challenger → QA)
	$(PYTHON) agents/boardroom_run.py

test-router: ## Test router model with sample request
	curl -X POST http://localhost/router/v1/chat/completions \
		-H "Content-Type: application/json" \
		-d '{"model": "cyankiwi/Nemotron-Orchestrator-8B-AWQ-4bit", "messages": [{"role": "user", "content": "Hello, how are you?"}], "max_tokens": 50}'

test-primary: ## Test primary model with sample request
	curl -X POST http://localhost/primary/v1/chat/completions \
		-H "Content-Type: application/json" \
		-d '{"model": "unsloth/NVIDIA-Nemotron-3-Nano-30B-A3B-NVFP4", "messages": [{"role": "user", "content": "Explain quantum computing briefly"}], "max_tokens": 200}'

pull: ## Pull latest images
	docker compose pull

update: pull down up ## Update to latest images

shell-router: ## Open shell in router container
	docker compose exec router /bin/bash

shell-primary: ## Open shell in primary container
	docker compose exec primary /bin/bash

shell-ai: ## Open shell in ai-router container
	docker compose exec ai-router /bin/bash

validate: ## Validate docker compose configuration
	docker compose config

network: ## Show network information
	docker network inspect ai-router_ai-network

volumes: ## Show volume information
	docker volume ls | grep ai-router

prune: ## Remove unused Docker resources
	docker system prune -f
	@echo "✓ Cleaned up unused resources"