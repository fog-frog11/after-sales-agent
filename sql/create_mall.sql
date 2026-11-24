-- 本地商城的运营数据。历史研究订单依然保存在原表。
CREATE TABLE IF NOT EXISTS public.mall_shops (
    shop_id TEXT PRIMARY KEY,
    name TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS public.mall_shop_staff (
    account_id TEXT NOT NULL,
    shop_id TEXT NOT NULL REFERENCES public.mall_shops,
    PRIMARY KEY (account_id, shop_id)
);
CREATE TABLE IF NOT EXISTS public.mall_terms (
    shop_id TEXT NOT NULL REFERENCES public.mall_shops,
    version INTEGER NOT NULL CHECK (version > 0),
    effective_on DATE NOT NULL,
    title TEXT NOT NULL,
    return_days SMALLINT NOT NULL CHECK (return_days > 0),
    fault_days SMALLINT NOT NULL CHECK (fault_days >= return_days),
    warranty_days SMALLINT NOT NULL CHECK (warranty_days >= fault_days),
    reply_hours SMALLINT NOT NULL CHECK (reply_hours > 0),
    return_postage NUMERIC(9,2) NOT NULL CHECK (return_postage >= 0),
    seller_advances BOOLEAN NOT NULL,
    PRIMARY KEY (shop_id, version),
    UNIQUE (shop_id, effective_on)
);
CREATE TABLE IF NOT EXISTS public.mall_orders (
    order_id TEXT PRIMARY KEY,
    buyer_id TEXT NOT NULL,
    shop_id TEXT NOT NULL,
    terms_version INTEGER NOT NULL,
    ordered_at TIMESTAMPTZ NOT NULL,
    FOREIGN KEY (shop_id, terms_version) REFERENCES public.mall_terms
);
CREATE INDEX IF NOT EXISTS mall_orders_buyer ON public.mall_orders (buyer_id, order_id);
CREATE TABLE IF NOT EXISTS public.mall_packages (
    order_id TEXT NOT NULL REFERENCES public.mall_orders,
    package_no INTEGER NOT NULL CHECK (package_no > 0),
    stage TEXT NOT NULL CHECK (stage IN ('preparing', 'in_transit', 'delivered')),
    carrier TEXT,
    tracking TEXT,
    delivered_at TIMESTAMPTZ,
    expected_on DATE,
    PRIMARY KEY (order_id, package_no),
    CHECK ((stage = 'delivered') = (delivered_at IS NOT NULL)),
    CHECK ((stage = 'preparing' AND carrier IS NULL AND tracking IS NULL)
        OR (stage <> 'preparing' AND carrier IS NOT NULL AND tracking IS NOT NULL))
);
CREATE TABLE IF NOT EXISTS public.mall_items (
    order_id TEXT NOT NULL,
    item_no INTEGER NOT NULL CHECK (item_no > 0),
    package_no INTEGER NOT NULL,
    sku TEXT NOT NULL,
    title TEXT NOT NULL,
    variant TEXT NOT NULL,
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    list_unit NUMERIC(9,2) NOT NULL CHECK (list_unit >= 0),
    paid_unit NUMERIC(9,2) NOT NULL CHECK (paid_unit BETWEEN 0 AND list_unit),
    return_credit NUMERIC(9,2) NOT NULL CHECK (return_credit >= 0),
    change_of_mind BOOLEAN NOT NULL,
    repairable BOOLEAN NOT NULL,
    PRIMARY KEY (order_id, item_no),
    FOREIGN KEY (order_id, package_no) REFERENCES public.mall_packages
);

-- 商品售后业务单、共享事件，以及与原售后单一对一关联的争议。
CREATE TABLE IF NOT EXISTS public.mall_cases (
    case_no BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    submission_id UUID NOT NULL UNIQUE,
    order_id TEXT NOT NULL,
    item_no INTEGER NOT NULL,
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    kind TEXT NOT NULL CHECK (kind IN ('refund_only','return_refund','exchange','repair','reship')),
    reason TEXT NOT NULL CHECK (reason IN ('wrong_purchase','quality','wrong_item','missing','not_received','cancel')),
    description TEXT NOT NULL CHECK (length(btrim(description)) BETWEEN 1 AND 500),
    status TEXT NOT NULL CHECK (status IN ('submitted','awaiting_return','return_sent','received','awaiting_outcome',
        'rejected','disputed','refunded','replacement_sent','reshipped','repaired','closed','withdrawn')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    due_at TIMESTAMPTZ,
    FOREIGN KEY (order_id, item_no) REFERENCES public.mall_items
);
CREATE INDEX IF NOT EXISTS mall_cases_order ON public.mall_cases (order_id, case_no DESC);
CREATE UNIQUE INDEX IF NOT EXISTS mall_cases_active_item ON public.mall_cases (order_id, item_no)
    WHERE status IN ('submitted','awaiting_return','return_sent','received','awaiting_outcome','disputed','rejected');
CREATE TABLE IF NOT EXISTS public.mall_case_events (
    event_no BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    case_no BIGINT NOT NULL REFERENCES public.mall_cases,
    operation_id UUID NOT NULL,
    actor_id TEXT NOT NULL,
    actor_role TEXT NOT NULL CHECK (actor_role IN ('buyer','merchant','staff')),
    action TEXT NOT NULL,
    status TEXT NOT NULL,
    note TEXT NOT NULL,
    details JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (case_no, operation_id)
);
CREATE TABLE IF NOT EXISTS public.mall_disputes (
    case_no BIGINT PRIMARY KEY REFERENCES public.mall_cases,
    previous_status TEXT NOT NULL CHECK (previous_status IN ('submitted','rejected','return_sent','received','awaiting_outcome','awaiting_return')),
    opened_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    decision TEXT CHECK (decision IN ('support_buyer','support_seller')),
    decision_at TIMESTAMPTZ,
    CHECK ((decision IS NULL) = (decision_at IS NULL))
);
