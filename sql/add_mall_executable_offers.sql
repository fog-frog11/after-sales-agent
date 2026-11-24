-- 保存店铺提案快照和对应的本地商品退款登记。
CREATE TABLE IF NOT EXISTS public.mall_case_offers (
    case_no BIGINT NOT NULL,
    operation_id UUID NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('partial_refund', 'refund_only', 'return_refund', 'exchange', 'repair', 'reship')),
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    amount NUMERIC(20,2) CHECK (amount >= 0),
    return_instructions TEXT,
    PRIMARY KEY (case_no, operation_id),
    FOREIGN KEY (case_no, operation_id) REFERENCES public.mall_case_prompts,
    CHECK ((kind IN ('partial_refund', 'refund_only', 'return_refund')) = (amount IS NOT NULL)),
    CHECK (kind <> 'partial_refund' OR amount > 0),
    CHECK ((kind IN ('return_refund','exchange','repair')) = (return_instructions IS NOT NULL))
);
CREATE TABLE IF NOT EXISTS public.mall_item_refunds (
    case_no BIGINT PRIMARY KEY REFERENCES public.mall_cases,
    operation_id UUID NOT NULL,
    amount NUMERIC(20,2) NOT NULL CHECK (amount >= 0),
    closed_quantity INTEGER NOT NULL CHECK (closed_quantity >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (case_no, operation_id) REFERENCES public.mall_case_events (case_no, operation_id),
    CHECK (amount > 0 OR closed_quantity > 0)
);

-- 旧版已退款记录原来按申请件数和成交单价登记，按原语义并入余额查询。
CREATE OR REPLACE VIEW public.mall_refund_book AS
SELECT c.case_no, c.order_id, c.item_no, COALESCE(r.amount, i.paid_unit * c.quantity) AS amount,
    COALESCE(r.closed_quantity, c.quantity) AS closed_quantity, COALESCE(r.created_at, c.updated_at) AS recorded_at
FROM public.mall_cases c JOIN public.mall_items i ON i.order_id=c.order_id AND i.item_no=c.item_no
LEFT JOIN public.mall_item_refunds r ON r.case_no=c.case_no
WHERE c.status='refunded';
