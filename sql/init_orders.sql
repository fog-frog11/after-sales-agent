-- 初始化基础订单表和首批模拟订单。
BEGIN;

CREATE TABLE IF NOT EXISTS public.orders (
    order_id TEXT PRIMARY KEY,
    signed_on DATE
);

INSERT INTO public.orders (order_id, signed_on)
VALUES
    ('A101', DATE '2026-11-06'),
    ('A102', DATE '2026-11-05'),
    ('A103', NULL)
ON CONFLICT (order_id) DO NOTHING;

COMMIT;
