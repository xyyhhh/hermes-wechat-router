# Changelog

## 0.3.2

- Accept GatewayRunner's two injected session flags while retaining strict
  validation of Router settings.
- Verify platform creation through the real GatewayRunner registry path.

## 0.3.1

- Add an explicit receive-only mode: accept and deduplicate signed messages
  without a model API key, inference worker or task dispatcher.
- Expose the mode in Dashboard; preserve paused storage until processing is
  deliberately configured and resumed.
- Extend synthetic HTTP and actual Hermes adapter integration checks.

## 0.3.0

- Configure a third-party TypeSafe System One compatible provider, base/full API URL, model and timeout.
- Separate third-party API credentials from the official TypeSafe key; never fall back between providers.
- Edit the Jev routing policy in the Dashboard and restore the built-in policy.
- Persist the provider and policy with each batch; prevent pending retries from silently switching providers.

## 0.2.0

- Web Dashboard sidebar page for configuration, credentials, counters, pause/resume and failed-batch retries.
- Profile-scoped backend API and prebuilt frontend assets; no frontend build required for installation.

## 0.1.0

- Signed HTTP ingress for private and group WeChat messages.
- Durable per-conversation batching with quiet/max-wait and size limits.
- One native Jev request per batch with per-message decisions and topic association.
- Configurable Hermes profile routing, observation mode and token usage counters.
- Native Kanban CLI handoff with idempotent task creation and durable follow-up tasks.
- Persistent retries, recovery leases, local pause/resume and failure inspection.
