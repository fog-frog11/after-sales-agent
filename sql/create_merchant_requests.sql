-- 按订单保存提交给店铺的售后申请和处理记录。
CREATE TABLE IF NOT EXISTS public.merchant_requests (
    order_id TEXT PRIMARY KEY REFERENCES public.orders (order_id),
    shop_id TEXT NOT NULL REFERENCES public.shop_return_policies (shop_id),
    reason TEXT NOT NULL CHECK (length(btrim(reason)) BETWEEN 1 AND 300),
    submitted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolution TEXT CHECK (length(btrim(resolution)) BETWEEN 1 AND 300),
    resolved_at TIMESTAMPTZ,
    resolved_by TEXT,
    CHECK ((resolution IS NULL) = (resolved_at IS NULL)),
    CHECK ((resolution IS NULL) = (resolved_by IS NULL))
);

CREATE INDEX IF NOT EXISTS merchant_requests_shop_queue
ON public.merchant_requests (shop_id, (resolved_at IS NOT NULL), order_id);
