# Hosted model error contract

The hosted endpoint returns stable, provider-neutral JSON errors. Clients must
branch on `error.code`, never on the English message or upstream status text.

```json
{
  "error": {
    "type": "catea_hosted_error",
    "code": "window_quota_exceeded",
    "message": "The current usage window has reached its limit.",
    "request_id": "hosted_...",
    "retryable": false,
    "reset_at": "2026-10-06T12:30:00Z"
  }
}
```

`request_id` is safe to share with support. `reset_at` is present only when a
known quota reset applies. Upstream response bodies, provider names, model
credentials and internal hostnames are never returned.

| Code | HTTP | Retry automatically | User action |
|---|---:|---:|---|
| `authorization_required` | 401 | No | Refresh the local plan binding. |
| `authorization_failed` | 401 | No | Refresh the plan or bind the purchase email again. |
| `subscription_required` | 402 | No | Subscribe to Pro. |
| `subscription_expired` | 402 | No | Renew Pro. |
| `window_quota_exceeded` | 429 | No | Wait until `reset_at`. |
| `monthly_quota_exceeded` | 429 | No | Wait until `reset_at` or purchase extra credits. |
| `invalid_json`, `invalid_request`, `invalid_messages` | 400 | No | Correct the request. |
| `upstream_rejected_request` | 400 | No | Remove unsupported tools or attachments. |
| `content_safety_rejected` | 4xx | No | Change the prompt. |
| `service_not_configured`, `entitlement_unavailable` | 503 | Yes | Retry later. |
| `content_safety_unavailable` | 503 | Yes | Retry later. |
| `upstream_rate_limited`, `upstream_unavailable` | 503 | Yes | Retry with backoff. |
| `upstream_timeout` | 504 | Yes | Retry with backoff. |
| `upstream_protocol_error` | 502 | Yes | Retry with backoff. |

Provider rate limits are deliberately translated to HTTP 503 so they are not
confused with a user's Catea quota. A valid `Retry-After` header is preserved.
The plugin retries transient failures only before any response content has been
emitted, preventing duplicate visible output.
