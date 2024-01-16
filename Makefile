container_name := ghcr.io/clemlesne/claim-ai-phone-bot
docker := docker
version_full ?= $(shell $(MAKE) --silent version-full)
version_small ?= $(shell $(MAKE) --silent version)
tunnel_name := claim-ai-phone-bot-$(shell hostname | tr '[:upper:]' '[:lower:]')
tunnel_url := $(shell devtunnel show $(tunnel_name) | grep -o 'http[s]*://[^"]*')

version:
	@bash ./cicd/version/version.sh -g . -c

version-full:
	@bash ./cicd/version/version.sh -g . -c -m

install:
	@echo "➡️ Installing Dev Tunnels CLI..."
	devtunnel --version || brew install --cask devtunnel

	@echo "➡️ Installing Python dependencies..."
	python3 -m pip install -r requirements.txt

upgrade:
	@echo "➡️ Upgrading pip..."
	python3 -m pip install --upgrade pip

	@echo "➡️ Upgrading Python dependencies..."
	pur -r requirements.txt

test:
	@echo "➡️ Running Black..."
	python3 -m black --check .

lint:
	@echo "➡️ Running Black..."
	python3 -m black .

tunnel:
	@echo "➡️ Creating tunnel..."
	devtunnel show $(tunnel_name) || devtunnel create $(tunnel_name) --allow-anonymous --expiration 1d

	@echo "➡️ Creating port forwarding..."
	devtunnel port show $(tunnel_name) --port-number 8080 || devtunnel port create $(tunnel_name) --port-number 8080

	@echo "➡️ Starting tunnel..."
	devtunnel host $(tunnel_name)

dev:
	VERSION=$(version_full) EVENTS_DOMAIN=$(tunnel_url) python3 -m uvicorn main:api \
		--header x-version:$${VERSION} \
		--no-server-header \
		--port 8080 \
		--proxy-headers \
		--timeout-keep-alive 60 \
		--reload


build:
	$(docker) build \
		--build-arg VERSION=$(version_full) \
		--tag $(container_name):$(version_small) \
		--tag $(container_name):latest \
		.

run:
	$(docker) run \
		--env EVENTS_DOMAIN=$(tunnel_url) \
		--env VERSION=$(version_full) \
		--mount type=bind,source="$(CURDIR)/.env",target="/app/.env" \
		--mount type=bind,source="$(CURDIR)/config.yaml",target="/app/config.yaml" \
		--name claim-ai-phone-bot \
		--publish 8080:8080 \
		--rm \
		$(container_name):$(version_small)

stop:
	@echo "Stopping container..."
	$(docker) stop claim-ai-phone-bot
