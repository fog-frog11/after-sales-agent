-- 创建待处理工单表。一个确认 ID 只允许对应一张工单。
BEGIN;

CREATE TABLE IF NOT EXISTS public.support_tickets (
    ticket_no BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    confirmation_id UUID NOT NULL UNIQUE,
    order_id TEXT NOT NULL REFERENCES public.orders (order_id),
    reason TEXT NOT NULL CHECK (char_length(btrim(reason)) BETWEEN 1 AND 300),
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

COMMIT;
