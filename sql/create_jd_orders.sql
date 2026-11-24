-- 京东研究订单使用通用订单主键，以便沿用现有的工单和审计外键。
CREATE TABLE IF NOT EXISTS public.jd_research_orders (
    order_id TEXT PRIMARY KEY REFERENCES public.orders (order_id),
    source_key TEXT NOT NULL UNIQUE CHECK (source_key ~ '^[0-9a-f]{64}$'),
    source_status TEXT NOT NULL CHECK (source_status IN ('delivered', 'unknown')),
    purchased_on DATE NOT NULL,
    estimated_on DATE,
    delivered_on DATE,
    snapshot_on DATE NOT NULL,
    channel TEXT NOT NULL CHECK (channel IN ('self', 'market', 'mixed')),
    CHECK ((source_status = 'delivered') = (delivered_on IS NOT NULL))
);

CREATE TABLE IF NOT EXISTS public.jd_research_items (
    order_id TEXT NOT NULL REFERENCES public.jd_research_orders (order_id),
    item_no INTEGER NOT NULL CHECK (item_no > 0),
    sku_id TEXT NOT NULL,
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    unit_price NUMERIC(12,2) NOT NULL,
    sale_channel TEXT NOT NULL CHECK (sale_channel IN ('self', 'market')),
    PRIMARY KEY (order_id, item_no)
);

CREATE INDEX IF NOT EXISTS jd_research_order_cursor_idx ON public.jd_research_orders ((order_id COLLATE "C"));
