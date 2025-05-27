# Versioning
version_full ?= $(shell $(MAKE) --silent version-full)
version_small ?= $(shell $(MAKE) --silent version)
# Dev tunnels configuration
tunnel_name := call-center-ai-$(shell hostname | sed 's/[^a-zA-Z0-9]//g' | tr '[:upper:]' '[:lower:]')
tunnel_url ?= $(shell res=$$(devtunnel show $(tunnel_name) | grep -o 'http[s]*://[^ ]*' | xargs) && echo $${res%/})
# Container configuration
container_name := ghcr.io/clemlesne/call-center-ai
image_version := main
# App location
# Warning: Some regions may not support all services (e.g. OpenAI models, AI Search) or capabilities (e.g. Cognitive Services TTS voices). Those regions have been tested and are known to work. If you encounter issues, please refer to the Azure documentation for the latest information, or try deploying with default locations.
cognitive_communication_location := westeurope
default_location := swedencentral
openai_location := swedencentral
search_location := francecentral
# Sanitize variables
name_sanitized := $(shell echo $(name) | tr '[:upper:]' '[:lower:]')
# App configuration
twilio_phone_number ?= $(shell cat config.yaml | yq '.sms.twilio.phone_number')
# Bicep inputs
prompt_content_filter ?= true
# Bicep outputs
app_url ?= $(shell az deployment sub show --name $(name_sanitized) | yq '.properties.outputs["appUrl"].value')
blob_storage_public_name ?= $(shell az deployment sub show --name $(name_sanitized) | yq '.properties.outputs["blobStoragePublicName"].value')
container_app_name ?= $(shell az deployment sub show --name $(name_sanitized) | yq '.properties.outputs["containerAppName"].value')

version:
	@bash ./cicd/version/version.sh -g . -c

version-full:
	@bash ./cicd/version/version.sh -g . -c -m

brew:
	@echo "➡️ Installing yq..."
	brew install yq

	@echo "➡️ Installing Azure CLI..."
	brew install azure-cli

	@echo "➡️ Installing pyenv..."
	brew install pyenv

	@echo "➡️ Installing Rust..."
	brew install rust

	@echo "➡️ Installing Azure Dev tunnels..."
	curl -sL https://aka.ms/DevTunnelCliInstall | bash

	@echo "➡️ Installing Twilio CLI..."
	brew tap twilio/brew && brew install twilio

	@echo "➡️ Installing uv..."
	brew install uv

install:
	@echo "➡️ Installing venv..."
	uv venv --python 3.13 --allow-existing

	$(MAKE) install-deps

install-deps:
	@echo "➡️ Syncing dependencies..."
	uv sync --extra dev

upgrade:
	@echo "➡️ Updating Git submodules..."
	git submodule update --init --recursive

	@echo "➡️ Compiling requirements..."
	uv lock --upgrade

	@echo "➡️ Upgrading Bicep CLI..."
	az bicep upgrade

test:
	$(MAKE) test-static
	$(MAKE) test-unit

test-static:
	@echo "➡️ Test Python code style..."
	uv run ruff check --select I,PL,RUF,UP,ASYNC,A,DTZ,T20,ARG,PERF --ignore RUF012,A005

	@echo "➡️ Test Python type hints..."
	uv run pyright .

	@echo "➡️ Test Bicep code style..."
	az bicep lint --file cicd/bicep/main.bicep

test-unit:
	@echo "➡️ Unit tests (Pytest)..."
	PUBLIC_DOMAIN=dummy uv run pytest \
		--junit-xml=test-reports/$(version_full).xml \
		tests/*.py

lint:
	@echo "➡️ Fix Python code style..."
	uv run ruff check --select I,PL,RUF,UP,ASYNC,A,DTZ,T20,ARG,PERF --ignore RUF012,A005 --fix

tunnel:
	@echo "➡️ Creating tunnel..."
	devtunnel show $(tunnel_name) || devtunnel create $(tunnel_name) --allow-anonymous --expiration 1d

	@echo "➡️ Creating port forwarding..."
	devtunnel port show $(tunnel_name) --port-number 8080 || devtunnel port create $(tunnel_name) --port-number 8080

	@echo "➡️ Starting tunnel..."
	devtunnel host $(tunnel_name)

dev:
	VERSION=$(version_full) PUBLIC_DOMAIN=$(tunnel_url) uv run granian \
		--host 0.0.0.0 \
		--interface asgi \
		--log-level info \
		--port 8080 \
		--reload \
		--workers 2 \
		--workers-kill-timeout 60 \
		app.main:api

build:
	DOCKER_BUILDKIT=1 docker build \
		--build-arg VERSION=$(version_full) \
		--file cicd/Dockerfile \
		--platform linux/amd64,linux/arm64 \
		--tag $(container_name):$(version_small) \
		--tag $(container_name):latest \
		.

deploy:
	$(MAKE) deploy-bicep

	@echo "🚀 Call Center AI is running on $(app_url)"

	@$(MAKE) deploy-post

deploy-bicep:
	@echo "👀 Current subscription:"
	@az account show --query "{subscriptionId:id, subscriptionName:name, tenantId:tenantId}" --output table

	@echo "🛠️ Deploying resources..."
	az deployment sub create \
		--location $(default_location) \
		--parameters \
			'cognitiveCommunicationLocation=$(cognitive_communication_location)' \
			'imageVersion=$(image_version)' \
			'instance=$(name)' \
			'openaiLocation=$(openai_location)' \
			'promptContentFilter=$(prompt_content_filter)' \
			'searchLocation=$(search_location)' \
		--template-file cicd/bicep/main.bicep \
	 	--name $(name_sanitized)

deploy-post:
	@$(MAKE) copy-public \
		name=$(blob_storage_public_name)

	@$(MAKE) twilio-register \
		endpoint=$(app_url)

	@$(MAKE) logs name=$(name_sanitized)

destroy:
	@echo "🧐 Are you sure you want to delete? Type 'delete now $(name_sanitized)' to confirm."
	@read -r confirm && [ "$$confirm" = "delete now $(name_sanitized)" ] || (echo "Confirmation failed. Aborting."; exit 1)

	@echo "❗️ Deleting RG..."
	az group delete --name $(name_sanitized) --yes --no-wait

	@echo "❗️ Deleting deployment..."
	az deployment sub delete --name $(name_sanitized)

logs:
	az containerapp logs show \
		--follow \
		--format text \
		--name call-center-ai \
		--resource-group $(name) \
		--tail 100

twilio-register:
	@echo "⚙️ Registering Twilio webhook..."
	twilio phone-numbers:update $(twilio_phone_number) \
		--sms-url $(endpoint)/twilio/sms

copy-public:
	@echo "📦 Copying public resources..."
	az storage blob upload-batch \
		--account-name $(name_sanitized) \
		--auth-mode login \
		--destination '$$web' \
		--no-progress \
		--output none \
		--overwrite \
		--source public

watch-call:
	@echo "👀 Watching status of $(phone_number)..."
	while true; do \
		clear; \
		curl -s "$(endpoint)/call?phone_number=%2B$(phone_number)" | yq --prettyPrint '.[0] | {"phone_number": .initiate.phone_number, "claim": .claim, "reminders": .reminders}'; \
		sleep 3; \
	done

sync-local-config:
	@echo "📥 Copying remote CONFIG_JSON to local config..."
	az containerapp revision list \
			--name $(container_app_name) \
			--output tsv \
			--query "[0].properties.template.containers[0].env[?name=='CONFIG_JSON'].value" \
			--resource-group $(name_sanitized) \
		| iconv -f utf-8 -t utf-8 -c \
		| yq eval 'del(.cache)' \
			--output-format=yaml \
			--prettyPrint \
		> config.yaml
