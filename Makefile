# LLM Cross-Compiler Framework - Makefile
# Enterprise CI/CD Entry Point for Linux/Headless
# DIREKTIVE: Goldstandard.

# Detect User ID for Docker Mapping to fix permission issues on Linux
UID := $(shell id -u)
GID := $(shell id -g)

.PHONY: help build up down clean test audit setup

help:
	@echo "LLM Framework - Build System"
	@echo "----------------------------"
	@echo "make setup    - Check & Install Requirements (Docker)"
	@echo "make build    - Build the Orchestrator images"
	@echo "make up       - Start the Orchestrator (Headless)"
	@echo "make gui      - Start the Orchestrator with GUI support"
	@echo "make down     - Stop all services"
	@echo "make clean    - Remove artifacts and cache"
	@echo "make test     - Run unit tests"
	@echo "make audit    - Run security audit (Trivy)"

setup:
	@echo "Running System Setup & Dependency Check..."
	@chmod +x scripts/setup_linux.sh
	@./scripts/setup_linux.sh

build: setup
	@echo "Building Infrastructure (UID: $(UID), GID: $(GID))..."
	UID=$(UID) GID=$(GID) docker-compose -f "Docker Setup/docker-compose.yml" build

up: setup
	@echo "Starting Orchestrator (Headless)..."
	UID=$(UID) GID=$(GID) GUI_ENABLED=false docker-compose -f "Docker Setup/docker-compose.yml" up -d orchestrator

gui: setup
	@echo "Starting Orchestrator (GUI)..."
	# Allow local docker user to access X11 for GUI (Linux specific)
	xhost +local:docker || true
	UID=$(UID) GID=$(GID) GUI_ENABLED=true docker-compose -f "Docker Setup/docker-compose.yml" up -d orchestrator

down:
	docker-compose -f "Docker Setup/docker-compose.yml" down

clean:
	docker-compose -f "Docker Setup/docker-compose.yml" down -v
	rm -rf output/* cache/*

test:
	@echo "Running Tests..."
	# Assuming we run tests inside the orchestrator container or via poetry locally
	poetry run pytest tests/

audit:
	@echo "Running Security Audit..."
	docker-compose -f "Docker Setup/docker-compose.yml" run --rm trivy-infra-scanner
