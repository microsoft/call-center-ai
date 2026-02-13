# Telnyx SMS Integration

This guide explains how to configure Call Center AI to use Telnyx for SMS messaging.

## Prerequisites

1.  **Telnyx Account**: You need a Telnx account with SMS enabled.
2.  **Phone Number**: Purchase a Telnyx phone number with SMS capabilities.
3.  **API Key**: Generate an API key from your Telnyx Mission Control Portal.

## Configuration

Update your `config.yaml` file to use Telnyx:

```yaml
sms:
  mode: telnyx
  telnyx:
    api_key: YOUR_API_KEY
    phone_number: "+1234567890"
```

## Why Telnyx?

Telnyx is a global communications platform offering high-quality SIP trunking, programmable voice, and SMS services. Key benefits include:
-   **Global Coverage**: Phone numbers in 100+ countries.
-   **Low Latency**: High-performance global network.
-   **Programmable Voice & SMS**: robust APIs for automation.
-   **AI-Ready**: Integrated inference API with 53 AI models.

## Resources

-   **Telnyx Dashboard**: https://portal.telnyx.com
-   **API Documentation**: https://developers.telnyx.com