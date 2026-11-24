-- 同一售后单的照片、待回复事项及按买家和商品隔离的咨询记录。
CREATE TABLE IF NOT EXISTS public.mall_case_photos (
    photo_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    event_no BIGINT NOT NULL REFERENCES public.mall_case_events,
    mime TEXT NOT NULL CHECK (mime IN ('image/jpeg', 'image/png')),
    image BYTEA NOT NULL CHECK (octet_length(image) BETWEEN 1 AND 1000000)
);
CREATE INDEX IF NOT EXISTS mall_case_photos_event ON public.mall_case_photos (event_no);

CREATE TABLE IF NOT EXISTS public.mall_case_prompts (
    case_no BIGINT NOT NULL REFERENCES public.mall_cases,
    operation_id UUID NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('evidence', 'offer')),
    note TEXT NOT NULL,
    answered_at TIMESTAMPTZ,
    response_action TEXT,
    PRIMARY KEY (case_no, operation_id),
    FOREIGN KEY (case_no, operation_id) REFERENCES public.mall_case_events (case_no, operation_id),
    CHECK ((answered_at IS NULL) = (response_action IS NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS mall_case_open_prompt ON public.mall_case_prompts (case_no)
    WHERE answered_at IS NULL;

CREATE TABLE IF NOT EXISTS public.mall_consultations (
    turn_no BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    buyer_id TEXT NOT NULL,
    order_id TEXT NOT NULL,
    item_no INTEGER NOT NULL,
    question TEXT NOT NULL CHECK (length(question) BETWEEN 1 AND 500),
    result JSONB NOT NULL,
    context JSONB NOT NULL,
    FOREIGN KEY (order_id, item_no) REFERENCES public.mall_items
);
CREATE INDEX IF NOT EXISTS mall_consultations_item ON public.mall_consultations (buyer_id, order_id, item_no, turn_no DESC);
