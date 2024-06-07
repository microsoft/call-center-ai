# Versioning
version_full ?= $(shell $(MAKE) --silent version-full)
version_small ?= $(shell $(MAKE) --silent version)
# DevTunnel configuration
tunnel_name := call-center-ai-$(shell hostname | sed 's/[^a-zA-Z0-9]//g' | tr '[:upper:]' '[:lower:]')
tunnel_url ?= $(shell res=$$(devtunnel show $(tunnel_name) | grep -o 'http[s]*://[^"]*' | xargs) && echo $${res%/})
# App location
cognitive_communication_location := westeurope
default_location := westeurope
functionapp_location := swedencentral
openai_location := southcentralus
search_location := northeurope
# App configuration
bot_phone_number ?= $(shell cat config.yaml | yq '.communication_services.phone_number')
event_subscription_name ?= $(shell echo '$(name)-$(bot_phone_number)' | tr -dc '[:alnum:]-')
twilio_phone_number ?= $(shell cat config.yaml | yq '.sms.twilio.phone_number')
# Bicep outputs
app_url ?= $(shell az deployment sub show --name $(name) | yq '.properties.outputs["appUrl"].value')
blob_storage_public_name ?= $(shell az deployment sub show --name $(name) | yq '.properties.outputs["blobStoragePublicName"].value')
communication_id ?= $(shell az deployment sub show --name $(name) | yq '.properties.outputs["communicationId"].value')
function_app_name ?= $(shell az deployment sub show --name $(name) | yq '.properties.outputs["functionAppName"].value')
log_analytics_workspace_customer_id ?= $(shell az deployment sub show --name $(name) | yq '.properties.outputs["logAnalyticsWorkspaceName"].value')

version:
	@bash ./cicd/version/version.sh -g . -c

version-full:
	@bash ./cicd/version/version.sh -g . -c -m

install:
	@echo "➡️ Installing Allure..."
	allure --version || brew install allure

	@echo "➡️ Installing Twilio CLI..."
	twilio --version || brew tap twilio/brew && brew install twilio

	@for f in $$(find . -name "requirements*.txt"); do \
		echo "➡️ Installing Python dependencies in $$f..."; \
		python3 -m pip install -r $$f; \
	done

upgrade:
	@echo "➡️ Upgrading pip..."
	python3 -m pip install --upgrade pip

	@for f in $$(find . -name "requirements*.txt"); do \
		echo "➡️ Upgrading Python dependencies in $$f..."; \
		python3 -m pip install --upgrade -r $$f; \
	done

	@echo "➡️ Upgrading Bicep CLI..."
	az bicep upgrade

test:
	@echo "➡️ Running Black..."
	python3 -m black --check .

	@echo "➡️ Running deptry..."
	python3 -m deptry \
		--ignore-notebooks \
		--per-rule-ignores "DEP002=aiohttp" \
		--per-rule-ignores "DEP003=aiohttp_retry" \
		.

	@echo "➡️ Running Pytest..."
	PUBLIC_DOMAIN=dummy pytest \
		--alluredir test-reports \
		-ra \
		*/tests/*.py

test-serve:
	allure serve test-reports

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
	VERSION=$(version_full) PUBLIC_DOMAIN=$(tunnel_url) func start

deploy:
	@echo "👀 Current subscription:"
	@az account show --query "{subscriptionId:id, subscriptionName:name, tenantId:tenantId}" --output table

	@echo "🛠️ Deploying resources..."
	az deployment sub create \
		--location $(default_location) \
		--parameters \
			'cognitiveCommunicationLocation=$(cognitive_communication_location)' \
			'functionappLocation=$(functionapp_location)' \
			'openaiLocation=$(openai_location)' \
			'searchLocation=$(search_location)' \
			'version=$(version_full)' \
		--template-file bicep/main.bicep \
	 	--name $(name)

	@echo "🛠️ Deploying Function App..."
	func azure functionapp publish $(function_app_name)

	@$(MAKE) post-deploy name=$(name)

post-deploy:
	@$(MAKE) copy-resources \
		name=$(blob_storage_public_name)

	@$(MAKE) twilio-register \
		endpoint=$(app_url)

	@echo "🚀 Call Center AI is running on $(app_url)"
	@$(MAKE) logs name=$(name)

destroy:
	@echo "🧐 Are you sure you want to delete? Type 'delete now $(name)' to confirm."
	@read -r confirm && [ "$$confirm" = "delete now $(name)" ] || (echo "Confirmation failed. Aborting."; exit 1)

	@echo "❗️ Deleting RG..."
	az group delete --name $(name) --yes --no-wait

	@echo "❗️ Deleting deployment..."
	az deployment sub delete --name $(name)

logs:
	func azure functionapp logstream $(function_app_name) \
		--browser

twilio-register:
	@echo "⚙️ Registering Twilio webhook..."
	twilio phone-numbers:update $(twilio_phone_number) \
		--sms-url $(endpoint)/twilio/sms

copy-resources:
	@echo "📦 Copying resources to Azure storage account..."
	az storage blob upload-batch \
		--account-name $(name) \
		--destination '$$web' \
		--no-progress \
		--output none \
		--overwrite \
		--source resources

watch-call:
	@echo "👀 Watching status of $(phone_number)..."
	while true; do \
		clear; \
		curl -s "$(endpoint)/call?phone_number=%2B$(phone_number)" | yq --prettyPrint '.[0] | {"phone_number": .phone_number, "claim": .claim, "reminders": .reminders}'; \
		sleep 3; \
	done
