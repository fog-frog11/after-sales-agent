-- 并行预览可对应同一张物流工单，其确认 ID 仍可重试。
CREATE TABLE IF NOT EXISTS public.support_ticket_confirmation_aliases (
    confirmation_id UUID PRIMARY KEY,
    ticket_no BIGINT NOT NULL REFERENCES public.support_tickets (ticket_no)
);
