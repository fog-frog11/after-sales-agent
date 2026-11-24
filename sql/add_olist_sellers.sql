-- 为历史订单保留源商品明细中出现的匿名卖家编号。
ALTER TABLE public.olist_orders
ADD COLUMN IF NOT EXISTS seller_ids TEXT[] NOT NULL DEFAULT '{}';
