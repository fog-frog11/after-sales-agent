-- 商品级售后申请与阶段日志，平台介入工单沿用原有表。
CREATE TABLE IF NOT EXISTS public.after_sales_requests (
    request_no BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    request_id UUID NOT NULL UNIQUE,
    buyer_id TEXT NOT NULL,
    order_id TEXT NOT NULL,
    item_no INTEGER NOT NULL,
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    kind TEXT NOT NULL CHECK (kind IN ('return', 'exchange', 'repair')),
    reason TEXT NOT NULL CHECK (reason IN ('wrong_purchase', 'quality', 'wrong_item', 'other')),
    description TEXT NOT NULL CHECK (char_length(description) BETWEEN 1 AND 500),
    status TEXT NOT NULL DEFAULT 'submitted'
        CHECK (status IN ('submitted', 'awaiting_return', 'return_sent', 'received', 'completed', 'rejected')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (order_id, item_no) REFERENCES public.jd_research_items (order_id, item_no)
);
CREATE UNIQUE INDEX IF NOT EXISTS after_sales_one_open_item ON public.after_sales_requests (order_id, item_no)
    WHERE status IN ('submitted', 'awaiting_return', 'return_sent', 'received');
CREATE INDEX IF NOT EXISTS after_sales_queue_idx ON public.after_sales_requests (status, request_no DESC);
CREATE INDEX IF NOT EXISTS after_sales_buyer_order_idx ON public.after_sales_requests (buyer_id, order_id, request_no DESC);

CREATE TABLE IF NOT EXISTS public.after_sales_request_events (
    request_no BIGINT NOT NULL REFERENCES public.after_sales_requests (request_no),
    status TEXT NOT NULL CHECK (status IN ('submitted', 'awaiting_return', 'return_sent', 'received', 'completed', 'rejected')),
    actor TEXT NOT NULL,
    note TEXT NOT NULL,
    carrier TEXT,
    tracking TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (request_no, status)
);

-- 只放明确标记的费用演练值，历史京东订单本身无此信息。
CREATE TABLE IF NOT EXISTS public.after_sales_freight_practice (
    order_id TEXT NOT NULL REFERENCES public.jd_research_orders (order_id),
    reason TEXT NOT NULL CHECK (reason IN ('wrong_purchase', 'quality', 'wrong_item', 'other')),
    payer TEXT NOT NULL CHECK (payer IN ('seller', 'buyer')),
    quoted_fee NUMERIC(8,2) NOT NULL CHECK (quoted_fee >= 0),
    voucher NUMERIC(8,2) NOT NULL DEFAULT 0 CHECK (voucher >= 0),
    prepaid BOOLEAN NOT NULL DEFAULT false,
    PRIMARY KEY (order_id, reason),
    CHECK (payer = 'seller' OR NOT prepaid)
);
