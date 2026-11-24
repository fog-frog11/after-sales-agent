-- 保存寄回信息与独立的复核记录，初次平台结论仍保留在 mall_disputes。
CREATE TABLE IF NOT EXISTS public.mall_return_details (
    case_no BIGINT PRIMARY KEY REFERENCES public.mall_cases,
    details JSONB CHECK (details IS NULL OR
        (jsonb_typeof(details) = 'object' AND details ?& ARRAY['recipient','address','phone'])),
    provided_at TIMESTAMPTZ,
    CHECK ((details IS NULL) = (provided_at IS NULL))
);
CREATE TABLE IF NOT EXISTS public.mall_dispute_reviews (
    review_no BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    case_no BIGINT NOT NULL REFERENCES public.mall_disputes,
    phase TEXT NOT NULL CHECK (phase IN ('return_details', 'receipt')),
    opened_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    decision TEXT CHECK (decision IN ('support_buyer', 'support_seller')),
    decision_at TIMESTAMPTZ,
    CHECK ((decision IS NULL) = (decision_at IS NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS mall_one_active_review ON public.mall_dispute_reviews (case_no) WHERE decision IS NULL;
CREATE INDEX IF NOT EXISTS mall_case_reviews ON public.mall_dispute_reviews (case_no, review_no);
