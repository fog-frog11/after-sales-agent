-- 保存买家在确认前核对的交接版本，与原工单记录一并提交。
ALTER TABLE public.support_tickets ADD COLUMN IF NOT EXISTS handoff JSONB;
