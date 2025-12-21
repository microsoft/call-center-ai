# CLAUDE.md - AI Assistant Developer Guide

This document provides comprehensive guidance for AI assistants (like Claude) working on the Call Center AI codebase. It covers architecture, conventions, development workflows, and best practices.

## Table of Contents

- [Project Overview](#project-overview)
- [Repository Structure](#repository-structure)
- [Technology Stack](#technology-stack)
- [Architecture & Design Patterns](#architecture--design-patterns)
- [Development Workflows](#development-workflows)
- [Code Conventions](#code-conventions)
- [Testing Guidelines](#testing-guidelines)
- [Configuration Management](#configuration-management)
- [Common Tasks](#common-tasks)
- [Troubleshooting](#troubleshooting)

---

## Project Overview

**Call Center AI** is an AI-powered call center solution that integrates Azure Communication Services, Azure Cognitive Services, and Azure OpenAI (GPT models) to provide automated phone call handling with sophisticated conversation capabilities.

### Key Capabilities

- **Real-time voice conversations** with streaming audio processing
- **Multi-language support** with Azure Translation
- **RAG (Retrieval-Augmented Generation)** for domain knowledge
- **Claim data collection** with structured schema validation
- **Call recording and transcription**
- **SMS integration** (Azure Communication Services or Twilio)
- **Human agent transfer** capability
- **Conversation resumption** after disconnections
- **Feature flags** for live configuration updates (60s refresh)

### Main Use Cases

Insurance claims, IT support, customer service, appointment scheduling, and any scenario requiring medium-complexity phone automation.

---

## Repository Structure

```
/home/user/call-center-ai/
├── .devcontainer/          # GitHub Codespaces configuration
├── .github/workflows/      # CI/CD pipelines (pipeline.yaml, codeql.yml)
├── .vscode/                # VS Code settings and extensions
├── app/                    # Main application source (~9,484 lines of Python)
│   ├── helpers/            # Utility modules (34 files)
│   │   ├── config_models/  # Pydantic configuration models
│   │   └── pydantic_types/ # Custom Pydantic types (phone numbers, etc.)
│   ├── models/             # Data models (call, message, claim, reminder, etc.)
│   ├── persistence/        # Data access layer with interface pattern
│   │   ├── istore.py       # Storage interface
│   │   ├── icache.py       # Cache interface
│   │   ├── isearch.py      # Search/RAG interface
│   │   ├── isms.py         # SMS interface
│   │   └── implementations (cosmos_db.py, redis.py, ai_search.py, etc.)
│   └── resources/          # Static resources (templates, audio files)
│       └── public_website/ # Jinja2 HTML templates
├── cicd/                   # CI/CD infrastructure
│   ├── bicep/              # Azure infrastructure as code (main.bicep, app.bicep)
│   └── Dockerfile          # Multi-stage container build
├── docs/                   # Documentation assets
├── examples/               # Example scripts and data
├── public/                 # Public static assets (lexicon.xml, loading.wav)
└── tests/                  # Test suite (7 Python test modules)
```

### Critical Files

- **app/main.py** (1,153 lines) - FastAPI application, WebSocket handlers, REST endpoints
- **app/helpers/call_llm.py** - LLM integration for call processing
- **app/helpers/llm_tools.py** - Tool definitions for LLM function calling
- **app/helpers/call_events.py** - Event handlers for call lifecycle
- **app/helpers/call_utils.py** - STT, TTS, media handling utilities
- **pyproject.toml** - Project metadata, dependencies, tool configurations
- **Makefile** - Build automation with 20+ targets
- **config.yaml** - Application configuration (created from examples)

---

## Technology Stack

### Core Runtime

- **Python 3.13+** - Main language
- **FastAPI ~0.115** - Async web framework
- **Granian ~2.3** - High-performance ASGI server (replaced traditional WSGI)
- **uv** - Modern, fast package manager (10-100x faster than pip)

### Azure Services

| Service | Purpose | Package |
|---------|---------|---------|
| Communication Services | Call automation & SMS | azure-communication-callautomation ~1.4.0a0 |
| Cognitive Services Speech | STT & TTS | azure-cognitiveservices-speech ~1.41 |
| AI Translation | Multi-language support | azure-ai-translation-text ~1.0 |
| OpenAI (gpt-4.1, gpt-4.1-nano) | LLM inference | azure-ai-inference ~1.0.0a0 |
| AI Search | RAG knowledge base | azure-search-documents ~11.6.0a0 |
| Cosmos DB | NoSQL database | azure-cosmos ~4.7 |
| Storage Queue | Message queuing | azure-storage-queue ~12.12 |
| App Configuration | Feature flags & live config | azure-appconfiguration ~1.7 |
| Event Grid | Event-driven architecture | azure-eventgrid ~4.20 |
| Monitor | OpenTelemetry tracing | azure-monitor-opentelemetry ~1.6 |

### Key Libraries

- **Pydantic ~2.9** - Data validation, settings management
- **Redis ~5.2** - Caching layer
- **Jinja2 ~3.1** - Template engine (prompts & web views)
- **TikToken ~0.8** - OpenAI tokenization
- **Tenacity ~8.2** - Retry logic
- **Structlog ~24.4** - Structured logging
- **phonenumbers ~8.13** - Phone number validation

### Development Tools

- **Ruff ~0.7** - Fast linter & formatter (replaces Black, isort, flake8)
- **Pyright ~1.1** - Static type checker
- **Pytest ~8.3** - Testing framework
  - pytest-asyncio ~0.24 - Async test support
  - pytest-xdist ~3.6 - Parallel execution
- **DeepEval ~0.21** - LLM evaluation
- **Deptry ~0.20** - Dependency validation

### Infrastructure

- **Docker** - Multi-stage builds (linux/amd64, linux/arm64)
- **Azure Bicep** - Infrastructure as Code
- **GitHub Actions** - CI/CD automation
- **Dev Containers** - Consistent development environment

---

## Architecture & Design Patterns

### Design Principles

1. **Interface-based persistence** - All data access through abstract interfaces (IStore, ICache, ISearch, ISms)
2. **Event-driven architecture** - Azure Event Grid + Queue Storage for decoupled processing
3. **Streaming-first** - Real-time audio/text streaming throughout pipeline
4. **Configuration as code** - Pydantic models validate all configuration
5. **Observability-first** - OpenTelemetry instrumentation on critical paths
6. **Async/await everywhere** - Fully async using asyncio

### Application Entry Point

**File**: `app/main.py`
**ASGI App**: `app.main:api`
**Server Command**: `granian --interface asgi --host 0.0.0.0 --port 8080 --workers 4 app.main:api`

### Application Lifecycle

1. **Startup** (lifespan context manager):
   - Initialize 4 async queue workers:
     - `_call_queue` - Call events processor
     - `_post_queue` - Post-call processing
     - `_sms_queue` - SMS events processor
     - `_training_queue` - Training data processor
   - Connect to Azure services

2. **Runtime**:
   - FastAPI handles HTTP/WebSocket requests
   - Queue workers process async events
   - OpenTelemetry tracks all operations

3. **Shutdown**:
   - Cancel queue tasks
   - Close HTTP sessions

### REST API Endpoints

Located in `app/main.py`:

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/health/liveness` | GET | Service health check |
| `/health/readiness` | GET | Dependency readiness check |
| `/call` | GET/POST | Query or initiate calls |
| `/communicationservices/callback` | POST | Azure Communication Services webhooks |
| `/communicationservices/wss` | WebSocket | Audio streaming |
| `/twilio/sms` | POST | Twilio SMS webhook |
| `/report/{phone_number}` | GET | User-facing call report UI |

### Data Models

Located in `app/models/`:

- **call.py** - `CallInitiateModel`, `CallGetModel` (call state)
- **message.py** - `MessageModel` with `ActionEnum`, `PersonaEnum`, `StyleEnum`
- **claim.py** - Claim data schema (dynamic validation)
- **reminder.py** - `ReminderModel` for follow-ups
- **synthesis.py** - `SynthesisModel` (call summaries)
- **next.py** - `NextModel` (next action determination)
- **training.py** - `TrainingModel` for RAG
- **error.py** - Error response models

### Persistence Layer Pattern

All data access uses interface pattern for testability and flexibility:

**Interfaces** (`app/persistence/`):
- `IStore` - Call state storage
- `ICache` - Caching operations
- `ISearch` - RAG search
- `ISms` - SMS sending

**Implementations**:
- `cosmos_db.py` - Production database
- `redis.py` - Production cache
- `ai_search.py` - Production RAG
- `communication_services.py` / `twilio.py` - SMS providers
- `memory.py` - In-memory implementations for testing

### LLM Tools (Function Calling)

Defined in `app/helpers/llm_tools.py`, exposed to LLM:

- `end_call` - Terminate conversation
- `transfer_call` - Transfer to human agent
- `update_claim` - Update claim fields
- `create_reminder` - Schedule follow-ups
- `send_sms` - Send SMS messages
- `search` - RAG knowledge base search

### Configuration Loading Priority

From `app/helpers/config.py`:

1. Environment variable `CONFIG_JSON` (JSON format) - **Production**
2. File `config.yaml` (YAML format) - **Development**
3. Fallback using dotenv for `.env` file

### Feature Flags

Managed via Azure App Configuration, refreshed every 60s (configurable via `app_configuration.ttl_sec`):

- `answer_hard_timeout_sec` - LLM abort timeout (default: 15)
- `answer_soft_timeout_sec` - Waiting message trigger (default: 4)
- `callback_timeout_hour` - Callback timeout (default: 3)
- `phone_silence_timeout_sec` - Silence warning trigger (default: 20)
- `recording_enabled` - Call recording (default: false)
- `slow_llm_for_chat` - Use gpt-4.1 instead of gpt-4.1-nano (default: false)
- `vad_threshold` - Voice activity detection sensitivity (default: 0.5)

---

## Development Workflows

### Initial Setup

#### Quick Start (GitHub Codespaces - Recommended)

```bash
# Open in Codespaces - environment auto-configures
# Badge: https://codespaces.new/microsoft/call-center-ai?quickstart=1
```

#### Local Setup (macOS)

```bash
# Install dependencies
make brew

# Setup Python environment
make install

# Create config file
cp config-local-example.yaml config.yaml
# Edit config.yaml with your Azure resource details
```

#### Local Setup (Other Platforms)

Install manually:
- Azure CLI
- yq
- Rust
- uv
- Bash-compatible shell
- Make

Then:
```bash
make install
cp config-local-example.yaml config.yaml
```

### Configuration Files

Two configuration templates exist:

1. **config-remote-example.yaml** - Minimal config for Azure deployment
   - Contains only required overrides
   - Used with `make deploy`

2. **config-local-example.yaml** - Full config for local development
   - Contains all Azure resource endpoints
   - Used with `make dev`

### Development Workflow

#### Local Development

```bash
# Terminal 1: Start dev tunnel (required for webhooks)
devtunnel login  # First time only
make tunnel

# Terminal 2: Run development server
make dev
# - Auto-reload on file changes
# - 2 workers
# - Available at http://localhost:8080
# - Public URL from tunnel

# Override specific config values via env vars
# Format: SECTION__SUBSECTION__KEY=value
LLM__FAST__ENDPOINT=https://xxx.openai.azure.com make dev
```

#### Testing Without Phone Calls

```bash
# Use local testing script (simulates calls without Communication Services)
python3 -m tests.local
```

#### Running Tests

```bash
# All tests (static + unit)
make test

# Static analysis only (linting + type checking + Bicep)
make test-static

# Unit tests only
make test-unit

# Auto-fix code style issues
make lint
```

### Azure Deployment

#### Prerequisites

1. Create Azure resource group (lowercase, dashes only, e.g., `ccai-customer-a`)
2. Create Communication Services resource (same name, system-managed identity)
3. Buy phone number (inbound/outbound, voice + SMS)

#### Deploy

```bash
# Create minimal config
cp config-remote-example.yaml config.yaml
# Edit with your resource group name and phone number

# Login to Azure
az login

# Deploy (specify image version to avoid breaking changes)
make deploy name=my-rg-name image_version=16.0.0

# View logs
make logs name=my-rg-name
```

#### Sync Remote Config to Local

```bash
# Copy production config to local for debugging
make sync-local-config name=my-rg-name
```

### CI/CD Pipeline

**File**: `.github/workflows/pipeline.yaml`

**Triggers**:
- Push to: `main`, `develop`, `feat/*`, `hotfix/*`
- Pull requests to same branches

**Jobs**:

1. **init** - Generate semantic versions from Git tags
2. **sast-creds** - Trufflehog credential scanning
3. **sast-semgrep** - Security pattern matching (CWE Top 25, OWASP Top 10)
4. **test** - Static analysis (Ruff, Pyright, Bicep)
5. **build-image** - Multi-platform Docker build (amd64, arm64)
   - Pushed to GitHub Container Registry
   - Tags: `latest`, branch name, semver, sha
   - SBOM generation
   - Build attestations
6. **create-release** - Draft GitHub release (main branch only)
7. **publish-release** - Publish release (main branch only)

### Building Containers

```bash
# Build multi-platform image
make build

# Uses BuildKit with layer caching
# Platforms: linux/amd64, linux/arm64
# Output: ghcr.io/clemlesne/call-center-ai:latest
```

### Versioning

```bash
# Short version (e.g., "1.0.0")
make version

# Full version with metadata (e.g., "1.0.0+sha.abc123")
make version-full
```

Version is derived from Git tags using semantic versioning.

---

## Code Conventions

### Commit Message Format

Follow conventional commits pattern (observed from git history):

```
<type>: <description>

Types:
- feat: New feature
- fix: Bug fix
- perf: Performance improvement
- refactor: Code refactoring
- doc: Documentation
- chore: Maintenance tasks
- quality: Code quality improvements
- ux: User experience improvements
- security: Security fixes
```

Examples from history:
- `perf: Use Granian as app server`
- `chore: Delete dead code`
- `doc: Specify App Config refresh behavior`
- `quality: Force lower case app config values`
- `ux: Lower thresholds for timeout static prompt triggers`
- `security: Upgrade deps`

### Python Code Style

**Formatter & Linter**: Ruff (configured in `pyproject.toml`)

**Selected rule sets**:
- `I` - Import sorting
- `PL` - Pylint
- `RUF` - Ruff-specific
- `UP` - pyupgrade
- `ASYNC` - Async best practices
- `A` - Shadowing builtins
- `DTZ` - Datetime timezone awareness
- `T20` - Print statements
- `ARG` - Unused arguments
- `PERF` - Performance anti-patterns

**Ignored rules**:
- `RUF012` - Mutable class attributes with typing.ClassVar
- `A005` - Shadowing Python builtins in modules

**Key conventions**:
- 4-space indentation (Python files)
- Combine imports (`from x import a, b`)
- Format docstring code examples
- Type hints everywhere (Pyright standard mode)

### File Organization

**Import order** (enforced by Ruff):
```python
# 1. Standard library
import asyncio
from datetime import UTC, datetime

# 2. Third-party
from azure.ai.inference.models import AssistantMessage
from pydantic import BaseModel

# 3. Local
from app.helpers.config import config
from app.models.call import CallModel
```

**Async patterns**:
```python
# Always use async/await, never blocking calls
async def process_call(call_id: str) -> CallModel:
    # Use async context managers
    async with http_session.get(url) as response:
        data = await response.json()

    # Use async comprehensions
    results = [await process(item) async for item in async_iterator]

    return result
```

### Type Hints

**Pyright configuration** (pyproject.toml):
- Python version: 3.13
- Type checking mode: standard
- Virtual environment: `.venv`

**Common patterns**:
```python
from typing import Any
from pydantic import BaseModel

# Use Pydantic models for validation
class CallInitiateModel(BaseModel):
    phone_number: PhoneNumber  # Custom Pydantic type
    bot_name: str
    claim: dict[str, Any] = {}

# Use type hints for all functions
async def get_call(call_id: UUID) -> CallGetModel | None:
    ...

# Use generics where appropriate
def cache_get[T](key: str, model: type[T]) -> T | None:
    ...
```

### Error Handling

**Use tenacity for retries**:
```python
from tenacity import retry, stop_after_attempt, wait_exponential

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10)
)
async def call_external_service() -> Response:
    ...
```

**Structured logging**:
```python
import structlog

logger = structlog.get_logger()

# Use structured fields, not string interpolation
logger.info("call_started", call_id=str(call_id), phone_number=phone_number)
logger.error("llm_error", error=str(e), attempt=retry_count)
```

### Testing Conventions

**Test file location**: `/tests/`

**Test structure**:
```python
import pytest
from app.persistence.memory import MemoryStore

@pytest.mark.asyncio
async def test_store_operations():
    """Test description."""
    # Arrange
    store = MemoryStore()

    # Act
    result = await store.call_create(call)

    # Assert
    assert result.call_id == call.call_id
```

**Fixtures** (tests/conftest.py):
- Mock Azure services (CallAutomationClientMock, SpeechSynthesizerMock)
- DeepEval integration for LLM testing
- Conversation YAML test data

---

## Testing Guidelines

### Test Structure

```
tests/
├── conftest.py           # Pytest fixtures and mocks
├── cache.py              # Cache layer tests
├── llm.py                # LLM integration tests
├── search.py             # AI Search tests
├── store.py              # Database tests
├── local.py              # Local testing script (no phone calls)
└── conversations.yaml    # Test conversation scenarios
```

### Running Tests

```bash
# All tests
make test

# Static analysis (Ruff + Pyright + Bicep)
make test-static

# Unit tests with JUnit XML output
make test-unit

# Parallel execution (via pytest-xdist)
pytest -n auto tests/

# Repeat tests for flakiness detection
pytest --count=10 tests/test_specific.py
```

### Writing Tests

**Async tests**:
```python
@pytest.mark.asyncio
async def test_async_operation():
    result = await async_function()
    assert result is not None
```

**Mock Azure services** (use fixtures from conftest.py):
```python
def test_with_mock_call_automation(call_automation_mock):
    # call_automation_mock provides CallAutomationClientMock
    result = call_automation_mock.create_call(...)
    assert result.call_connection_id
```

**LLM evaluation** (DeepEval integration):
```python
from deepeval import evaluate
from deepeval.metrics import AnswerRelevancyMetric

def test_llm_response_quality():
    metric = AnswerRelevancyMetric(threshold=0.7)
    # Test LLM output quality
```

### Test Coverage Expectations

- **Critical paths**: 100% coverage (call lifecycle, data persistence)
- **LLM tools**: Test all function calling plugins
- **Data validation**: Test all Pydantic models
- **Error handling**: Test retry logic and failure modes

---

## Configuration Management

### Configuration Files

1. **Production**: Environment variable `CONFIG_JSON` (JSON string)
2. **Development**: `config.yaml` (YAML file)
3. **Fallback**: `.env` file (key-value pairs)

### Configuration Structure

**Minimal config** (config-remote-example.yaml):
```yaml
conversation:
  initiate:
    agent_phone_number: "+33612345678"
    bot_company: Contoso
    bot_name: Amélie
    lang: {}

communication_services:
  phone_number: "+33612345678"

sms: {}
prompts:
  llm: {}
  tts: {}
```

**Full config** (config-local-example.yaml):
```yaml
resources:
  public_url: https://xxx.blob.core.windows.net/public

conversation:
  initiate:
    agent_phone_number: "+33612345678"
    bot_company: Contoso
    bot_name: Robert

communication_services:
  access_key: xxx
  endpoint: https://xxx.france.communication.azure.com
  phone_number: "+33612345678"
  # ... more fields

cognitive_service:
  endpoint: https://xxx.cognitiveservices.azure.com
  region: swedencentral

llm:
  fast:  # gpt-4.1-nano for real-time chat
    endpoint: https://xxx.openai.azure.com/openai/deployments/xxx
    model: gpt-4.1-nano
  slow:  # gpt-4.1 for analysis
    endpoint: https://xxx.openai.azure.com/openai/deployments/xxx
    model: gpt-4.1

ai_search:
  endpoint: https://xxx.search.windows.net
  index: trainings
```

### Environment Variable Overrides

Override any config value using double-underscore notation:

```bash
# Format: SECTION__SUBSECTION__KEY=value
LLM__FAST__ENDPOINT=https://override.openai.azure.com

# Example in development
LLM__FAST__MODEL=gpt-4.1 make dev
```

### Customizing Claim Schema

Define custom fields in `conversation.default_initiate.claim`:

```yaml
conversation:
  default_initiate:
    claim:
      - name: device_info
        type: text
        description: "Hardware and software details"
      - name: incident_datetime
        type: datetime
        description: "When the issue first occurred"
      - name: caller_email
        type: email
      - name: caller_phone
        type: phone_number
```

**Supported types**: `text`, `datetime`, `email`, `phone_number` (E164 format)

### Customizing Prompts

Prompts use Jinja2 templating with placeholders:

```yaml
prompts:
  tts:
    hello_tpl:
      - "Hello, I'm {bot_name} from {bot_company}! How can I help?"
      - "Hi, I'm {bot_name}. What's the issue?"  # Multiple variations

  llm:
    default_system_tpl: |
      Assistant is called {bot_name} and works for {bot_company}.
      Today is {date}. Customer is calling from {phone_number}.

    chat_system_tpl: |
      # Objective
      {task}

      # Current claim data
      {claim}

      # Reminders
      {reminders}
```

**Available placeholders**:
- `{bot_name}`, `{bot_company}`, `{bot_phone_number}`
- `{phone_number}` - Caller's number
- `{date}` - Current date
- `{task}` - Call objective
- `{claim}` - Current claim data
- `{reminders}` - Scheduled reminders
- `{default_lang}` - Default language

### Language Configuration

```yaml
conversation:
  initiate:
    lang:
      default_short_code: fr-FR
      availables:
        - pronunciations_en: ["French", "FR", "France"]
          short_code: fr-FR
          voice: fr-FR-DeniseNeural
        - pronunciations_en: ["Chinese", "ZH", "China"]
          short_code: zh-CN
          voice: zh-CN-XiaoqiuNeural
          custom_voice_endpoint_id: xxx  # Optional CNV
```

See [Azure Speech language support](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/language-support?tabs=tts).

---

## Common Tasks

### Adding a New LLM Tool

1. **Define the tool** in `app/helpers/llm_tools.py`:

```python
class MyNewTool(LlmPlugin):
    """Tool description for LLM."""

    _conversation: Conversation

    def __init__(self, conversation: Conversation) -> None:
        self._conversation = conversation

    @staticmethod
    def spec() -> ChatCompletionsToolDefinition:
        return ChatCompletionsToolDefinition(
            function=FunctionDefinition(
                name="my_new_tool",
                description="What this tool does",
                parameters={
                    "type": "object",
                    "properties": {
                        "param1": {
                            "type": "string",
                            "description": "Parameter description",
                        },
                    },
                    "required": ["param1"],
                },
            )
        )

    async def execute(self, param1: str) -> str:
        """Execute the tool."""
        # Implementation
        return "Result"
```

2. **Register the tool** in `app/helpers/llm_tools.py` in the tools list:

```python
tools = [
    EndCallTool,
    TransferCallTool,
    UpdateClaimTool,
    CreateReminderTool,
    SendSmsTool,
    SearchTool,
    MyNewTool,  # Add here
]
```

3. **Add tests** in `tests/llm.py`

### Adding a New API Endpoint

1. **Add route** in `app/main.py`:

```python
@api.get("/my-endpoint")
async def my_endpoint(param: str) -> dict[str, Any]:
    """Endpoint description."""
    logger.info("my_endpoint_called", param=param)

    # Implementation
    return {"result": "value"}
```

2. **Add response model** in `app/models/` if needed:

```python
class MyResponseModel(BaseModel):
    result: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
```

3. **Update endpoint** with model:

```python
@api.get("/my-endpoint", response_model=MyResponseModel)
async def my_endpoint(param: str) -> MyResponseModel:
    return MyResponseModel(result="value")
```

### Adding a New Persistence Implementation

1. **Create implementation** in `app/persistence/`:

```python
# app/persistence/my_store.py
from app.persistence.istore import IStore

class MyStore(IStore):
    """Implementation description."""

    async def call_create(self, call: CallGetModel) -> CallGetModel:
        # Implementation
        pass

    async def call_get(self, phone_number: str) -> list[CallGetModel]:
        # Implementation
        pass

    # Implement all interface methods...
```

2. **Update factory** in `app/helpers/` to instantiate your implementation based on config

3. **Add tests** in `tests/store.py`

### Updating Dependencies

```bash
# Update lock file
make upgrade

# Sync virtual environment
make install-deps

# Test everything still works
make test
```

### Adding a Feature Flag

1. **Add to Azure App Configuration** via Portal or CLI

2. **Access in code**:

```python
from app.helpers.features import FeatureManager

# In async context
feature_manager = FeatureManager(config)
await feature_manager.refresh()

# Get feature value
my_feature_enabled = feature_manager.get_bool("my_feature_enabled", default=False)
timeout_value = feature_manager.get_int("my_timeout_sec", default=30)
```

Note: Features refresh every 60s (configurable via `app_configuration.ttl_sec`)

### Monitoring Performance

**Application Insights** automatically collects:
- HTTP requests
- Dependencies (Redis, Cosmos DB, Azure services)
- Exceptions
- Custom metrics
- LLM traces (OpenLLMetry)

**Custom metrics**:
```python
from app.helpers.monitoring import tracer

# Add custom span
with tracer.start_as_current_span("my_operation") as span:
    span.set_attribute("custom_attribute", value)
    # Operation
```

**View metrics**:
- Azure Portal > Application Insights > Metrics
- Custom metrics: `call.aec.dropped`, `call.aec.missed`, `call.answer.latency`

### Debugging Locally

```bash
# 1. Start tunnel
make tunnel

# 2. Run with debugger
# VS Code: F5 (launch.json should be configured)
# Or manual:
PUBLIC_DOMAIN=$(make tunnel_url) uv run python -m debugpy --listen 5678 -m granian ...

# 3. Make test call
curl -X POST http://localhost:8080/call \
  -H "Content-Type: application/json" \
  -d '{
    "phone_number": "+1234567890",
    "bot_name": "Test",
    "bot_company": "Contoso",
    "task": "Test call"
  }'

# 4. Watch call status
make watch-call phone_number=1234567890 endpoint=http://localhost:8080
```

---

## Troubleshooting

### Common Issues

#### Issue: "CONFIG_JSON not found"

**Solution**: Create `config.yaml`:
```bash
cp config-local-example.yaml config.yaml
# Edit with your Azure resource details
```

#### Issue: "Module not found"

**Solution**: Ensure virtual environment is activated and dependencies installed:
```bash
make install-deps
source .venv/bin/activate  # Or use 'uv run' prefix
```

#### Issue: "Tunnel not working"

**Solution**:
```bash
# Login to dev tunnels
devtunnel login

# Delete old tunnel
devtunnel delete call-center-ai-$(hostname | sed 's/[^a-zA-Z0-9]//g' | tr '[:upper:]' '[:lower:]')

# Recreate
make tunnel
```

#### Issue: "Tests failing with Azure authentication error"

**Solution**:
```bash
# Login to Azure
az login

# Set subscription
az account set --subscription "Your Subscription Name"

# For service principal (CI/CD):
export AZURE_CLIENT_ID=xxx
export AZURE_CLIENT_SECRET=xxx
export AZURE_TENANT_ID=xxx
```

#### Issue: "LLM responses are slow"

**Causes & Solutions**:
1. **No PTU (Provisioned Throughput)**: Consider upgrading to Azure OpenAI PTU for 2x faster latency
2. **Wrong model**: Check `slow_llm_for_chat` feature flag is `false` (should use gpt-4.1-nano)
3. **Network latency**: Use Application Insights to identify bottlenecks
4. **Timeout settings**: Adjust `answer_soft_timeout_sec` and `answer_hard_timeout_sec`

#### Issue: "Call recording not working"

**Solution**:
1. Ensure storage container exists (e.g., `recordings`)
2. Enable feature flag: `recording_enabled = true` in App Configuration
3. Check `recording_container_url` in config points to correct container

#### Issue: "Voice activity detection too sensitive"

**Solution**: Adjust VAD settings via feature flags:
- `vad_threshold` - Increase to 0.7 for less sensitivity (default: 0.5)
- `vad_silence_timeout_ms` - Increase to 700ms (default: 500)
- `vad_cutoff_timeout_ms` - Increase to 400ms (default: 250)

#### Issue: "Echo during calls"

**Cause**: Echo cancellation (AEC) issues

**Solution**: Monitor custom metrics:
- `call.aec.dropped` - AEC dropping voice completely
- `call.aec.missed` - AEC failing to remove echo

Adjust echo cancellation settings in call configuration.

#### Issue: "Deployment fails with Bicep error"

**Solution**:
```bash
# Validate Bicep template
az bicep lint --file cicd/bicep/main.bicep

# Check Azure subscription limits
az vm list-usage --location swedencentral -o table

# Try different region
make deploy name=my-rg-name default_location=northeurope
```

#### Issue: "SMS not sending"

**Solutions**:

For Azure Communication Services:
1. Verify phone number has SMS capability
2. Check `sms_queue_name` in config
3. Verify Event Grid subscription is active

For Twilio:
1. Register webhook: `make twilio-register endpoint=https://your-domain.com`
2. Verify `twilio_phone_number` in config
3. Check Twilio account has credits

### Debugging Tips

**Enable verbose logging**:
```python
# In app/helpers/logging.py, adjust log level
structlog.configure(
    wrapper_class=structlog.make_filtering_bound_logger(logging.DEBUG)
)
```

**Check queue processing**:
```python
# Verify queue workers are running
# In app/main.py, add debug logging in queue workers
logger.debug("queue_worker_processing", queue="_call_queue", message=message)
```

**Inspect call state**:
```bash
# Get call details
curl "http://localhost:8080/call?phone_number=%2B1234567890" | jq

# Watch in real-time
make watch-call phone_number=1234567890 endpoint=http://localhost:8080
```

**Check Application Insights**:
```bash
# Get logs from Azure
make logs name=my-rg-name

# Query Application Insights
az monitor app-insights query \
  --app your-app-insights \
  --analytics-query "traces | where message contains 'error' | take 50"
```

### Performance Optimization

**Reduce latency**:
1. Use Azure OpenAI PTU (Provisioned Throughput)
2. Use `gpt-4.1-nano` for chat (set `slow_llm_for_chat=false`)
3. Deploy Azure resources in same region
4. Enable Redis caching
5. Tune timeout settings:
   - `answer_soft_timeout_sec=3` (faster waiting message)
   - `answer_hard_timeout_sec=12` (faster abort)

**Reduce costs**:
1. Use serverless Container Apps (default)
2. Use `gpt-4.1-nano` instead of `gpt-4.1` for chat
3. Disable recording if not needed: `recording_enabled=false`
4. Reduce conversation history token count in prompts
5. Use basic Azure AI Search tier for small datasets
6. Enable Application Insights sampling

### Getting Help

1. **Check README.md** - Comprehensive deployment and configuration guide
2. **Check GitHub Issues** - https://github.com/clemlesne/call-center-ai/issues
3. **Application Insights** - Query traces for detailed debugging
4. **Azure Support** - For Azure-specific service issues

---

## Best Practices for AI Assistants

### When Making Changes

1. **Always read files before editing** - Never propose changes to code you haven't read
2. **Understand context** - Review related files to understand dependencies
3. **Follow existing patterns** - Match the style and architecture already in use
4. **Test your changes** - Run `make test` before committing
5. **Update documentation** - Update this file if you change architecture

### Code Quality

1. **Type hints** - Add type hints to all functions and variables
2. **Async/await** - Use async patterns consistently, never blocking calls
3. **Error handling** - Use structured logging and tenacity for retries
4. **Validation** - Use Pydantic models for all data validation
5. **Security** - Never commit secrets, validate user input, use parameterized queries

### Testing

1. **Write tests first** - TDD approach for new features
2. **Test edge cases** - Null values, empty lists, invalid input
3. **Mock external services** - Use fixtures from conftest.py
4. **Async tests** - Use `@pytest.mark.asyncio` decorator
5. **Run tests locally** - `make test` before pushing

### Documentation

1. **Docstrings** - Add to all public functions and classes
2. **Type hints** - Better than comments for documenting types
3. **Comments** - Explain "why", not "what" (code shows "what")
4. **Update CLAUDE.md** - When adding new patterns or conventions
5. **Commit messages** - Follow conventional commits format

### Performance

1. **Async operations** - Never block the event loop
2. **Caching** - Use Redis for expensive operations
3. **Batch operations** - Group database calls where possible
4. **Monitor metrics** - Use Application Insights to identify bottlenecks
5. **Streaming** - Stream LLM responses, don't wait for completion

### Security

1. **No secrets in code** - Use Azure Key Vault or environment variables
2. **Input validation** - Validate all user input with Pydantic
3. **SQL injection** - Use parameterized queries (Cosmos DB SDK handles this)
4. **CORS** - Restrict origins in production
5. **Content filtering** - Use Azure OpenAI content filters

---

## Appendix: Key File Reference

### Configuration Files

- `pyproject.toml` - Dependencies, tool configuration
- `Makefile` - Build automation, deployment scripts
- `config.yaml` - Application configuration (create from examples)
- `.env` - Environment variables (optional, for local dev)
- `uv.lock` - Locked dependency versions

### Application Code

- `app/main.py` - FastAPI application entry point
- `app/helpers/call_llm.py` - LLM integration
- `app/helpers/llm_tools.py` - LLM function calling tools
- `app/helpers/call_events.py` - Call lifecycle events
- `app/helpers/call_utils.py` - STT, TTS, media utilities
- `app/helpers/config.py` - Configuration loading
- `app/helpers/features.py` - Feature flag management
- `app/helpers/monitoring.py` - OpenTelemetry tracing

### Data Models

- `app/models/call.py` - Call state models
- `app/models/message.py` - Message models with actions/personas
- `app/models/claim.py` - Claim schema
- `app/models/reminder.py` - Reminder models

### Persistence

- `app/persistence/istore.py` - Storage interface
- `app/persistence/cosmos_db.py` - Cosmos DB implementation
- `app/persistence/redis.py` - Redis cache implementation
- `app/persistence/ai_search.py` - AI Search implementation

### Infrastructure

- `cicd/Dockerfile` - Multi-stage container build
- `cicd/bicep/main.bicep` - Azure infrastructure definition
- `cicd/bicep/app.bicep` - Application resources

### CI/CD

- `.github/workflows/pipeline.yaml` - Main CI/CD pipeline
- `.github/workflows/codeql.yml` - Security scanning

### Testing

- `tests/conftest.py` - Test fixtures and mocks
- `tests/store.py` - Database tests
- `tests/llm.py` - LLM tests
- `tests/local.py` - Local testing script

---

## Quick Reference Commands

```bash
# Development
make install          # Setup Python environment
make dev              # Run development server with hot reload
make tunnel           # Create dev tunnel for webhooks
make test             # Run all tests
make lint             # Auto-fix code style

# Deployment
make deploy name=rg-name image_version=16.0.0  # Deploy to Azure
make logs name=rg-name                         # View logs
make sync-local-config name=rg-name            # Sync config from Azure

# Testing
make test-static      # Linting + type checking
make test-unit        # Pytest execution
python3 -m tests.local  # Test without phone calls

# Utilities
make version          # Get version (e.g., "1.0.0")
make version-full     # Get full version with metadata
make watch-call phone_number=XXX endpoint=URL  # Watch call status

# Build
make build            # Build Docker container
make upgrade          # Update dependencies
```

---

**Last Updated**: 2025-12-20
**Version**: Based on repository state at commit `958b319`
