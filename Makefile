# LLM Cross-Compiler Framework - Makefile
# Shortcuts for development (tests, git hooks, secret scan) and for the framework in a container that you build on
# this computer. No framework image is published or pulled: "make build" builds it locally with
# scripts/build_container_image.sh, and the compose file never pulls it. Guide: docs/Container_Setup.md.

# Shell setup for consistency
SHELL := /bin/bash

# Docker Compose v2 (the docker compose plugin). LCF_COMPOSE_OVERRIDE names a compose file of your own that is
# applied on top (for example the Docker secret with your vault key, see docs/Container_Setup.md).
LCF_COMPOSE_OVERRIDE ?=
COMPOSE := docker compose -f "Docker Setup/docker-compose.yml" $(if $(LCF_COMPOSE_OVERRIDE),-f "$(LCF_COMPOSE_OVERRIDE)")
IMAGE ?= lcf-framework:local
GUI_IMAGE ?= lcf-framework:local-gui
# The vulnerability scanner for "make audit": the same pinned version the framework uses (setting image_trivy).
TRIVY_IMAGE ?= aquasec/trivy:0.75.0

.PHONY: help build build-gui up gui down doctor shell clean test audit hooks secret-scan

help:
	@echo "LLM Framework - Build System"
	@echo "----------------------------"
	@echo "make build          - Build the framework's container image on this computer (headless: CLI and MCP server)"
	@echo "make build-gui      - Build it with the desktop libraries for the GUI (explicit option)"
	@echo "make up             - Start the headless framework as the container lcf-framework"
	@echo "make gui            - Start the GUI in a container (needs make build-gui and an X11 display)"
	@echo "make down           - Stop the containers (the data volume lcf-data stays)"
	@echo "make doctor         - Run llm-cli doctor in the running container"
	@echo "make shell          - Open a shell in the running container"
	@echo "make clean          - Remove the stopped containers and show what holds data (deletes no data)"
	@echo "make test           - Run unit tests (Host execution)"
	@echo "make audit          - Scan the built image for vulnerabilities (Trivy, from an image archive)"
	@echo "make hooks          - Enable the git hooks that refuse commits/pushes with credentials"
	@echo "make secret-scan    - Scan the whole git history for credentials"

build:
	scripts/build_container_image.sh --tag "$(IMAGE)"

build-gui:
	scripts/build_container_image.sh --gui --tag "$(GUI_IMAGE)"

up:
	LCF_IMAGE="$(IMAGE)" $(COMPOSE) up -d framework
	@echo "Running as lcf-framework. MCP server for an AI client: docker exec -i lcf-framework llm-cli mcp serve"

gui:
	@echo "The GUI container gets access to your X11 display (screen and keyboard input)."
	xhost "+SI:localuser:$$(id -un)" >/dev/null
	LCF_GUI_IMAGE="$(GUI_IMAGE)" $(COMPOSE) --profile gui up -d gui

down:
	$(COMPOSE) --profile gui down

doctor:
	$(COMPOSE) exec framework llm-cli doctor

shell:
	$(COMPOSE) exec framework bash

# Deletes nothing you can see as data: the volume and the folders below stay until you remove them yourself.
clean:
	$(COMPOSE) --profile gui down --remove-orphans
	@echo "Kept (remove them yourself if you no longer need them):"
	@echo "  volume lcf-data (settings, credential vault, audit log, MCP tokens, models, builds): docker volume rm lcf-data"
	@for d in output cache; do if [ -d "$$d" ]; then echo "  $$(pwd)/$$d ($$(du -sh "$$d" | cut -f1)): build results and cache of a development run"; fi; done

test:
	@echo "Running Tests (Local Environment)..."
	# Requires poetry installed on host
	poetry run pytest tests/

# The image is saved to an archive and scanned from there: the scanner never gets the Docker socket.
audit:
	@set -euo pipefail; tmp=$$(mktemp -d); trap 'rm -rf "$$tmp"' EXIT; \
	docker image save -o "$$tmp/image.tar" "$(IMAGE)"; chmod 0644 "$$tmp/image.tar"; \
	docker run --rm --cap-drop ALL --security-opt no-new-privileges:true -v "$$tmp/image.tar:/image.tar:ro" \
		"$(TRIVY_IMAGE)" image --input /image.tar --severity HIGH,CRITICAL --exit-code 0

hooks:
	git config core.hooksPath .githooks
	@echo "Secret scan hooks enabled (pre-commit, pre-push)."

secret-scan:
	python3 scripts/secret_scan.py --history
