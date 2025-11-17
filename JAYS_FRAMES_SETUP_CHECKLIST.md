# Jay's Frames - Production Setup Checklist

Use this checklist to ensure you complete all steps for production deployment.

## Phase 1: Azure Account Setup

- [ ] Create Azure account (or use existing)
- [ ] Verify billing is set up
- [ ] Estimate monthly budget ($185-650 based on call volume)
- [ ] Set up billing alerts

## Phase 2: Azure Resources Creation

### Required Services

- [ ] Create Resource Group (`jays-frames-rg`)
- [ ] Deploy Azure Communication Services
  - [ ] Purchase phone number with calling + SMS
  - [ ] Note phone number: __________________
  - [ ] Save access key
- [ ] Deploy Azure Cognitive Services (multi-service)
  - [ ] Save endpoint
  - [ ] Save access key
- [ ] Deploy Azure OpenAI Service
  - [ ] Deploy `gpt-4.1-nano` model
  - [ ] Deploy `gpt-4.1` model
  - [ ] Deploy `text-embedding-3-large` model
  - [ ] Save endpoints for each
- [ ] Deploy Azure Cosmos DB
  - [ ] Create database: `call-center`
  - [ ] Create container: `calls-v3`
- [ ] Deploy Azure AI Search
  - [ ] Create index: `trainings`
- [ ] Deploy Azure Storage Account
  - [ ] Create containers: `recordings`, `public`
- [ ] Deploy Azure Container Registry
  - [ ] Note registry name: __________________
- [ ] Deploy Azure Container Apps
  - [ ] Create environment
  - [ ] Note app URL: __________________
- [ ] Deploy Azure Application Insights
  - [ ] Note instrumentation key
- [ ] Deploy Azure Cache for Redis

### Optional but Recommended

- [ ] Set up Azure Key Vault for secrets
- [ ] Configure custom domain name
- [ ] Set up SSL certificate
- [ ] Enable Azure Private Link

## Phase 3: Local Configuration

- [ ] Clone repository to local machine
- [ ] Install Python 3.12+
- [ ] Install Azure CLI
- [ ] Install Docker (optional)
- [ ] Create virtual environment
- [ ] Install dependencies: `pip install -r requirements.txt`

### Configuration File Setup

- [ ] Copy `config-jays-frames.yaml` to `config.yaml`
- [ ] Update `agent_phone_number` (your business phone)
- [ ] Update `bot_company` (if different from "Jay's Frames")
- [ ] Update `bot_name` (if different from "Jordan")
- [ ] Update `communication_services`:
  - [ ] endpoint
  - [ ] phone_number
  - [ ] access_key
  - [ ] resource_id
- [ ] Update `cognitive_service`:
  - [ ] endpoint
  - [ ] region
  - [ ] resource_id
- [ ] Update `llm`:
  - [ ] fast.endpoint
  - [ ] slow.endpoint
- [ ] Update `ai_search`:
  - [ ] endpoint
  - [ ] embedding_endpoint
- [ ] Update `ai_translation` (if using)

## Phase 4: Testing

### Local Testing

- [ ] Run `make dev` successfully
- [ ] Test health endpoint: `curl localhost:8080/health/liveness`
- [ ] Test readiness endpoint: `curl localhost:8080/health/readiness`
- [ ] Review logs for errors

### Integration Testing

- [ ] Set up Dev Tunnel for local testing with Azure
- [ ] Update config with tunnel URL
- [ ] Make test call to bot
- [ ] Verify bot answers
- [ ] Test conversation flow
- [ ] Verify data collection
- [ ] Test human transfer
- [ ] Verify SMS follow-up
- [ ] Check call appears in Cosmos DB
- [ ] Review call in Application Insights

### Test Scenarios

Run through all test scenarios in `tests/conversations-jays-frames.yaml`:

- [ ] Simple painting frame request
- [ ] Family photos with budget constraints
- [ ] Diploma framing quote
- [ ] Canvas with special requirements
- [ ] Multiple items consultation
- [ ] Watercolor with matting discussion
- [ ] Spanish language customer
- [ ] Military memorabilia
- [ ] Unknown size artwork
- [ ] Rush order

## Phase 5: Content & Knowledge Base

### Training Materials

- [ ] Create framing guide document
- [ ] Document frame style recommendations
- [ ] Document matting color guide
- [ ] Document glass type comparisons
- [ ] Document pricing guidelines
- [ ] Document conservation framing info
- [ ] Upload all training docs to AI Search
- [ ] Test knowledge retrieval

### Prompts Review

- [ ] Review default system prompt
- [ ] Review chat system prompt
- [ ] Review example conversations
- [ ] Adjust tone/personality if needed
- [ ] Review pricing ranges in prompts
- [ ] Update with actual business info

## Phase 6: Production Deployment

### Build & Push

- [ ] Build Docker image
- [ ] Push to Azure Container Registry
- [ ] Tag with version number

### Deploy

- [ ] Deploy to Azure Container Apps
- [ ] Set environment variables
- [ ] Configure auto-scaling
- [ ] Set up health checks
- [ ] Verify deployment successful

### DNS & Domain

- [ ] Configure custom domain (optional)
- [ ] Set up DNS records
- [ ] Enable SSL/TLS
- [ ] Test HTTPS access

## Phase 7: Monitoring & Alerts

### Application Insights

- [ ] Verify telemetry is flowing
- [ ] Create dashboard for:
  - [ ] Call volume
  - [ ] Call duration
  - [ ] Error rates
  - [ ] Response times
  - [ ] Cost metrics

### Alerts

- [ ] Set up alert for high error rate
- [ ] Set up alert for slow response times
- [ ] Set up alert for service downtime
- [ ] Set up alert for high costs
- [ ] Test alert delivery (email/SMS)

### Log Queries

- [ ] Save query for recent calls
- [ ] Save query for errors
- [ ] Save query for call durations
- [ ] Save query for customer feedback

## Phase 8: Security & Compliance

### Security

- [ ] Verify rate limiting is active
- [ ] Verify security headers are set
- [ ] Review CORS configuration
- [ ] Set up Azure Defender
- [ ] Configure network security groups
- [ ] Review RBAC permissions
- [ ] Scan for vulnerabilities
- [ ] Review secrets management

### Compliance

- [ ] Review call recording consent requirements
- [ ] Add recording notice to greeting
- [ ] Review data retention policies
- [ ] Document data privacy measures
- [ ] Review GDPR compliance (if applicable)
- [ ] Review CCPA compliance (if applicable)
- [ ] Create privacy policy
- [ ] Create terms of service

### Legal

- [ ] Consult with lawyer about:
  - [ ] Call recording laws in your jurisdiction
  - [ ] AI disclosure requirements
  - [ ] Quote accuracy disclaimers
  - [ ] Data privacy requirements

## Phase 9: Operations Setup

### Team Training

- [ ] Train team on accessing call transcripts
- [ ] Train team on reviewing Application Insights
- [ ] Train team on handling escalations
- [ ] Document response procedures
- [ ] Create runbook for common issues

### Backup & Recovery

- [ ] Verify Cosmos DB automatic backups
- [ ] Document restore procedure
- [ ] Test disaster recovery
- [ ] Set up configuration backups

### Maintenance Plan

- [ ] Schedule weekly call review
- [ ] Schedule monthly prompt updates
- [ ] Schedule quarterly pricing review
- [ ] Schedule security audits
- [ ] Document update procedures

## Phase 10: Launch

### Soft Launch

- [ ] Announce to small customer group
- [ ] Monitor first 10 calls closely
- [ ] Collect feedback
- [ ] Make adjustments
- [ ] Document improvements

### Full Launch

- [ ] Update business website with phone number
- [ ] Update Google Business listing
- [ ] Update social media
- [ ] Update business cards
- [ ] Update email signatures
- [ ] Announce to all customers

### Post-Launch

- [ ] Monitor call volume daily (first week)
- [ ] Review call quality daily (first week)
- [ ] Address any issues immediately
- [ ] Collect customer feedback
- [ ] Measure customer satisfaction
- [ ] Track quote conversion rate

## Phase 11: Optimization

### Week 1 Review

- [ ] Review all call transcripts
- [ ] Identify common issues
- [ ] Update prompts as needed
- [ ] Add missing training materials
- [ ] Adjust pricing estimates

### Month 1 Review

- [ ] Analyze call metrics
- [ ] Review cost vs. value
- [ ] Identify optimization opportunities
- [ ] Update knowledge base
- [ ] Refine conversation flows

### Ongoing

- [ ] Monthly cost analysis
- [ ] Monthly quality review
- [ ] Quarterly feature planning
- [ ] Annual security audit

## Success Metrics

Track these KPIs:

- [ ] Call volume per day/week/month
- [ ] Average call duration
- [ ] Quote conversion rate
- [ ] Customer satisfaction (from call synthesis)
- [ ] Transfer to human rate
- [ ] Cost per call
- [ ] ROI calculation

## Support Resources

- **Deployment Guide:** `JAYS_FRAMES_DEPLOYMENT_GUIDE.md`
- **README:** `JAYS_FRAMES_README.md`
- **Azure Support:** https://azure.microsoft.com/support/
- **Project Docs:** https://github.com/microsoft/call-center-ai

---

## Quick Reference

**Critical Phone Numbers:**
- Bot Phone Number: __________________
- Business Phone (transfers): __________________

**Critical URLs:**
- Container App: __________________
- Application Insights: __________________
- Cosmos DB: __________________

**Access Keys Location:**
- Communication Services: Azure Portal → Keys
- Cognitive Services: Azure Portal → Keys
- OpenAI: Azure Portal → Keys and Endpoint

**Emergency Contacts:**
- Azure Support: __________________
- Team Lead: __________________
- Technical Contact: __________________

---

*Version 1.0 - Jay's Frames Production Setup*
