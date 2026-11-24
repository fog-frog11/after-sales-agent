-- 扩展现有审核记录。旧记录仍按整店缺件说明读取。
ALTER TABLE public.mall_supplement_reviews
    ADD COLUMN IF NOT EXISTS topic text NOT NULL DEFAULT 'missing'
        CHECK (topic IN ('missing','evidence','freight','return','refund','delivery','service')),
    ADD COLUMN IF NOT EXISTS title text,
    ADD COLUMN IF NOT EXISTS sku text,
    ADD COLUMN IF NOT EXISTS article_key uuid,
    ADD COLUMN IF NOT EXISTS revises_id bigint REFERENCES public.mall_supplement_reviews(id);

CREATE INDEX IF NOT EXISTS mall_supplement_article_history
    ON public.mall_supplement_reviews(shop_id,article_key,reviewed_at DESC,id DESC)
    WHERE decision='approved';
