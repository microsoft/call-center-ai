# Jay's Frames - Deployment Guide

Complete guide to deploy the AI call center for Jay's Frames custom art framing business.

## Table of Contents

1. [Overview](#overview)
2. [Prerequisites](#prerequisites)
3. [Azure Resource Setup](#azure-resource-setup)
4. [Configuration](#configuration)
5. [Local Development](#local-development)
6. [Production Deployment](#production-deployment)
7. [Testing](#testing)
8. [Monitoring & Operations](#monitoring--operations)
9. [Troubleshooting](#troubleshooting)

---

## Overview

This AI-powered call center system for Jay's Frames provides:

- **24/7 Automated Phone Support** - Customers can call anytime to discuss framing projects
- **Intelligent Information Gathering** - AI collects artwork details, preferences, budget, timeline
- **Multi-language Support** - English and Spanish (configurable)
- **SMS Follow-ups** - Automatic text message summaries after calls
- **Quote Preparation** - Structured data collection for creating detailed quotes
- **Human Handoff** - Seamless transfer to live agents when needed

**Technical Stack:**
- Azure Communication Services (Phone, SMS, Speech)
- Azure OpenAI (GPT-4.1 for conversations)
- Azure Cosmos DB (Call storage)
- Azure AI Search (Knowledge base)
- FastAPI + Granian (Application server)
- Azure Container Apps (Hosting)

---

## Prerequisites

### Required Accounts & Access

1. **Azure Subscription** with ability to create resources
   - Cost estimate: $100-500/month depending on call volume
   - Free trial available: https://azure.microsoft.com/free/

2. **Required Tools:**
   ```bash
   # Install Azure CLI
   curl -sL https://aka.ms/InstallAzureCLIDeb | sudo bash

   # Install Python 3.12+
   sudo apt-get install python3.12 python3.12-venv

   # Install Make
   sudo apt-get install build-essential

   # Install Docker (for container builds)
   curl -fsSL https://get.docker.com -o get-docker.sh
   sudo sh get-docker.sh
   ```

3. **GitHub Account** (for code repository)

4. **Domain Name** (optional but recommended)
   - For production, you'll want a custom domain
   - Azure provides a domain, but custom is more professional

---

## Azure Resource Setup

### Step 1: Create Azure Resources

The project includes Infrastructure as Code (Bicep templates) to automate resource creation.

```bash
# Login to Azure
az login

# Set your subscription
az account set --subscription "YOUR_SUBSCRIPTION_NAME"

# Create resource group
az group create \
  --name jays-frames-rg \
  --location eastus

# Deploy all resources (takes 15-30 minutes)
az deployment group create \
  --resource-group jays-frames-rg \
  --template-file cicd/bicep/main.bicep \
  --parameters \
    name=jaysframes \
    openAiApiKey=YOUR_OPENAI_KEY \
    openAiApiUrl=YOUR_OPENAI_URL
```

**Resources Created:**
- Azure Communication Services (phone + SMS)
- Azure Cognitive Services (speech, translation)
- Azure OpenAI Service (GPT-4.1 models)
- Azure Cosmos DB (NoSQL database)
- Azure AI Search (vector search)
- Azure Container Apps (application hosting)
- Azure Storage (recordings, queues)
- Application Insights (monitoring)
- Redis Cache (session storage)

### Step 2: Purchase Phone Number

```bash
# List available phone numbers
az communication phonenumber list-phonenumbers \
  --connection-string "YOUR_COMM_SERVICES_CONNECTION_STRING"

# Search for available numbers (US, toll-free example)
az communication phonenumber search \
  --phonenumber-type "tollFree" \
  --assignment-type "application" \
  --capabilities "calling,sms" \
  --area-code "800"

# Purchase the number
az communication phonenumber purchase \
  --search-id "YOUR_SEARCH_ID"
```

**Note:** Phone numbers cost ~$1-2/month + usage fees.

### Step 3: Configure OpenAI Models

Ensure these models are deployed in your Azure OpenAI resource:

| Model Name | Deployment Name | Purpose |
|------------|----------------|---------|
| gpt-4.1-nano | gpt-4.1-nano-2025-04-14 | Fast conversations |
| gpt-4.1 | gpt-4.1-2025-04-14 | Fallback for complex queries |
| text-embedding-3-large | text-embedding-3-large-1 | Document search |

```bash
# Create model deployments via Azure Portal or CLI
az cognitiveservices account deployment create \
  --name YOUR_OPENAI_RESOURCE \
  --resource-group jays-frames-rg \
  --deployment-name gpt-4.1-nano-2025-04-14 \
  --model-name gpt-4.1-nano \
  --model-version "2025-04-14" \
  --model-format OpenAI \
  --sku-capacity 100 \
  --sku-name "Standard"
```

---

## Configuration

### Step 4: Update Configuration File

Copy the Jay's Frames template and fill in your Azure resource details:

```bash
cp config-jays-frames.yaml config.yaml
```

**Edit `config.yaml` and update these critical values:**

```yaml
conversation:
  initiate:
    # YOUR BUSINESS PHONE (for human transfers)
    agent_phone_number: "+15551234567"  # ← UPDATE THIS

    bot_company: "Jay's Frames"
    bot_name: "Jordan"

communication_services:
  # From Azure Communication Services resource
  endpoint: https://YOUR-RESOURCE.communication.azure.com  # ← UPDATE
  phone_number: "+18005551234"  # ← Your purchased number
  access_key: "YOUR_ACCESS_KEY"  # ← From Azure portal
  resource_id: "/subscriptions/.../YOUR_RESOURCE"  # ← From Azure

cognitive_service:
  # From Azure Cognitive Services multi-service account
  endpoint: https://YOUR-REGION.cognitiveservices.azure.com  # ← UPDATE
  region: "eastus"  # ← Your region
  resource_id: "/subscriptions/.../YOUR_RESOURCE"

llm:
  fast:
    endpoint: https://YOUR-OPENAI.openai.azure.com/openai/deployments/gpt-4.1-nano-2025-04-14  # ← UPDATE
  slow:
    endpoint: https://YOUR-OPENAI.openai.azure.com/openai/deployments/gpt-4.1-2025-04-14  # ← UPDATE

ai_search:
  endpoint: https://YOUR-SEARCH.search.windows.net  # ← UPDATE
  embedding_endpoint: https://YOUR-OPENAI.openai.azure.com  # ← UPDATE
```

**Get these values from Azure Portal:**
1. Communication Services: Settings → Keys
2. Cognitive Services: Keys and Endpoint
3. OpenAI: Keys and Endpoint → Deployments
4. AI Search: Settings → Keys

### Step 5: Set Environment Variables (Production)

For production, store sensitive values as environment variables or Azure Key Vault secrets:

```bash
# Set as environment variables (for Container Apps)
export COMMUNICATION_SERVICES__ACCESS_KEY="your-key"
export COGNITIVE_SERVICE__RESOURCE_ID="your-resource-id"
export LLM__FAST__ENDPOINT="your-endpoint"
# ... etc
```

Or use Docker secrets:
```bash
echo "your-key" | docker secret create comm_services_key -
```

---

## Local Development

### Step 6: Run Locally for Testing

```bash
# Install dependencies
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Run the development server
make dev

# Server starts on http://localhost:8080
```

**Test the endpoints:**
```bash
# Health check
curl http://localhost:8080/health/liveness

# Initiate a test call
curl -X POST http://localhost:8080/call \
  -H "Content-Type: application/json" \
  -d '{
    "phone_number": "+15551234567",
    "bot_company": "Jays Frames",
    "bot_name": "Jordan"
  }'
```

### Step 7: Local Testing with Azure Services

To receive webhook callbacks from Azure Communication Services while running locally:

```bash
# Install Dev Tunnels (for exposing local server to internet)
# https://learn.microsoft.com/en-us/azure/developer/dev-tunnels/get-started

# Create a tunnel
devtunnel create --allow-anonymous

# Run the tunnel
devtunnel port create -p 8080

# Note the public URL (e.g., https://abc123.devtunnels.ms)
# Update config.yaml:
#   public_domain: https://abc123.devtunnels.ms
```

---

## Production Deployment

### Step 8: Build and Push Container Image

```bash
# Build the Docker image
docker build -t jaysframes.azurecr.io/call-center-ai:latest .

# Login to Azure Container Registry
az acr login --name jaysframes

# Push image
docker push jaysframes.azurecr.io/call-center-ai:latest
```

### Step 9: Deploy to Azure Container Apps

```bash
# Deploy using the provided Bicep templates
make deploy

# Or manually update the container app
az containerapp update \
  --name jays-frames-app \
  --resource-group jays-frames-rg \
  --image jaysframes.azurecr.io/call-center-ai:latest \
  --set-env-vars \
    CONFIG_JSON="$(cat config.yaml | yq -o=json)"
```

### Step 10: Configure Custom Domain (Optional)

```bash
# Add custom domain to Container App
az containerapp hostname add \
  --name jays-frames-app \
  --resource-group jays-frames-rg \
  --hostname calls.jaysframes.com

# Create DNS record
# Type: CNAME
# Name: calls
# Value: jays-frames-app.YOUR-REGION.azurecontainerapps.io
```

### Step 11: Enable SSL/TLS

Azure Container Apps automatically provides SSL certificates. For custom domains:

```bash
# Bind SSL certificate
az containerapp hostname bind \
  --name jays-frames-app \
  --resource-group jays-frames-rg \
  --hostname calls.jaysframes.com \
  --environment jays-frames-env \
  --validation-method CNAME
```

---

## Testing

### Step 12: Run Test Conversations

```bash
# Run the framing-specific test scenarios
python -m pytest tests/ -k "jays_frames"

# Or run all tests
make test
```

### Step 13: Make a Live Test Call

1. **Call the bot directly:**
   - Dial your Azure Communication Services phone number
   - The bot should answer and greet you as "Jordan from Jay's Frames"

2. **Test the conversation flow:**
   - Say: "I have a painting I need framed"
   - Provide details when asked (size, style, etc.)
   - Verify the bot collects all required information

3. **Check the database:**
   ```bash
   # View call records in Cosmos DB via Azure Portal
   # Or use the API:
   curl https://YOUR-DOMAIN/call/PHONE_NUMBER
   ```

4. **Test SMS follow-up:**
   - Complete a call
   - Verify you receive an SMS summary

---

## Monitoring & Operations

### Application Insights

All telemetry is automatically sent to Azure Application Insights.

**Key Metrics to Monitor:**

1. **Call Volume:**
   - Query: `traces | where message contains "Call initiated"`
   - Alert: Set threshold for expected daily call volume

2. **LLM Response Time:**
   - Metric: `call.answer.latency`
   - Target: < 4 seconds (soft timeout)
   - Alert: Average > 6 seconds

3. **Error Rate:**
   - Query: `exceptions | summarize count() by type`
   - Alert: Spike in exceptions

4. **Cost Monitoring:**
   - OpenAI usage (token consumption)
   - Communication Services (call minutes)
   - Speech Services (hours of transcription)

**Create Alert Rules:**

```bash
az monitor metrics alert create \
  --name "High Error Rate" \
  --resource-group jays-frames-rg \
  --scopes /subscriptions/.../YOUR_APP_INSIGHTS \
  --condition "avg exceptions/count > 10" \
  --window-size 5m \
  --evaluation-frequency 1m
```

### Custom Dashboards

Create dashboards in Azure Portal:

1. Navigate to Application Insights
2. Click "Workbooks" → "New"
3. Add queries for:
   - Call volume by hour
   - Average call duration
   - Top error types
   - Customer sentiment (from call synthesis)
   - Quote request conversion rate

### Log Queries

**View recent calls:**
```kusto
traces
| where timestamp > ago(24h)
| where message contains "Call"
| project timestamp, message, customDimensions
| order by timestamp desc
```

**Analyze call durations:**
```kusto
customMetrics
| where name == "call.duration"
| summarize avg(value), max(value), min(value) by bin(timestamp, 1h)
| render timechart
```

---

## Operational Best Practices

### 1. Knowledge Base Management

Add framing-specific documentation to the AI Search index:

```bash
# Create training documents (examples):
# - Common frame styles and when to recommend them
# - Matting color guide
# - Glass type comparison (UV vs regular vs museum-quality)
# - Pricing guidelines
# - Rush order policies
# - Conservation framing best practices

# Upload to AI Search
python scripts/upload_training.py \
  --file docs/framing-guide.md \
  --index trainings
```

### 2. Regular Updates

- **Weekly:** Review call transcripts for quality
- **Monthly:** Update prompts based on common customer questions
- **Quarterly:** Review and update pricing ranges in prompts

### 3. Backup Strategy

```bash
# Backup Cosmos DB
az cosmosdb sql container export \
  --account-name YOUR_COSMOSDB \
  --database-name call-center \
  --name calls-v3 \
  --output-path backups/

# Schedule automatic backups (built-in)
# Cosmos DB provides automatic backups every 4 hours
# Retention: 30 days (configurable up to 90 days)
```

### 4. Security Checklist

- [ ] Rate limiting enabled (✓ already configured)
- [ ] Security headers configured (✓ already configured)
- [ ] TLS 1.2+ enforced
- [ ] Secrets stored in Key Vault (not in code)
- [ ] Role-based access control (RBAC) configured
- [ ] Network security groups configured
- [ ] Regular security scans (Azure Defender)
- [ ] PII data handling reviewed
- [ ] GDPR compliance reviewed (if applicable)

---

## Troubleshooting

### Common Issues

**Issue: Bot doesn't answer calls**
- Check: Communication Services phone number is correctly configured
- Check: Webhook URL is accessible from Azure
- Check: Callback secret matches in config
- Logs: Application Insights → Exceptions

**Issue: Poor audio quality**
- Check: Cognitive Services region matches app region (reduce latency)
- Check: TTS voice model is appropriate for language
- Adjust: Speech speed in config

**Issue: Bot gives incorrect information**
- Review: System prompts for clarity
- Check: Training documents in AI Search
- Adjust: Temperature settings in LLM config
- Review: Recent call transcripts for patterns

**Issue: High costs**
- Monitor: OpenAI token usage
- Optimize: Use fast model (gpt-4.1-nano) by default
- Limit: Conversation length (already set to 20 messages max)
- Review: Unnecessary tool calls

**Issue: SMS not sending**
- Check: Communication Services SMS capability enabled
- Check: SMS queue is processing
- Verify: Phone number has SMS capability
- Logs: Search for "SMS" in Application Insights

### Debug Mode

Enable detailed logging:

```bash
# Set log level to DEBUG
export LOG_LEVEL=DEBUG

# Or in config.yaml:
logging:
  level: DEBUG
```

View detailed traces in Application Insights:
```kusto
traces
| where severityLevel >= 1  // Debug level
| order by timestamp desc
```

### Support Resources

- **Azure Communication Services Docs:** https://learn.microsoft.com/en-us/azure/communication-services/
- **Azure OpenAI Docs:** https://learn.microsoft.com/en-us/azure/ai-services/openai/
- **Project GitHub:** https://github.com/microsoft/call-center-ai
- **FastAPI Docs:** https://fastapi.tiangolo.com/

---

## Cost Estimation

**Expected Monthly Costs (Moderate Usage - 100 calls/month):**

| Service | Usage | Cost |
|---------|-------|------|
| Communication Services | 100 calls × 5 min avg | ~$15 |
| Azure OpenAI (GPT-4.1-nano) | ~500K tokens | ~$5 |
| Speech Services (STT/TTS) | 10 hours | ~$10 |
| Cosmos DB | 400 RU/s provisioned | ~$25 |
| Container Apps | 1 instance, always on | ~$45 |
| AI Search | Basic tier | ~$75 |
| Storage & Other | Minimal | ~$10 |
| **Total** | | **~$185/month** |

**High Volume (1000 calls/month):** ~$650/month

**Ways to Reduce Costs:**
- Use consumption-based pricing for Container Apps
- Scale down Cosmos DB during off-hours
- Use serverless tier for AI Search
- Implement aggressive caching

---

## Next Steps

1. **Test thoroughly** with the provided test scenarios
2. **Train your team** on how to review call transcripts
3. **Build your knowledge base** with framing documentation
4. **Set up monitoring alerts** for critical metrics
5. **Start with soft launch** - advertise to small customer group
6. **Collect feedback** and iterate on prompts
7. **Scale up** as usage grows

---

## Customization Options

### Add New Claim Fields

Edit `config-jays-frames.yaml`:

```yaml
claim:
  - name: frame_width_preference
    type: text
    description: "Preferred frame width (thin, medium, wide)"
```

### Modify AI Personality

Edit the `prompts.llm.default_system_tpl` section to change:
- Tone (more formal, more casual)
- Expertise level
- Response style

### Add Business Logic

Create custom LLM tools in `app/helpers/llm_tools.py`:

```python
async def calculate_estimate(
    self,
    artwork_dimensions: str,
    frame_style: str,
) -> str:
    """Calculate rough price estimate."""
    # Add your pricing logic
    return f"Estimated price: ${price}"
```

---

## Compliance & Legal

**Important Considerations:**

1. **Call Recording Consent:**
   - Many jurisdictions require consent for call recording
   - Ensure your greeting mentions recording
   - Configure `recording_enabled` appropriately

2. **Data Privacy:**
   - Customer data stored in Cosmos DB
   - Implement data retention policies
   - Provide data deletion capabilities (GDPR/CCPA)

3. **Terms of Service:**
   - Clearly communicate AI nature of assistant
   - Provide opt-out to human agent
   - Disclaimer for quote accuracy

---

**Deployment guide for Jay's Frames AI Call Center**
*Version 1.0 - Ready for Production*

For questions or issues, refer to the project documentation or Azure support.
