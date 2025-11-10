# Dependency Reduction & SIP Integration Migration Guide

This guide explains the changes made to reduce Azure dependencies and prepare for SIP gateway integration (e.g., Miralix).

## Overview

The codebase has been refactored to use **interface-based architecture** for core services, making it easy to swap between different providers:

- **Telephony**: Azure Communication Services ↔ SIP Gateway (Miralix, FreeSWITCH, Asterisk)
- **Storage**: Azure Cosmos DB ↔ SQLite (file-based)
- **Queues**: Azure Storage Queues ↔ Local in-memory queues

## What Changed

### 1. Telephony Abstraction (`ITelephony`)

**New Interface**: `app/persistence/itelephony.py`

All telephony operations (call control, audio streaming, recording) now go through the `ITelephony` interface.

**Implementations**:
- `AzureCommunicationServicesTelephony` - Azure Communication Services (existing, now refactored)
- `SipTelephony` - SIP gateway support (POC stub for now)

**Configuration**:
```yaml
telephony:
  mode: azure_communication_services  # or 'sip'

  # Azure Communication Services (uses existing communication_services config)
  azure_communication_services: {}

  # SIP Gateway (for Miralix, FreeSWITCH, etc.)
  sip:
    gateway_host: sip.miralix.example.com
    gateway_port: 5060
    username: your_username
    password: your_password
    phone_number: "+15551234567"
    transport: udp  # or tcp, tls
    rtp_port_range_start: 10000
    rtp_port_range_end: 20000
    stun_server: stun.l.google.com:19302  # optional
```

### 2. SQLite Storage (`SqliteStore`)

**New Implementation**: `app/persistence/sqlite_store.py`

Lightweight, file-based storage for local development. No database server needed!

**Configuration**:
```yaml
database:
  mode: sqlite  # or 'cosmos_db'

  sqlite:
    database_path: ./data/calls.db  # local file

  # cosmos_db:
  #   endpoint: ...
  #   database: ...
  #   container: ...
```

**Benefits**:
- ✅ No Azure account needed for development
- ✅ Single file database
- ✅ ACID transactions
- ✅ Great for testing and POCs
- ✅ Suitable for low-medium traffic production

### 3. Local Queues (`LocalQueue`)

**New Implementation**: `app/persistence/local_queue.py`

In-memory queue for local development (no Azure dependencies).

**Configuration**:
```yaml
queue:
  mode: local  # or 'azure_queue_storage'

  local: {}  # uses defaults

  # azure_queue_storage:
  #   account_url: ...
  #   call_name: ...
  #   post_name: ...
  #   sms_name: ...
  #   training_name: ...
```

**Note**: Local queues are in-memory only. Messages are lost on restart.

## Configuration Examples

### Full Local Development (No Azure!)

```yaml
# config.local.yaml
public_domain: https://your-domain.com

# Use SQLite for storage
database:
  mode: sqlite
  sqlite:
    database_path: ./data/calls.db

# Use local queues
queue:
  mode: local

# Use Azure Communication Services (still needed for telephony during POC)
telephony:
  mode: azure_communication_services

# SMS can use Twilio instead of Azure
sms:
  mode: twilio
  twilio:
    account_sid: your_sid
    auth_token: your_token
    phone_number: "+15551234567"

# Cache can be in-memory
cache:
  mode: memory

# ... other required config (STT/TTS, LLM, etc.)
```

### Hybrid Configuration (Local Storage + Azure Services)

```yaml
# config.hybrid.yaml
public_domain: https://your-domain.com

# Local storage for development
database:
  mode: sqlite
  sqlite:
    database_path: ./data/calls.db

# Local queues for development
queue:
  mode: local

# Azure telephony (for now)
telephony:
  mode: azure_communication_services

# Azure STT/TTS (keep as is)
cognitive_service:
  endpoint: https://your-region.api.cognitive.microsoft.com/

# ... rest of config
```

### Future: Full SIP Integration

```yaml
# config.sip.yaml (future state)
public_domain: https://your-domain.com

# SIP telephony with Miralix
telephony:
  mode: sip
  sip:
    gateway_host: sip.miralix.example.com
    gateway_port: 5060
    username: your_sip_username
    password: your_sip_password
    phone_number: "+15551234567"
    transport: udp
    rtp_port_range_start: 10000
    rtp_port_range_end: 20000

# SQLite for storage
database:
  mode: sqlite
  sqlite:
    database_path: ./data/calls.db

# Local queues
queue:
  mode: local

# Twilio for SMS
sms:
  mode: twilio

# Azure Cognitive Services still used for STT/TTS
cognitive_service:
  endpoint: https://your-region.api.cognitive.microsoft.com/

# ... rest of config
```

## SIP Integration Status

The SIP telephony implementation is currently a **POC stub** (`app/persistence/sip_telephony.py`).

**What's Ready**:
- ✅ Interface definition (`ITelephony`)
- ✅ Configuration model
- ✅ Architecture documentation in code
- ✅ Integration points identified

**What's Needed** (for production):
- ⚠️ SIP protocol library integration (pjsua2 or aiosip)
- ⚠️ RTP audio streaming implementation
- ⚠️ Codec handling (G.711, G.722 ↔ PCM 16kHz)
- ⚠️ WebSocket bridge for existing audio pipeline
- ⚠️ DTMF support (RFC 4733)
- ⚠️ NAT traversal (STUN/TURN)
- ⚠️ Call recording via RTP tap

**Architecture**:
```
SIP Gateway (Miralix)
    ↕ SIP Signaling (INVITE, BYE, etc.)
    ↕ RTP Audio (G.711/G.722)
SipTelephony Implementation
    ↕ Codec Conversion
    ↕ PCM 16kHz 16-bit mono
WebSocket Bridge
    ↕
Existing Audio Pipeline (STT, TTS, LLM)
```

## Running with New Configuration

### Install Dependencies

```bash
# Install new dependencies
uv sync

# Or with pip
pip install aiosqlite~=0.20
```

### Create Data Directory (for SQLite)

```bash
mkdir -p data
```

### Update Your Configuration

1. Copy your existing `config.yaml`
2. Add the new configuration sections (see examples above)
3. Set `database.mode: sqlite` and `queue.mode: local` for easy local development

### Run the Application

```bash
# Development
make dev

# Production
make prod
```

### Environment Variables

You can also configure via environment variables:

```bash
# Database mode
export DATABASE__MODE=sqlite
export DATABASE__SQLITE__DATABASE_PATH=./data/calls.db

# Queue mode
export QUEUE__MODE=local

# Telephony mode (future)
export TELEPHONY__MODE=sip
export TELEPHONY__SIP__GATEWAY_HOST=sip.example.com
```

## Benefits

### For Development
- ✅ **No Azure account needed** - Use SQLite + local queues
- ✅ **Faster setup** - No cloud resource provisioning
- ✅ **Easier debugging** - Local files, in-memory queues
- ✅ **Lower costs** - No cloud usage during development

### For Production
- ✅ **Vendor flexibility** - Not locked into Azure Communication Services
- ✅ **SIP integration ready** - Interface designed for Miralix, FreeSWITCH, Asterisk
- ✅ **Gradual migration** - Swap components one at a time
- ✅ **Cost optimization** - Choose cheapest/best option for each service

## Next Steps

1. **Test with local setup** - Try SQLite + local queues for development
2. **Plan SIP implementation** - If you need Miralix integration, we can implement the full SIP stack
3. **Gradual migration** - Move one component at a time (start with storage, then queues, finally telephony)

## SIP Implementation Roadmap

If you want to proceed with full SIP/Miralix integration:

### Phase 1: SIP Library Integration (1-2 days)
- Add pjsua2 or aiosip dependency
- Implement basic SIP signaling (INVITE, ACK, BYE)
- Test call setup and teardown with Miralix

### Phase 2: Audio Streaming (2-3 days)
- Implement RTP receiver/sender
- Add codec conversion (G.711/G.722 ↔ PCM 16kHz)
- Bridge RTP ↔ WebSocket for existing audio pipeline

### Phase 3: Advanced Features (1-2 days)
- DTMF support (RFC 4733)
- Call transfer (SIP REFER)
- Call recording (RTP tap)
- NAT traversal (STUN integration)

### Phase 4: Testing & Production (2-3 days)
- Load testing with Miralix gateway
- Failover and error handling
- Monitoring and metrics
- Documentation and deployment

**Total Estimate**: 1-2 weeks for production-ready SIP integration

## Questions?

This is a POC implementation to show the architecture. Let me know if you want to:
- Implement full SIP integration
- Add other telephony providers
- Further reduce Azure dependencies
- Deploy and test the current changes

---

**Files Changed**:
- `app/persistence/itelephony.py` - New telephony interface
- `app/persistence/azure_communication_services.py` - Azure Comms implementation
- `app/persistence/sip_telephony.py` - SIP stub implementation
- `app/persistence/sqlite_store.py` - SQLite storage
- `app/persistence/local_queue.py` - Local queue
- `app/helpers/config_models/telephony.py` - Telephony configuration
- `app/helpers/config_models/database.py` - Updated for SQLite
- `app/helpers/config_models/queue.py` - Updated for local queues
- `app/helpers/config_models/root.py` - Added telephony config
- `pyproject.toml` - Added aiosqlite dependency
