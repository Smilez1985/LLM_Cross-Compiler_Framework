# LLM Cross-Compiler Framework - Makefile
# Shortcuts for development (tests, git hooks, secret scan) and for the framework in a container that you build on
# this computer. No framework image is published or pulled: "make build" builds it locally with
# scripts/build_container_image.sh, and the compose file never pulls it. Guide: docs/Container_Setup.md.

# Shell setup for consistency
SHELL := /bin/bash

IMAGE ?= lcf-framework:local
GUI_IMAGE ?= lcf-framework:local-gui
# Docker Compose v2 (the docker compose plugin). LCF_COMPOSE_OVERRIDE names a compose file of your own that is
# applied on top (for example the Docker secret with your vault key, see docs/Container_Setup.md).
LCF_COMPOSE_OVERRIDE ?=
COMPOSE := LCF_IMAGE="$(IMAGE)" LCF_GUI_IMAGE="$(GUI_IMAGE)" docker compose -f "Docker Setup/docker-compose.yml" \
	$(if $(LCF_COMPOSE_OVERRIDE),-f "$(LCF_COMPOSE_OVERRIDE)")
# Targets you create and packs you install live in the container's program folder, not in the data volume. No target
# here removes or replaces a container that holds such files of yours; this script lists them.
KEPT := scripts/container_kept_files.sh
# The vulnerability scanner for "make audit": the same pinned version the framework uses (setting image_trivy).
TRIVY_IMAGE ?= aquasec/trivy:0.75.0

.PHONY: help build build-gui up gui down kept-files doctor mojo shell clean test audit hooks secret-scan

help:
	@echo "LLM Framework - Build System"
	@echo "----------------------------"
	@echo "make build          - Build the framework's container image on this computer (headless: CLI and MCP server)"
	@echo "make build-gui      - Build it with the desktop libraries for the GUI (explicit option)"
	@echo "make up             - Start the headless framework as the container lcf-framework"
	@echo "make gui            - Start the GUI in a container (needs make build-gui and an X11 display)"
	@echo "make down           - Stop the containers (they, what they hold and the data volume lcf-data stay)"
	@echo "make kept-files     - Show the files of yours the containers hold outside the data volume"
	@echo "make mojo           - Check the installation in the running container (Mojo Oracle)"
	@echo "make doctor         - The same under its old name (llm-cli doctor)"
	@echo "make shell          - Open a shell in the running container"
	@echo "make clean          - Remove the containers that hold nothing of yours and show what holds data (deletes no data)"
	@echo "make test           - Run unit tests (Host execution)"
	@echo "make audit          - Scan the built image for vulnerabilities (Trivy, from an image archive)"
	@echo "make hooks          - Enable the git hooks that refuse commits/pushes with credentials"
	@echo "make secret-scan    - Scan the whole git history for credentials"

build:
	scripts/build_container_image.sh --tag "$(IMAGE)"

build-gui:
	scripts/build_container_image.sh --gui --tag "$(GUI_IMAGE)"

# Starts a service. Compose replaces a container when its image or settings changed, and everything in it outside the
# data volume goes with it: a container that holds files of yours is therefore started as it is and never replaced,
# and you see those files and how to move on. $(1) service, $(2) container, $(3) image, $(4) compose options.
define start_service
	@set -uo pipefail; \
	kept=$$($(KEPT) $(2)); rc=$$?; \
	if [ $$rc -eq 0 ]; then \
		$(COMPOSE) $(4) up -d $(1); \
	elif [ $$rc -eq 3 ]; then \
		$(COMPOSE) $(4) up -d --no-recreate $(1) || exit $$?; \
		if [ "$$(docker container inspect -f '{{.Image}}' $(2))" = "$$(docker image inspect -f '{{.Id}}' "$(3)" 2>/dev/null)" ]; then \
			echo "$(2) holds files of yours outside the data volume (make kept-files lists them): it is started as it is and never replaced."; \
		else \
			echo "$(2) keeps running its old image: it holds files of yours outside the data volume, and replacing it by $(3) would delete them:"; \
			printf '%s\n' "$$kept"; \
			echo "To move to the new image, copy what you need into /data first (a target: docker exec $(2) llm-cli zoo export target NAME --author \"<your name>\" --out /data/packs), then remove the old container yourself (docker rm -f $(2)) and run make $@ again."; \
		fi; \
	else \
		exit $$rc; \
	fi
endef

up:
	$(call start_service,framework,lcf-framework,$(IMAGE),)
	@echo "Running as lcf-framework. MCP server for an AI client: docker exec -i lcf-framework llm-cli mcp serve"

gui:
	@echo "The GUI container gets access to your X11 display (screen and keyboard input)."
	xhost "+SI:localuser:$$(id -un)" >/dev/null
	$(call start_service,gui,lcf-framework-gui,$(GUI_IMAGE),--profile gui)

# Stops the containers and removes none: what they hold outside the data volume stays.
down:
	$(COMPOSE) --profile gui stop

kept-files:
	@set -uo pipefail; \
	for container in lcf-framework lcf-framework-gui; do \
		kept=$$($(KEPT) "$$container"); rc=$$?; \
		if [ $$rc -eq 0 ]; then echo "$$container: nothing of yours outside the data volume (or no such container)"; \
		elif [ $$rc -eq 3 ]; then echo "$$container holds these files of yours outside the data volume:"; printf '%s\n' "$$kept"; \
		else exit $$rc; fi; \
	done

mojo:
	$(COMPOSE) exec framework llm-cli mojo

doctor:
	$(COMPOSE) exec framework llm-cli doctor

shell:
	$(COMPOSE) exec framework bash

# Deletes nothing you can see as data: a container is removed only when it holds nothing of yours outside the data
# volume; the others, the volume and the folders below stay until you remove them yourself. No --remove-orphans:
# a container of this project that the compose files do not name (a service from an earlier LCF_COMPOSE_OVERRIDE) was
# never checked, so it stays.
clean:
	@set -uo pipefail; all=1; \
	for pair in framework:lcf-framework gui:lcf-framework-gui; do \
		service=$${pair%%:*}; container=$${pair#*:}; \
		kept=$$($(KEPT) "$$container"); rc=$$?; \
		if [ $$rc -eq 0 ]; then \
			$(COMPOSE) --profile gui rm --stop --force "$$service" || exit $$?; \
		elif [ $$rc -eq 3 ]; then \
			all=0; \
			echo "Kept the container $$container: it holds these files of yours outside the data volume:"; \
			printf '%s\n' "$$kept"; \
			echo "  Copy what you need into /data, then remove it yourself: docker rm -f $$container"; \
		else \
			exit $$rc; \
		fi; \
	done; \
	if [ $$all -eq 1 ]; then $(COMPOSE) --profile gui down; fi
	@echo "Kept (remove them yourself if you no longer need them):"
	@echo "  volume lcf-data (settings, credential vault, audit log, MCP tokens, models, builds): docker volume rm lcf-data"
	@for d in output cache; do if [ -d "$$d" ]; then echo "  $$(pwd)/$$d ($$(du -sh "$$d" | cut -f1)): build results and cache of a development run"; fi; done

test:
	@echo "Running Tests (Local Environment)..."
	# Requires uv on the host: the environment comes from uv.lock (--locked stops when it no longer matches pyproject.toml)
	QT_QPA_PLATFORM=offscreen uv run --locked pytest tests/

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
