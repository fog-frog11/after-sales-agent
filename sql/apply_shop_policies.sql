-- 为订单补充店铺关联和退货政策，可在已有数据库重复执行。
BEGIN;

CREATE TABLE IF NOT EXISTS public.shop_return_policies (
    shop_id TEXT PRIMARY KEY,
    shop_name TEXT NOT NULL,
    policy_name TEXT NOT NULL,
    return_window_days INTEGER NOT NULL CHECK (return_window_days > 0)
);

INSERT INTO public.shop_return_policies (shop_id, shop_name, policy_name, return_window_days)
VALUES
    ('demo-standard', '青禾生活馆', '青禾签收后 7 天退货政策', 7),
    ('demo-extended', '栖木家居店', '栖木签收后 15 天退货政策', 15)
ON CONFLICT (shop_id) DO NOTHING;

ALTER TABLE public.orders
    ADD COLUMN IF NOT EXISTS shop_id TEXT REFERENCES public.shop_return_policies (shop_id);

UPDATE public.orders SET shop_id = 'demo-standard'
WHERE order_id IN ('A101', 'A102') AND shop_id IS NULL;

UPDATE public.orders SET shop_id = 'demo-extended'
WHERE order_id IN ('A103', 'A104') AND shop_id IS NULL;

INSERT INTO public.orders (order_id, signed_on, shop_id)
VALUES ('A104', DATE '2026-11-05', 'demo-extended')
ON CONFLICT (order_id) DO NOTHING;

COMMIT;
