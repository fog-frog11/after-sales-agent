-- 只保存已纳入本机样本的商品行，保留源品类和准确金额。
CREATE TABLE IF NOT EXISTS public.olist_order_items (
    source_order_id TEXT NOT NULL REFERENCES public.olist_orders (source_order_id),
    item_no INTEGER NOT NULL CHECK (item_no > 0),
    product_id TEXT NOT NULL CHECK (product_id ~ '^[0-9a-f]{32}$'),
    category TEXT,
    price NUMERIC(12, 2) NOT NULL CHECK (price >= 0),
    freight NUMERIC(12, 2) NOT NULL CHECK (freight >= 0),
    PRIMARY KEY (source_order_id, item_no)
);
