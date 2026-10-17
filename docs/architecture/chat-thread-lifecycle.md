# Chat Thread Lifecycle and Staleness Protection

Owning modules:
- `ui/views/chat.js`: Client-side thread selection, idempotent creation, request staleness guards, and immediate title synchronization.
- `api/chat.py`: `POST /chat/threads` idempotent reuse of empty threads (`200 OK` vs `201 Created`).
- `core/storage/interfaces.py`: `ChatStore.newest_empty_thread()`.
- `core/storage/sqlite/chat.py`: SQLite implementation of `newest_empty_thread()`.
- `core/storage/postgres/adapters.py`: Postgres implementation of `newest_empty_thread()`.

## Purpose

1. **Thread Deduplication and Empty Thread Reuse:** Prevents runaway creation of blank threads when the owner clicks "+ New chat" multiple times or repeatedly without sending messages. If the newest thread for the owner contains zero messages, both client and server reuse it (`200 OK`) instead of inserting another row (`201 Created`).
2. **In-Flight Staleness Protection:** Guarantees that slow or delayed asynchronous fetches (`api.chat()`, `api.threads()`, `selectThread()`) never overwrite or revert the active view when switching threads or creating a new chat. Monotonic token counters (`historyToken`, `threadsToken`, `selectToken`) and `threadId` matching drop stale responses.
3. **Immediate Header and Rail Title Updates:** Refreshes thread title in the sidebar rail and top conversation header immediately when the first user message is accepted by the server (`POST /chat` 202 Accepted), rather than waiting for the entire background job to finish.
4. **Active Thread Retention and Sort Stability:** `loadThreads()` never switches or drops `threadId` on its own. If the active thread is temporarily missing from the server thread list, the UI preserves it and inserts a local placeholder row in the history rail. The legacy `default` thread is normalized to `null` (hash `#/chat`) so `thread_id: "default"` is never dispatched. On the server, `list_threads` normalizes timestamps to ISO UTC (`...Z`) and sorts just-created empty threads ahead of existing threads on timestamp ties.

## Architecture and Interaction Flow

```mermaid
sequenceDiagram
    autonumber
    actor User as Owner
    participant View as ui/views/chat.js
    participant API as api/chat.py
    participant Store as ChatStore (SQLite / Postgres)

    Note over User,View: 1. Idempotent Thread Creation
    User->>View: Click "+ New chat" (multiple clicks)
    alt Already on empty thread or creatingThread is true
        View-->>User: Focus composer (no network call)
    else First click on non-empty thread
        View->>API: POST /chat/threads
        API->>Store: newest_empty_thread(owner)
        alt Newest thread has no messages
            Store-->>API: thread {id, title}
            API-->>View: 200 OK {id, title} (reused)
        else All threads have messages or no threads exist
            API->>Store: create_thread(owner)
            Store-->>API: new_thread {id, title}
            API-->>View: 201 Created {id, title}
        end
        View->>View: threadId = id, historyToken++, loadThreads()
    end

    Note over User,View: 2. Staleness Token Guard
    User->>View: Select Thread A (slow fetch starts, token = 1)
    View->>API: GET /chat?thread_id=A
    User->>View: Rapidly Select Thread B (token = 2)
    View->>API: GET /chat?thread_id=B
    API-->>View: Response for B (token matches 2) -> Renders Thread B
    API-->>View: Delayed Response for A (token 1 != current 2) -> Dropped silently

    Note over User,View: 3. Immediate Title Synchronization
    User->>View: Send first message "What is my balance?"
    View->>API: POST /chat {thread_id, message}
    API->>Store: update thread title to message prefix
    API-->>View: 202 Accepted {job_id, status: "queued"}
    View->>View: await loadThreads() -> Update rail row & chat-title
    View->>API: Poll / stream job progress until done
```

## Staleness Tokens and Invariants

| Token | Scope | Invariant Checked | Effect when Violated |
| :--- | :--- | :--- | :--- |
| `historyToken` | `loadHistory()` | `token === historyToken && threadId === forId` | Discards delayed history payload from prior thread or cleared chat; prevents rendering ghost messages. |
| `threadsToken` | `loadThreads()` | `token === threadsToken` | Prevents outdated thread lists from reverting `threadId` back to an older thread. |
| `selectToken` | `selectThread(id)` | `token === selectToken && threadId === id` | Prevents race conditions during rapid thread switching in the sidebar rail. |

## Data Flow & Storage Contract

```mermaid
erDiagram
    CHAT_THREADS {
        string id PK
        string owner FK
        string title "Defaults to 'New chat', updated on first message"
        timestamp created_at
        timestamp last_message_at
        string status "running | done | blocked"
    }

    CHAT_MESSAGES {
        string id PK
        string thread_id FK
        string role "user | agent"
        string text
        string job_id FK
        timestamp created_at
        string intent "question | clarify | action | refusal (nullable, migration 0006)"
    }

    CHAT_THREADS ||--o{ CHAT_MESSAGES : "contains (empty if count == 0)"
```

## Intent Persistence (Migration 0006)

Migration `core/storage/migrations/0006_chat_intent.sql` adds the nullable `intent` column (`text`) to `chat_messages` (with column UPDATE permission for `aw_app`). SQLite (`core/storage/sqlite/chat.py`) and Postgres adapter (`core/storage/postgres/adapters.py`) persist and surface `intent` on agent messages without requiring backfill.
