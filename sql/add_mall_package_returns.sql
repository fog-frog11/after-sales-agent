-- 整包退款沿用单一退款登记，按提交时核定的商品明细写入各自的账目。
CREATE OR REPLACE VIEW public.mall_refund_book AS
SELECT c.case_no, c.order_id, COALESCE(line.item_no, c.item_no) AS item_no,
       COALESCE(line.amount, r.amount, i.paid_unit * c.quantity) AS amount,
       COALESCE(line.quantity, r.closed_quantity, c.quantity) AS closed_quantity,
       COALESCE(r.created_at, c.updated_at) AS recorded_at
FROM public.mall_cases c JOIN public.mall_items i ON i.order_id=c.order_id AND i.item_no=c.item_no
LEFT JOIN public.mall_item_refunds r ON r.case_no=c.case_no
LEFT JOIN public.mall_case_events submission ON submission.case_no=c.case_no AND submission.operation_id=c.submission_id
LEFT JOIN LATERAL jsonb_to_recordset(submission.details->'package_lines')
    AS line(item_no integer, quantity integer, amount numeric) ON TRUE
WHERE c.status='refunded'
  AND (submission.details->'package_lines' IS NULL OR r.case_no IS NOT NULL);
