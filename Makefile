# LLM Cross-Compiler Framework - Makefile
# Enterprise CI/CD Entry Point for Linux/Headless
# DIREKTIVE: Goldstandard.

# Shell setup for consistency
SHELL := /bin/bash

# Configuration
COMPOSE_FILE := "Docker Setup/docker-compose.yml"

# Detect User ID for Docker Mapping to fix permission issues on Linux
UID := $(shell id -u)
GID := $(shell id -g)

.PHONY: help build up down clean test test-container audit setup

help:
	@echo "LLM Framework - Build System"
	@echo "----------------------------"
	@echo "make setup          - Check & Install Requirements (Docker)"
	@echo "make build          - Build the Orchestrator images"
	@echo "make up             - Start the Orchestrator (Headless)"
	@echo "make gui            - Start the Orchestrator with GUI support"
	@echo "make down           - Stop all services"
	@echo "make clean          - Remove artifacts and cache"
	@echo "make test           - Run unit tests (Host execution)"
	@echo "make test-container - Run unit tests (Docker execution - Recommended)"
	@echo "make audit          - Run security audit (Trivy)"

setup:
	@echo "Running System Setup & Dependency Check..."
	@chmod +x scripts/setup_linux.sh
	@./scripts/setup_linux.sh

build: setup
	@echo "Building Infrastructure (UID: $(UID), GID: $(GID))..."
	UID=$(UID) GID=$(GID) docker-compose -f $(COMPOSE_FILE) build

up: setup
	@echo "Starting Orchestrator (Headless)..."
	UID=$(UID) GID=$(GID) GUI_ENABLED=false docker-compose -f $(COMPOSE_FILE) up -d orchestrator

gui: setup
	@echo "Starting Orchestrator (GUI)..."
	# Allow local docker user to access X11 for GUI (Linux specific)
	xhost +local:docker || true
	UID=$(UID) GID=$(GID) GUI_ENABLED=true docker-compose -f $(COMPOSE_FILE) up -d orchestrator

down:
	docker-compose -f $(COMPOSE_FILE) down

clean:
	docker-compose -f $(COMPOSE_FILE) down -v
	rm -rf output/* cache/*

test:
	@echo "Running Tests (Local Environment)..."
	# Requires poetry installed on host
	poetry run pytest tests/

test-container:
	@echo "Running Tests (Containerized Environment)..."
	# Runs tests inside the container -> Ensures 100% env match with production
	UID=$(UID) GID=$(GID) docker-compose -f $(COMPOSE_FILE) run --rm orchestrator pytest tests/

audit:
	@echo "Running Security Audit..."
	docker-compose -f $(COMPOSE_FILE) run --rm trivy-infra-scanner
