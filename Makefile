# Versioning
version_full ?= $(shell $(MAKE) --silent version-full)
version_small ?= $(shell $(MAKE) --silent version)
# Dev tunnels configuration
tunnel_name := call-center-ai-$(shell hostname | sed 's/[^a-zA-Z0-9]//g' | tr '[:upper:]' '[:lower:]')
tunnel_url ?= $(shell res=$$(devtunnel show $(tunnel_name) | grep -o 'http[s]*://[^"]*' | xargs) && echo $${res%/})
# Container configuration
container_name := ghcr.io/clemlesne/call-center-ai
docker := docker
image_version := main
# App location
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

install:
	@echo "➡️ Installing pip-tools..."
	python3 -m pip install pip-tools

	@echo "➡️ Syncing dependencies..."
	pip-sync --pip-args "--no-deps" requirements-dev.txt

upgrade:
	@echo "➡️ Updating Git submodules..."
	git submodule update --init --recursive

	@echo "➡️ Upgrading pip..."
	python3 -m pip install --upgrade pip setuptools wheel

	@echo "➡️ Upgrading pip-tools..."
	python3 -m pip install --upgrade pip-tools

	@echo "➡️ Compiling app requirements..."
	pip-compile \
		--output-file requirements.txt \
		pyproject.toml

	@echo "➡️ Compiling dev requirements..."
	pip-compile \
		--extra dev \
		--output-file requirements-dev.txt \
		pyproject.toml

	@echo "➡️ Upgrading Bicep CLI..."
	az bicep upgrade

test:
	@echo "➡️ Test code smells (Ruff)..."
	python3 -m ruff check --select I,PL,RUF,UP,ASYNC,A,DTZ,T20,ARG,PERF --ignore RUF012

	@echo "➡️ Test types (Pyright)..."
	python3 -m pyright .

	@echo "➡️ Unit tests (Pytest)..."
	PUBLIC_DOMAIN=dummy pytest \
		--junit-xml=test-reports/$(version_full).xml \
		tests/*.py

lint:
	@echo "➡️ Fix with formatter..."
	python3 -m ruff format

	@echo "➡️ Lint with linter..."
	python3 -m ruff check --select I,PL,RUF,UP,ASYNC,A,DTZ,T20,ARG,PERF --ignore RUF012 --fix

tunnel:
	@echo "➡️ Creating tunnel..."
	devtunnel show $(tunnel_name) || devtunnel create $(tunnel_name) --allow-anonymous --expiration 1d

	@echo "➡️ Creating port forwarding..."
	devtunnel port show $(tunnel_name) --port-number 8080 || devtunnel port create $(tunnel_name) --port-number 8080

	@echo "➡️ Starting tunnel..."
	devtunnel host $(tunnel_name)

dev:
	VERSION=$(version_full) PUBLIC_DOMAIN=$(tunnel_url) python3 -m gunicorn app.main:api \
		--access-logfile - \
		--bind 0.0.0.0:8080 \
		--proxy-protocol \
		--reload \
		--reload-extra-file .env \
		--reload-extra-file config.yaml \
		--worker-class uvicorn.workers.UvicornWorker \
		--workers 2

build:
	$(docker) build \
		--build-arg VERSION=$(version_full) \
		--tag $(container_name):$(version_small) \
		--tag $(container_name):latest \
		.

run:
	$(docker) run \
		--env PUBLIC_DOMAIN=$(tunnel_url) \
		--env VERSION=$(version_full) \
		--env-file .env \
		--mount type=bind,source=$(shell pwd)/config.yaml,target=/app/config.yaml \
		--publish 8080:8080 \
		$(container_name):latest

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
		curl -s "$(endpoint)/call?phone_number=%2B$(phone_number)" | yq --prettyPrint '.[0] | {"phone_number": .phone_number, "claim": .claim, "reminders": .reminders}'; \
		sleep 3; \
	done

sync-local-config:
	@echo "📥 Copying remote CONFIG_JSON to local config..."
	az containerapp revision list \
		--name $(container_app_name) \
		--output tsv \
		--query "[0].properties.template.containers[0].env[?name=='CONFIG_JSON'].value" \
		--resource-group $(name_sanitized) \
			| yq \
				--output-format yaml \
				--prettyPrint \
					> config.yaml
