# Chat Quota Buckets and One-Voice Composition

Owning modules:
- `api/jobs.py`: `JobLimits`, `JobService` (quota check, action/inquiry classification, refund).
- `api/chat_compose.py`: `compose_agent_text` (one-voice composition).
- `core/agent/planner.py`: `FallbackPlanner` (keyword routing, swap support).
- `core/storage`: `JobStore` implementations (`SqliteJobs`, `PgJobStore`).

Specification: [`docs/design/webapp/chat-v3-spec.md`](../../design/webapp/chat-v3-spec.md).

## Purpose

1. **Two Quota Buckets:** Separates chat quota into `chat_action` (shares 6/hr limit with `run`) and `chat_inquiry` (own limit 60/hr). Chat jobs that finish with zero gateway proposals are recorded as inquiries and refund the action bucket.
2. **Fallback Swap Fix:** Removes `swap` from `_OWNER_ONLY_WORDS` in `FallbackPlanner` so swapping is not falsely refused as an owner-only action.
3. **One Voice:** Removes the boilerplate "No transaction was made." prefix and "Agent says: " label. For questions and no-action replies, the model reply is the entire text; for actions with orders, the model reply serves as a lead-in line before the code-recorded facts.

## Architecture and Flow

```mermaid
flowchart TD
    User["Owner (POST /chat)"] --> API["api/chat.py"]
    API --> Start["JobService.start(kind='chat')"]
    Start --> QCheck{"Quota Check<br/>Action < 6 and Inquiry < 60?"}
    QCheck -->|No| R429["429 RateLimited"]
    QCheck -->|Yes| Enqueue["JobStore.create(kind='chat_action')"]
    Enqueue --> Worker["Worker Thread (_execute)"]
    Worker --> Planner["Planner (OpenAI or Fallback)"]
    Planner --> Runner["Runner.run_instruction"]
    Runner --> GW{"Gateway proposals?"}
    GW -->|>= 1 orders| ActionRec["JobStore.update(kind='chat_action')<br/>Consumes action bucket"]
    GW -->|0 orders| InqRec["JobStore.update(kind='chat_inquiry')<br/>Action bucket refunded"]
    ActionRec --> Compose["api.chat_compose::compose_agent_text<br/>Lead-in reply + Code facts"]
    InqRec --> ComposeInq["api.chat_compose::compose_agent_text<br/>Model reply only (one voice)"]
    Compose --> ChatStore["ChatStore.append('agent', text)"]
    ComposeInq --> ChatStore
```

## Data Schema & Accounting

```mermaid
erDiagram
    JOBS {
        string job_id PK
        string kind "chat_action | chat_inquiry | run | mint | withdraw"
        string status "queued | running | succeeded | denied | failed"
        string planner_client "openai | fallback | scripted | none"
        json order_ids "IDs of orders proposed to gateway"
        json seqs "Audit log sequences"
        timestamp created_at
        timestamp finished_at
    }
```
