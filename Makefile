.PHONY: help up down restart logs health clean backup restore

help: ## Show this help message
	@echo 'Usage: make [target]'
	@echo ''
	@echo 'Available targets:'
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  %-15s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

up: ## Start all services
	docker compose up -d

down: ## Stop all services
	docker compose down

restart: ## Restart all services
	docker compose restart

logs: ## Follow logs for all services
	docker compose logs -f

logs-router: ## Follow logs for router service
	docker compose logs -f router

logs-primary: ## Follow logs for primary service
	docker compose logs -f primary

logs-ai: ## Follow logs for ai-router service
	docker compose logs -f ai-router

health: ## Check health of all services
	@echo "=== Service Status ==="
	@docker compose ps
	@echo ""
	@echo "=== Health Checks ==="
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
		-v ai-routing_hf-cache:/data \
		-v $(PWD)/backups:/backup \
		ubuntu tar czf /backup/hf-cache-$$(date +%Y%m%d-%H%M%S).tar.gz /data
	@echo "✓ Backup created in backups/"

restore: ## Restore from latest backup
	@echo "Restoring from latest backup..."
	@docker run --rm \
		-v ai-routing_hf-cache:/data \
		-v $(PWD)/backups:/backup \
		ubuntu bash -c "cd /data && tar xzf /backup/$$(ls -t /backup/*.tar.gz | head -1) --strip-components=1"
	@echo "✓ Restored"

test-router: ## Test router model with sample request
	curl -X POST http://localhost/router/v1/chat/completions \
		-H "Content-Type: application/json" \
		-d '{"model": "unsloth/NVIDIA-Nemotron-3-Nano-30B-A3B-NVFP4", "messages": [{"role": "user", "content": "Hello, how are you?"}], "max_tokens": 50}'

test-primary: ## Test primary model with sample request
	curl -X POST http://localhost/primary/v1/chat/completions \
		-H "Content-Type: application/json" \
		-d '{"model": "unsloth/DeepSeek-R1-Distill-Llama-8B-unsloth-bnb-4bit", "messages": [{"role": "user", "content": "Explain quantum computing briefly"}], "max_tokens": 200}'

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
	docker network inspect ai-routing_ai-network

volumes: ## Show volume information
	docker volume ls | grep ai-routing

prune: ## Remove unused Docker resources
	docker system prune -f
	@echo "✓ Cleaned up unused resources"