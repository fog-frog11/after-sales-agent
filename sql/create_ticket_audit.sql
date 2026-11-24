-- 每张工单只记录第一次成功的人工处理操作。
CREATE TABLE IF NOT EXISTS public.support_ticket_audit (
    ticket_no BIGINT PRIMARY KEY REFERENCES public.support_tickets (ticket_no),
    actor_id TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action = 'resolve'),
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
