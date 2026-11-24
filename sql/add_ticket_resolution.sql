-- 为已有工单表增加处理说明与时间。旧工单的待处理状态保持原值。
BEGIN;

ALTER TABLE public.support_tickets
    ADD COLUMN IF NOT EXISTS resolution TEXT CHECK (char_length(btrim(resolution)) BETWEEN 1 AND 300),
    ADD COLUMN IF NOT EXISTS resolved_at TIMESTAMPTZ;

COMMIT;
