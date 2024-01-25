# Call center claim AI phone bot

AI-powered call center solution with Azure and OpenAI GPT.

<!-- github.com badges -->
[![Last release date](https://img.shields.io/github/release-date/clemlesne/claim-ai-phone-bot)](https://github.com/clemlesne/claim-ai-phone-bot/releases)
[![Project license](https://img.shields.io/github/license/clemlesne/claim-ai-phone-bot)](https://github.com/clemlesne/claim-ai-phone-bot/blob/main/LICENSE)

## Overview

A French demo is avaialble on YouTube. Do not hesitate to watch the demo in x1.5 speed to get a quick overview of the project.

[![French demo](https://img.youtube.com/vi/gnnin34V7Zg/maxresdefault.jpg)](https://www.youtube.com/watch?v=gnnin34V7Zg)

Main interactions shown in the demo:

1. User calls the call center
2. The bot answers and the conversation starts
3. The bot stores conversation, claim and todo list in the database

Extract of the data stored during the call:

```json
{
  "claim": {
    "incident_date_time": "2024-01-11T19:33:41",
    "incident_description": "The vehicle began to travel with a burning smell and the driver pulled over to the side of the freeway.",
    "policy_number": "B01371946",
    "policyholder_contact_info": "[number masked for the demo]",
    "policyholder_name": "Clémence Lesne",
    "vehicle_info": "Ford Fiesta 2003"
  },
  "reminders": [
    {
      "description": "Check that all the information in Clémence Lesne's file is correct and complete.",
      "due_date_time": "2024-01-18T16:00:00",
      "title": "Check Clémence file"
    }
  ]
}
```

### Features

> [!NOTE]
> This project is a proof of concept. It is not intended to be used in production. This demonstrates how can be combined Azure Communication Services, Azure Cognitive Services and Azure OpenAI to build an automated call center solution.

- [x] Access the claim on a public website
- [x] Access to customer conversation history
- [x] Bot can be called from a phone number
- [x] Company products (= lexicon) can be understood by the bot (e.g. a name of a specific insurance product)
- [x] Create by itself a todo list of tasks to complete the claim
- [x] Customizable prompts
- [x] Disengaging from a human agent when needed
- [x] Fine understanding of the customer request with GPT-4 Turbo
- [x] Follow a specific data schema for the claim
- [x] Has access to a documentation database (few-shot training / RAG)
- [x] Help the user to find the information needed to complete the claim
- [x] Responses are streamed from the LLM to the user, to avoid long pauses
- [x] Send a SMS report after the call
- [x] Take back a conversation after a disengagement
- [ ] Call back the user when needed
- [ ] Simulate a IVR workflow

### User report after the call

A report is available at `https://[your_domain]/report/[phone_number]` (like `http://localhost:8080/report/%2B133658471534`). It shows the conversation history, claim data and reminders.

![User report](./docs/user_report.jpg)

### High level architecture

```mermaid
---
title: System diagram (C4 model)
---
graph
  user(["User"])
  agent(["Agent"])

  api["Claim AI"]

  api -- Transfer to --> agent
  api -. Send voice .-> user
  user -- Call --> api
```

### Component level architecture

```mermaid
---
title: Claim AI component diagram (C4 model)
---
graph LR
  agent(["Agent"])
  user(["User"])

  subgraph "Claim AI"
    api["API"]
    ai_search[("RAG\n(AI Search)")]
    communication_service_sms["SMS gateway\n(Communication Services)"]
    communication_service["Call gateway\n(Communication Services)"]
    db[("Conversations and claims\n(Cosmos DB or SQLite)")]
    event_grid[("Broker\n(Event Grid)")]
    gpt["GPT-4 Turbo\n(OpenAI)"]
  end

  api -- Answer with text --> communication_service
  api -- Few-shot training --> ai_search
  api -- Generate completion --> gpt
  api -- Save conversation --> db
  api -- Send SMS report --> communication_service_sms
  api -- Transfer to agent --> communication_service
  api -. Watch .-> event_grid

  communication_service -- Notifies --> event_grid
  communication_service -- Transfer to --> agent
  communication_service -. Send voice .-> user

  communication_service_sms -- Send SMS --> user

  user -- Call --> communication_service
```

## Local installation

### Prerequisites

Place a file called `config.yaml` in the root of the project with the following content:

```yaml
# config.yaml
api:
  root_path: "/"

database:
  # mode: cosmos_db
  # cosmos_db:
  #   container: calls
  #   database: claim-ai
  #   endpoint: https://xxx.documents.azure.com:443
  sqlite: {}

resources:
  public_url: "https://xxx.blob.core.windows.net/public"

workflow:
  agent_phone_number: "+33612345678"
  bot_company: Contoso
  bot_name: Robert

communication_service:
  access_key: xxx
  endpoint: https://xxx.france.communication.azure.com
  phone_number: "+33612345678"
  voice_name: fr-FR-DeniseNeural

cognitive_service:
  # Must be of type "AI services multi-service account"
  # See: https://learn.microsoft.com/en-us/azure/ai-services/multi-service-resource?tabs=macos&pivots=azportal#create-a-new-multi-service-resource
  endpoint: https://xxx.cognitiveservices.azure.com

openai:
  endpoint: https://xxx.openai.azure.com
  gpt_deployment: gpt
  gpt_model: gpt-4-1106-preview

ai_search:
  access_key: xxx
  endpoint: https://xxx.search.windows.net
  index: trainings
  semantic_configuration: default
```

If you want to use a Service Principal to authenticate to Azure, you can also add the following in a `.env` file:

```dotenv
AZURE_CLIENT_ID=xxx
AZURE_CLIENT_SECRET=xxx
AZURE_TENANT_ID=xxx
```

Then run:

```bash
# Install dependencies
make install
```

Also, a public file server is needed to host the audio files. Upload the files with `make copy-resources name=myinstance` (`myinstance` is the storage account name), or manually.

For your knowledge, this `resources` folder contains:

- Audio files (`xxx.wav`) to be played during the call
- [Lexicon file (`lexicon.xml`)](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/speech-synthesis-markup-pronunciation#custom-lexicon) to be used by the bot to understand the company products (note: any change [makes up to 15 minutes](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/speech-synthesis-markup-pronunciation#custom-lexicon-file) to be taken into account)

### Run

Finally, in two different terminals, run:

```bash
# Expose the local server to the internet
make tunnel
```

```bash
# Start the local API server
make dev
```

## Remote deployment

Container is available on GitHub Actions, at:

- Latest version from a branch: `ghcr.io/clemlesne/claim-ai-phone-bot:main`
- Specific tag: `ghcr.io/clemlesne/claim-ai-phone-bot:0.1.0` (recommended)

Create a local `config.yaml` file (most of the fields are filled automatically by the deployment script):

```yaml
# config.yaml
api: {}

database:
  cosmos_db: {}

resources: {}

workflow:
  agent_phone_number: "+33612345678"
  bot_company: Contoso
  bot_name: Robert

communication_service:
  phone_number: "+33612345678"
  voice_name: fr-FR-DeniseNeural

cognitive_service:
  endpoint: https://xxx.cognitiveservices.azure.com

openai: {}

ai_search:
  index: trainings
  semantic_configuration: default
```

Steps to deploy:

1. Create an `Communication Services` resource plus a `Phone Number`
2. Create the local `config.yaml`
3. Connect to your Azure environment (e.g. `az login`)
4. Run deployment with `make deploy name=my-instance`
5. Wait for the deployment to finish
6. Create the AI Search index
7. Get the logs with `make logs name=my-instance`

## Advanced usage

### Add my custom training data

Training data is stored on AI Search to be retrieved by the bot, on demand.

An exampe is [available at `examples/import-training.ipynb`](examples/import-training.ipynb). It shows how to import training data from a PDF files dataset.

### Customize the prompts

Note that prompt examples contains `{xxx}` placeholders. These placeholders are replaced by the bot with the corresponding data. For example, `{bot_name}` is internally replaced by the bot name.

```yaml
# config.yaml
[...]

prompts:
  tts:
    hello_tpl: |
      Bonjour, je suis {bot_name}, de {bot_company} ! Je suis spécialiste du support informatique.

      Voici comment je fonctionne : lorsque je travaillerai, vous entendrez une petite musique ; après, au bip, ce sera à votre tour de parler. Vous pouvez me parler naturellement, je comprendrai.

      Exemples:
      - "J'ai un problème avec mon ordinateur, il ne s'allume plus"
      - "L'écran externe clignote, je ne sais pas pourquoi"

      Quel est votre problème ?
  llm:
    default_system_tpl: |
      Assistant is called {bot_name} and is in a call center for the company {bot_company} as an expert with 20 years of experience in IT service. Today is {date}. Customer is calling from {phone_number}. Call center number is {bot_phone_number}.
    chat_system_tpl: |
      Assistant will provide internal IT support to employees.

      Assistant:
      - Answers in {conversation_lang}, even if the employee speaks in a different language
      - Cannot talk about any topic other than IT support
      - Is polite, helpful, and professional
      - Rephrase the employee's questions as statements and answer them
      - Use additional context to enhance the conversation with useful details
      - When the employee says a word and then spells out letters, this means that the word is written in the way the employee spelled it (e.g. "I work in Paris PARIS", "My name is John JOHN", "My email is Clemence CLEMENCE at gmail GMAIL dot com COM")
      - You work for {bot_company}, not someone else

      Required employee data to be gathered by the assistant:
      - Employee name
      - Department
      - Location
      - Description of the IT issue or request

      General process to follow:
      1. Gather information to know the employee's identity (e.g. name, department)
      2. Gather details about the IT issue or request to understand the situation (e.g. description, location)
      3. Provide initial troubleshooting steps or solutions
      4. Gather additional information if needed (e.g. error messages, screenshots)
      5. Be proactive and create reminders for follow-up or further assistance

      Support status:
      {claim}

      Reminders:
      {reminders}

      Assistant requires data from the employee to provide IT support. The assistant's role is not over until the issue is resolved or the request is fulfilled.
```

### Customize the claim data schema

Customization of the data schema is not supported yet through the configuration file. However, you can customize the data schema by modifying the application source code.

The data schema is defined in `models/claim.py`. All the fields are required to be of type `Optional[str]` (except the immutable fields).

```python
# models/claim.py
class ClaimModel(BaseModel):
    # Immutable fields
    [...]
    # Editable fields
    employee_department: Optional[str] = None
    employee_email: Optional[str] = None
    employee_location: Optional[str] = None
    employee_name: Optional[str] = None
    employee_phone_number: Optional[str] = None
    issue_business_impact: Optional[str] = None
    issue_date_time: Optional[str] = None
    issue_description: Optional[str] = None
    issue_error_messages: Optional[str] = None
    issue_reproduction_steps: Optional[str] = None

    # Built-in functions
    [...]
