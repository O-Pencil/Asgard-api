ALTER TABLE public.asgard_billing_usage_periods
    ADD COLUMN used_microcredits BIGINT NOT NULL DEFAULT 0;

ALTER TABLE public.asgard_billing_usage_windows
    ADD COLUMN used_microcredits BIGINT NOT NULL DEFAULT 0;

ALTER TABLE public.asgard_billing_credit_balances
    ADD COLUMN balance_microcredits BIGINT NOT NULL DEFAULT 0;

ALTER TABLE public.asgard_billing_usage_events
    ADD COLUMN prompt_tokens_total INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN uncached_input_tokens INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN cache_read_tokens INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN cache_write_tokens INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN reasoning_tokens INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN weighted_token_millis BIGINT NOT NULL DEFAULT 0,
    ADD COLUMN charged_microcredits BIGINT NOT NULL DEFAULT 0,
    ADD COLUMN accounting_version VARCHAR(32) NOT NULL DEFAULT 'legacy-total-token-v0',
    ADD COLUMN credit_source VARCHAR(32) NOT NULL DEFAULT 'monthly',
    ADD COLUMN status VARCHAR(32) NOT NULL DEFAULT 'completed',
    ADD COLUMN usage_estimated BOOLEAN NOT NULL DEFAULT FALSE;

UPDATE public.asgard_billing_usage_periods
SET used_microcredits = GREATEST(used_credits, 0)::BIGINT * 1000000;

UPDATE public.asgard_billing_usage_windows
SET used_microcredits = GREATEST(used_credits, 0)::BIGINT * 1000000;

UPDATE public.asgard_billing_credit_balances
SET balance_microcredits = GREATEST(balance_credits, 0)::BIGINT * 1000000;

UPDATE public.asgard_billing_usage_events
SET prompt_tokens_total = GREATEST(input_tokens, 0),
    uncached_input_tokens = GREATEST(input_tokens, 0),
    weighted_token_millis = GREATEST(input_tokens + output_tokens, 0)::BIGINT * 1000,
    charged_microcredits = GREATEST(credits, 0)::BIGINT * 1000000,
    accounting_version = 'legacy-total-token-v0';

ALTER TABLE public.asgard_billing_usage_events
    ALTER COLUMN accounting_version SET DEFAULT 'catea-credit-v1';

ALTER TABLE public.asgard_billing_usage_periods
    ADD CONSTRAINT ck_billing_usage_periods_microcredits_nonnegative
    CHECK (used_microcredits >= 0);

ALTER TABLE public.asgard_billing_usage_windows
    ADD CONSTRAINT ck_billing_usage_windows_microcredits_nonnegative
    CHECK (used_microcredits >= 0);

ALTER TABLE public.asgard_billing_credit_balances
    ADD CONSTRAINT ck_billing_credit_balances_microcredits_nonnegative
    CHECK (balance_microcredits >= 0);

ALTER TABLE public.asgard_billing_usage_events
    ADD CONSTRAINT ck_billing_usage_events_accounting_nonnegative
    CHECK (
        prompt_tokens_total >= 0
        AND uncached_input_tokens >= 0
        AND output_tokens >= 0
        AND cache_read_tokens >= 0
        AND cache_write_tokens >= 0
        AND reasoning_tokens >= 0
        AND weighted_token_millis >= 0
        AND charged_microcredits >= 0
    );
