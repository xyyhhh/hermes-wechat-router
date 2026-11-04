# Changelog

## 0.1.0

- Signed HTTP ingress for private and group WeChat messages.
- Durable per-conversation batching with quiet/max-wait and size limits.
- One native Jev request per batch with per-message decisions and topic association.
- Configurable Hermes profile routing, observation mode and token usage counters.
- Native Kanban CLI handoff with idempotent task creation and durable follow-up tasks.
- Persistent retries, recovery leases, local pause/resume and failure inspection.
