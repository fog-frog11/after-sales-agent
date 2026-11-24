-- 保存 Olist 历史订单样本，查询编号与店铺退货演示分开。
CREATE TABLE IF NOT EXISTS public.olist_orders (
    source_order_id TEXT PRIMARY KEY CHECK (source_order_id ~ '^[0-9a-f]{32}$'),
    order_id TEXT NOT NULL UNIQUE CHECK (order_id = 'OL-' || upper(left(source_order_id, 10))),
    source_status TEXT NOT NULL CHECK (source_status IN ('delivered', 'shipped', 'canceled')),
    purchased_on DATE NOT NULL,
    estimated_on DATE,
    delivered_on DATE
);
