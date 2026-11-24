-- 买家演练收件快照与售后寄出包裹，分别关联原订单和售后单。
CREATE TABLE IF NOT EXISTS public.mall_order_destinations (
    order_id TEXT PRIMARY KEY REFERENCES public.mall_orders,
    details JSONB NOT NULL CHECK (jsonb_typeof(details) = 'object' AND details ?& ARRAY['recipient','address','phone'])
);
CREATE TABLE IF NOT EXISTS public.mall_outbound_parcels (
    parcel_no BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    case_no BIGINT NOT NULL REFERENCES public.mall_cases,
    destination JSONB NOT NULL CHECK (jsonb_typeof(destination) = 'object' AND destination ?& ARRAY['recipient','address','phone']),
    address_version INTEGER NOT NULL DEFAULT 1 CHECK (address_version > 0),
    stage TEXT NOT NULL CHECK (stage IN ('ready','shipped','received','superseded')),
    carrier TEXT,
    tracking TEXT,
    shipped_at TIMESTAMPTZ,
    received_at TIMESTAMPTZ,
    problem TEXT CHECK (problem IN ('not_dispatched','not_received','wrong_item','damaged')),
    issue_at TIMESTAMPTZ,
    CHECK ((carrier IS NULL) = (tracking IS NULL) AND (carrier IS NULL) = (shipped_at IS NULL)),
    CHECK ((stage = 'ready') = (shipped_at IS NULL)),
    CHECK ((stage = 'received') = (received_at IS NOT NULL)),
    CHECK ((problem IS NULL) = (issue_at IS NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS mall_outbound_one_open ON public.mall_outbound_parcels (case_no)
    WHERE stage IN ('ready','shipped');
CREATE UNIQUE INDEX IF NOT EXISTS mall_outbound_tracking ON public.mall_outbound_parcels
    (case_no, lower(tracking)) WHERE tracking IS NOT NULL;
CREATE INDEX IF NOT EXISTS mall_outbound_history ON public.mall_outbound_parcels (case_no, parcel_no DESC);

-- 仅补充本地 CN 演练订单的虚构收件信息。
INSERT INTO public.mall_order_destinations (order_id, details)
SELECT order_id, jsonb_build_object(
    'recipient', CASE buyer_id WHEN 'buyer-a' THEN '周宁' ELSE '沈悦' END,
    'address', CASE buyer_id WHEN 'buyer-a' THEN '杭州星桥街道澄景苑 1 栋' ELSE '苏州观澜街道知春苑 2 栋' END,
    'phone', '00000000000')
FROM public.mall_orders
WHERE order_id ~ '^CN-[12][0-9]{3}$' AND buyer_id IN ('buyer-a','buyer-b')
ON CONFLICT (order_id) DO NOTHING;
