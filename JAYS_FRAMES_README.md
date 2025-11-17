# Jay's Frames - AI Call Center

**Custom AI-Powered Phone System for Jay's Frames Art Framing Business**

This is a customized deployment of the Microsoft Call Center AI system, tailored specifically for Jay's Frames custom art framing business.

## What This Does

Your customers can call a dedicated phone number 24/7 and speak with an AI assistant named "Jordan" who:

- ✅ Understands custom framing needs
- ✅ Gathers artwork details (type, size, style preferences)
- ✅ Discusses frame options, matting, and glass types
- ✅ Provides rough price estimates
- ✅ Collects customer information for quotes
- ✅ Transfers to human staff when needed
- ✅ Sends SMS summaries after calls
- ✅ Speaks English and Spanish

## Quick Start

### 1. Prerequisites

- Azure subscription
- Azure CLI installed
- Python 3.12+
- Docker (optional, for containerized deployment)

### 2. Install Dependencies

```bash
# Clone the repository
git clone https://github.com/YOUR-REPO/call-center-ai.git
cd call-center-ai

# Create virtual environment
python3.12 -m venv .venv
source .venv/bin/activate

# Install requirements
pip install -r requirements.txt
```

### 3. Configure for Your Business

```bash
# Copy the Jay's Frames configuration template
cp config-jays-frames.yaml config.yaml

# Edit config.yaml and fill in:
# - Your Azure resource endpoints
# - Your business phone number
# - Your purchased Azure Communication Services phone number
```

**Critical settings to update in `config.yaml`:**

```yaml
conversation:
  initiate:
    agent_phone_number: "+1YOUR-BUSINESS-PHONE"  # For transfers to humans

communication_services:
  phone_number: "+1YOUR-BOT-PHONE-NUMBER"        # The number customers call
  endpoint: "https://YOUR-ACS.communication.azure.com"
  access_key: "YOUR-ACCESS-KEY"
```

### 4. Deploy Azure Resources

See the complete [Deployment Guide](JAYS_FRAMES_DEPLOYMENT_GUIDE.md) for detailed instructions.

Quick deployment:
```bash
# Login to Azure
az login

# Deploy all resources
make deploy
```

### 5. Test Locally

```bash
# Run the development server
make dev

# The server starts at http://localhost:8080
```

### 6. Make Your First Test Call

Once deployed to Azure:
1. Call your Azure Communication Services phone number
2. The AI assistant "Jordan" will answer
3. Say: "I have a painting I need framed"
4. Follow the conversation and provide details

## What's Been Customized

This deployment includes Jay's Frames-specific customizations:

### 1. **Custom Data Collection** (`config-jays-frames.yaml`)
Collects framing-specific details:
- Artwork type and dimensions
- Frame style preferences
- Matting and glass options
- Budget and timeline
- Delivery preferences

### 2. **Framing-Specific AI Prompts**
The AI assistant is trained to:
- Understand framing terminology
- Make style recommendations
- Explain conservation framing
- Discuss pricing ranges
- Guide customers through options

### 3. **Business-Specific Tools** (`app/helpers/llm_tools_jays_frames.py`)
Optional custom functions:
- `search_frame_options()` - Recommend frames based on artwork
- `estimate_framing_cost()` - Provide price ranges
- `get_framing_advice()` - Answer framing questions
- `schedule_consultation()` - Book in-person visits

### 4. **Test Scenarios** (`tests/conversations-jays-frames.yaml`)
10 test conversations covering:
- Simple framing requests
- Budget discussions
- Conservation framing
- Rush orders
- Multi-language support

### 5. **Production Security**
- API rate limiting (60 requests/minute)
- Security headers (CSP, HSTS, etc.)
- Request size limits
- TLS enforcement

## File Structure

```
call-center-ai/
├── config-jays-frames.yaml          # Jay's Frames configuration
├── JAYS_FRAMES_DEPLOYMENT_GUIDE.md  # Complete deployment guide
├── JAYS_FRAMES_README.md            # This file
├── app/
│   ├── helpers/
│   │   ├── llm_tools_jays_frames.py # Custom framing tools
│   │   └── security_middleware.py   # Production security
│   └── main.py                      # Application (with security middleware)
├── tests/
│   └── conversations-jays-frames.yaml # Framing test scenarios
└── cicd/
    └── bicep/                       # Azure infrastructure templates
```

## Configuration Overview

### Bot Identity
- **Company:** Jay's Frames
- **Bot Name:** Jordan
- **Languages:** English (primary), Spanish
- **Voice:** Natural, multilingual neural voice

### Data Collected

Each call collects:
- Customer name and email
- Artwork details (type, dimensions, description)
- Frame preferences (style, material, color)
- Matting preferences
- Glass type (UV protection, non-glare, etc.)
- Budget range
- Project deadline
- Delivery/pickup preference
- Special requirements

### Conversation Flow

1. **Greeting** - Warm introduction
2. **Discovery** - What needs to be framed?
3. **Details** - Size, style, materials
4. **Options** - Frame, mat, glass recommendations
5. **Budget & Timeline** - Constraints and needs
6. **Contact** - Customer information
7. **Next Steps** - Quote follow-up or in-person visit
8. **SMS Summary** - Automated text message with details

## Monitoring & Analytics

### View Call Reports

```bash
# List recent calls
curl https://YOUR-DOMAIN/call?phone_number=+15551234567

# View specific call
curl https://YOUR-DOMAIN/call/CALL-ID

# Web interface
https://YOUR-DOMAIN/report/PHONE-NUMBER
```

### Application Insights

All calls are tracked in Azure Application Insights:
- Call volume and duration
- Customer sentiment
- Common requests
- Error rates
- Response times

### Cost Tracking

Monitor costs in Azure Cost Management:
- Communication Services (phone minutes)
- OpenAI API (conversation tokens)
- Speech Services (transcription)
- Storage and compute

## Common Operations

### Update Prompts

Edit `config-jays-frames.yaml` → `prompts` section and redeploy.

### Add Training Materials

Upload framing guides to AI Search:
```bash
python scripts/upload_training.py --file docs/framing-guide.md
```

### Adjust Pricing Ranges

Update the cost estimation logic in `llm_tools_jays_frames.py`:
```python
async def estimate_framing_cost(...):
    # Update your pricing logic here
```

### Change Bot Personality

Edit system prompts in config:
```yaml
prompts:
  llm:
    default_system_tpl: |
      Assistant is called {bot_name} and works at {bot_company}...
      [Customize the personality, expertise, tone here]
```

## Troubleshooting

**Bot doesn't answer:**
- Check phone number configuration
- Verify webhook URL is accessible
- Check Application Insights for errors

**Poor AI responses:**
- Review and refine system prompts
- Add more training materials
- Check conversation history for patterns

**High costs:**
- Review token usage in Azure OpenAI metrics
- Optimize conversation length
- Consider caching frequent responses

**For detailed troubleshooting**, see the [Deployment Guide](JAYS_FRAMES_DEPLOYMENT_GUIDE.md#troubleshooting).

## Support & Resources

- **Full Deployment Guide:** [JAYS_FRAMES_DEPLOYMENT_GUIDE.md](JAYS_FRAMES_DEPLOYMENT_GUIDE.md)
- **Original Project:** [microsoft/call-center-ai](https://github.com/microsoft/call-center-ai)
- **Azure Docs:** [Communication Services](https://learn.microsoft.com/en-us/azure/communication-services/)
- **OpenAI Docs:** [Azure OpenAI Service](https://learn.microsoft.com/en-us/azure/ai-services/openai/)

## Customization Examples

### Add a New Data Field

In `config-jays-frames.yaml`:
```yaml
claim:
  - name: installation_required
    type: text
    description: "Whether customer needs installation service"
```

### Create a Custom Tool

In `app/helpers/llm_tools_jays_frames.py`:
```python
@add_customer_response(["Let me check our schedule..."])
async def check_availability(
    self,
    desired_date: Annotated[str, "When customer wants service"],
) -> str:
    # Your logic here
    return "We have availability on..."
```

### Modify Voice Style

In `config-jays-frames.yaml`:
```yaml
lang:
  availables:
    - pronunciations_en: ["English"]
      short_code: "en-US"
      voice: "en-US-AriaNeural"  # Change to different voice
```

## Production Checklist

Before going live:

- [ ] All Azure resources deployed
- [ ] Phone number purchased and configured
- [ ] Configuration file updated with real values
- [ ] Test calls completed successfully
- [ ] SMS notifications working
- [ ] Monitoring alerts configured
- [ ] Team trained on reviewing call transcripts
- [ ] Knowledge base populated with framing guides
- [ ] Pricing estimates reviewed and accurate
- [ ] Human transfer number tested
- [ ] Legal compliance reviewed (call recording consent)

## Next Steps

1. **Review the full [Deployment Guide](JAYS_FRAMES_DEPLOYMENT_GUIDE.md)**
2. **Set up your Azure resources**
3. **Configure the system with your business details**
4. **Test thoroughly with the provided scenarios**
5. **Build your framing knowledge base**
6. **Train your team**
7. **Soft launch with a small customer group**
8. **Collect feedback and refine**
9. **Scale up!**

---

**Ready to transform your customer service with AI?**

Questions? Check the [Deployment Guide](JAYS_FRAMES_DEPLOYMENT_GUIDE.md) or Azure documentation.

*Customized for Jay's Frames - Version 1.0*
