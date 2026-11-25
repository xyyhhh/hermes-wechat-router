# Changelog

## 0.6.0

- Persist every model attempt before sending, with full request JSON, raw bounded response body, HTTP status, timing and retry/batch context.
- Journal failures, timeouts, cancellations and invalid responses; preserve Google raw usage/probabilities and redact known API-key echoes.
- Add private audit status in Dashboard and mode-0600 JSONL export; no automatic retention cleanup.
- Exclude explicitly discarded backlog from all future model history while preserving delivery idempotency.
- Keep the inbox schema and model/dispatch behavior unchanged; both runtime and synthetic connection tests use the same audit sink.

## 0.5.2

- Fix Google Noul criteria to true_description/false_description rather than TypeSafe true/false.
- Reuse Hermes proxy resolution for both inference and Dashboard connection tests, including gateway.trust_env and NO_PROXY.
- Fail explicitly when a selected proxy connector is unavailable; retain TLS checks and no redirects/fallback.
- Distinguish timeout from connection errors and show safe, actionable error messages in Dashboard.
- Tighten fixtures to check Google’s actual wire field names and exercise real Hermes proxy policy.

## 0.5.1

- Align default policy, batch question and both modes’ criteria with conversation-isolated receive-order batching.
- Use bounded history only to interpret current messages; allow cross-batch confirmations and multiple topics.
- Delegate semantic deduplication and execution-state checks to the main assistant; do not suppress similar messages.
- Treat unconfirmed invitations and incomplete but identifiable events as potential handoffs, not automatic watch cases.
- Keep configured thresholds, grouping settings, cached snapshots and existing custom prompts intact on upgrade.

## 0.5.0

- Add Google Gemini Decisions native API: fixed /v1beta/decisions, JSON input text, x-goog-api-key and GEMINI_API_KEY.
- Support Google Noul/Choice requests, strict answer validation and normalized token usage.
- Add Google provider/model/secret controls and native request preview to Dashboard.
- Add an explicit saved-connection test using synthetic data without inbox reads or Kanban writes.
- Retain single-question batches, main-assistant handoffs, receive-only mode and provider-fenced retries.
- Verify both Google modes through local HTTP fixtures and real Hermes adapter/Kanban integration.

## 0.4.0

- Ask one event-detection question per merged batch; hand all actionable events to one main assistant.
- Configure native bool/Noul or Choice modes, question text, probability threshold and criteria in Dashboard.
- Preview the actual request JSON without inference, real messages or configuration writes.
- Main assistant receives the whole batch, context and related tasks for classification, splitting and delegation.
- Preserve receive-only mode and cached decisions; migrate undecided old batches and retarget only uncreated legacy tasks.
- Keep old routes/triage YAML readable for compatibility; new handoffs ignore category routing.

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
