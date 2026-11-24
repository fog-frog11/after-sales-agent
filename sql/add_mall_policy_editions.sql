-- 平台约束和店铺发布记录均按版本保存；订单仍使用购买时的 mall_terms 版本。
CREATE TABLE IF NOT EXISTS public.mall_platform_policies (
    version integer PRIMARY KEY,
    return_days integer NOT NULL, fault_days integer NOT NULL, warranty_days integer NOT NULL,
    reply_hours integer NOT NULL, max_postage numeric(9,2) NOT NULL
);
INSERT INTO public.mall_platform_policies VALUES (1,7,30,30,48,12.00) ON CONFLICT DO NOTHING;

CREATE TABLE IF NOT EXISTS public.mall_shop_policy_editions (
    shop_id text NOT NULL, version integer NOT NULL,
    platform_version integer NOT NULL REFERENCES public.mall_platform_policies,
    item_credit numeric(9,2) NOT NULL CHECK (item_credit >= 0),
    guidance_key text NOT NULL, guidance text NOT NULL,
    published_at timestamptz NOT NULL DEFAULT clock_timestamp(), publisher text NOT NULL,
    PRIMARY KEY (shop_id,version),
    FOREIGN KEY (shop_id,version) REFERENCES public.mall_terms
);

-- 商家提交的配件补发说明须经平台审核，以购买时间确定适用范围。
CREATE TABLE IF NOT EXISTS public.mall_supplement_reviews (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    shop_id text NOT NULL REFERENCES public.mall_shops,
    note text NOT NULL, author text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    decision text CHECK (decision IN ('approved','rejected')),
    reviewer text, reviewed_at timestamptz, feedback text
);
CREATE INDEX IF NOT EXISTS mall_supplement_shop_date ON public.mall_supplement_reviews(shop_id,reviewed_at DESC,id DESC);
