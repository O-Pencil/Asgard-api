# Catea Credit Accounting

This document defines the provider-neutral accounting standard for hosted text
model usage. It intentionally does not identify an upstream model vendor,
subscription, credential source, or private commercial arrangement.

## Scope

The standard applies to successful hosted text-model requests charged against a
Catea monthly allowance or purchased credit balance. Catea meters its own
entitlements independently from upstream account balances.

- Catea does not query an upstream balance to decide whether a request is
  authorized.
- Upstream rate limits and capacity limits remain upstream concerns. Standard
  upstream errors, including HTTP 429, are normalized by the gateway.
- A rejected request is not charged unless the upstream response contains
  authoritative usage for work that was already performed.
- Credentials and upstream account details must never be stored in usage events
  or returned by billing APIs.

## Accounting version

The initial weighted accounting version is `catea-credit-v1`.

One Catea Credit represents 40 weighted text tokens:

```text
1 Credit = 40 weighted tokens
```

The weights reflect the relative computational cost of the four usage classes.
They are Catea product constants, not a claim about any particular provider.

| Usage class | Weight per token | Credits per token | Credits per 10 tokens |
| --- | ---: | ---: | ---: |
| Uncached input | 1.00 | 0.025 | 0.25 |
| Output | 4.00 | 0.100 | 1.00 |
| Cache read | 0.20 | 0.005 | 0.05 |
| Cache write | 1.25 | 0.03125 | 0.3125 |

The normative formulas are:

```text
uncached_input_tokens = max(
  prompt_tokens_total - cache_read_tokens - cache_write_tokens,
  0
)

weighted_tokens =
    uncached_input_tokens
  + output_tokens * 4
  + cache_read_tokens * 0.2
  + cache_write_tokens * 1.25

credits = weighted_tokens / 40
```

If an upstream response does not distinguish cached input, all reported prompt
tokens are treated as uncached input and both cache counters are recorded as
zero. Reasoning tokens may be retained as an audit dimension, but must not be
charged again when they are already included in the reported output-token count.

## Integer implementation

Accounting must not use binary floating-point values. Store the charge in
microcredits:

```text
1 Credit = 1,000,000 microcredits
```

The exact integer formula for `catea-credit-v1` is:

```text
charged_microcredits =
    uncached_input_tokens * 25,000
  + output_tokens * 100,000
  + cache_read_tokens * 5,000
  + cache_write_tokens * 31,250
```

Microcredits accumulate across requests. User-facing values may be rounded for
display, but quota enforcement uses the unrounded integer balance.

## Capacity represented by 100,000 Credits

The exact raw-token capacity depends on the usage mix. The following rows show
single-class upper bounds, not a promise that a real request will contain only
one class.

| Hypothetical usage mix | Maximum raw tokens |
| --- | ---: |
| Input only | 4,000,000 |
| Output only | 1,000,000 |
| Cache reads only | 20,000,000 |
| Cache writes only | 3,200,000 |

For mixed text generation, every class is charged independently. For example:

```text
uncached input: 150,000 tokens
output:         600,000 tokens
cache read:           0 tokens
cache write:          0 tokens

weighted tokens = 150,000 + 600,000 * 4
                = 2,550,000

credits = 2,550,000 / 40
        = 63,750
```

Retries and revisions are separate model requests and consume additional Credits
when they complete with reported usage.

## Usage-event data

Each hosted request must produce at most one append-only usage event. The event
must retain enough detail to reproduce its charge without access to request or
response content.

| Field | Purpose |
| --- | --- |
| `request_id` | Idempotency and operational correlation |
| `customer_id` | Owner of the charged entitlement |
| `model_route` | Internal hosted route, without credentials |
| `accounting_version` | The immutable rule version, initially `catea-credit-v1` |
| `prompt_tokens_total` | Total prompt tokens reported for the request |
| `uncached_input_tokens` | Prompt tokens charged at the standard input weight |
| `output_tokens` | Completion tokens, including reasoning when reported that way |
| `cache_read_tokens` | Input tokens served from cache |
| `cache_write_tokens` | Input tokens written to cache |
| `reasoning_tokens` | Optional audit-only subset of output tokens |
| `weighted_token_millis` | Weighted tokens multiplied by 1,000 for exact audit math |
| `charged_microcredits` | Final integer charge applied to the ledger |
| `credit_source` | Monthly allowance, purchased balance, or a split between both |
| `status` | Completed, rejected, failed, or canceled |
| `created_at` | Accounting timestamp |

Do not store prompts, generated text, authorization headers, upstream API keys,
or upstream account balances in the usage event.

## Entitlement order and failures

Successful usage consumes entitlements in this order:

1. The active plan's monthly Credits.
2. Purchased Credits after the monthly allowance is exhausted.

The short reset window and monthly allowance are Catea product controls. They do
not mirror an upstream subscription window.

For failures:

| Outcome | Charge behavior |
| --- | --- |
| Local authorization or quota rejection | No charge |
| Upstream 429 before reported usage | No charge |
| Other upstream failure before reported usage | No charge |
| Successful response with authoritative usage | Charge reported usage |
| Interrupted stream with authoritative final usage | Charge reported usage once |
| Completed response without usage metadata | Apply the documented fallback estimator and mark the event as estimated |

Diagnostics and health checks are operational traffic and must not consume a
customer's Credits.

## Versioning

Weights and the number of weighted tokens represented by one Credit are immutable
within an accounting version. A future change must introduce a new version rather
than reinterpret historical events. Existing balances remain denominated in
Catea Credits; only new usage events use the newly activated accounting version.
