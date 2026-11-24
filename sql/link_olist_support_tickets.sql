-- 两类订单各用真实外键，每张工单恰好关联一笔订单。
ALTER TABLE public.support_tickets
    ALTER COLUMN order_id DROP NOT NULL,
    ADD COLUMN IF NOT EXISTS olist_order_id TEXT REFERENCES public.olist_orders (order_id);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'public.support_tickets'::regclass AND conname = 'support_tickets_exactly_one_order'
    ) THEN
        ALTER TABLE public.support_tickets
            ADD CONSTRAINT support_tickets_exactly_one_order
            CHECK ((order_id IS NULL) <> (olist_order_id IS NULL));
    END IF;
END;
$$;
