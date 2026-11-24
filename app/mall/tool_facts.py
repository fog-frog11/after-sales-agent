"""给生成模型提供有类型的业务事实，业务兜底文案不参与事实构造。"""

from datetime import date, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from fastapi.encoders import jsonable_encoder

from app.mall.consultation import EVIDENCE_CHECKLIST
from app.mall.rules import KINDS, REASONS, policy_clauses


def build_facts(item: dict, result: dict, memory: dict, topics: list[str], cases: list[dict],
                today: date) -> dict:
    """每项事实标明类型和数据；不会把旧答复切成句子交给模型改写。"""
    facts=[]
    def add(fact_type, **data):
        facts.append({'kind':fact_type,'data':jsonable_encoder(data,custom_encoder={Decimal:lambda value:format(value,'f')})})
    selected=next((row for row in cases if str(row['case_no'])==str(result.get('case_no'))),None)
    if selected is None and cases:
        selected=next((row for row in cases if row.get('is_active')),cases[0])
    focus=set(topics)
    if focus=={'out_of_scope'}:
        add('service_scope',supported=['订单商品','物流','售后资格','寄回费用','售后进度','退款','协商与平台介入'])
    elif result.get('response_kind')=='clarification':
        add('clarification',question=result['answer'],already_known=memory.get('issue_description'))
    elif result.get('order_candidates') is not None:
        add('order_search',query_scope='signed_in_buyer',purchase_history=bool(result.get('purchase_history')),
            candidates=result['order_candidates'],displayed_count=len(result['order_candidates']),
            complete_history=False,selection_required=True,filters=result.get('candidate_filter'))
    else:
        add('item_identity',order_id=item.get('order_id'),item_no=item.get('item_no'),shop_id=item.get('shop_id'),
            title=item['title'],variant=item['variant'],purchase_policy_version=item['terms_version'])
        if selected is None and (focus & {'progress','refund','offer','return','outbound','platform','next_step','eligibility','fit_exchange'}
                or memory.get('issue_state') in ('resolved','packaging_only') or result.get('response_kind')=='intent_paused'):
            add('case_absence',case_exists=False,application_submitted=False)
        if 'product' in focus:
            add('product',title=item['title'],variant=item['variant'],ordered_quantity=item['quantity'],
                paid_unit=f"{item['paid_unit']:.2f}",item_line_total=f"{item['paid_unit']*item['quantity']:.2f}",
                price_scope='selected_item_line_excluding_shipping',remaining_quantity=item.get('remaining_quantity',item['quantity']))
        if 'logistics' in focus:
            delivered=item.get('delivered_at')
            add('original_logistics',stage=item['stage'],carrier=item.get('carrier'),tracking=item.get('tracking'),
                delivered_at=delivered.astimezone(ZoneInfo('Asia/Shanghai')).isoformat() if delivered else None,
                expected_on=item.get('expected_on'),expected_is_new_promise=False,live_carrier_tracking=False)
        checked=result.get('eligibility') is not None
        if 'eligibility' in focus or 'fit_exchange' in focus:
            signed=item.get('delivered_at')
            signed=signed.astimezone(ZoneInfo('Asia/Shanghai')).date() if signed else None
            deadlines={}
            window_days={}
            for kind in result.get('kinds',{}):
                days=item['warranty_days'] if kind=='repair' else item['return_days'] if memory.get('reason')=='wrong_purchase' else item['fault_days']
                window_days[kind]=days
                if signed:
                    deadlines[kind]=(signed+timedelta(days=days)).isoformat()
            add('eligibility',checked=checked,reason=memory.get('reason'),reason_label=REASONS.get(memory.get('reason')),
                available_kinds=result.get('kinds',{}),deadlines=deadlines,window_days=window_days,stage=item['stage'],
                remaining_quantity=item.get('remaining_quantity',item['quantity']),
                existing_case_no=selected['case_no'] if selected and selected.get('is_active') else None,
                blocked_reason=(result.get('eligibility') or {}).get('message') if not result.get('kinds') else None,
                transit=bool((result.get('eligibility') or {}).get('transit')),
                return_required_by_kind={kind:kind in ('return_refund','exchange','repair') for kind in result.get('kinds',{})},
                requires_seller_verification=memory.get('reason') in ('quality','wrong_item','missing'))
        if 'freight' in focus:
            quote=result.get('freight') or {}
            payer='buyer' if 'due' in quote else 'shop_after_verification' if 'advance' in quote else 'not_determined'
            add('return_freight',checked=bool(quote),payer=payer,quoted_postage=f"{item['return_postage']:.2f}",
                buyer_due=str(quote['due']) if 'due' in quote else None,
                credit=str(quote.get('credit',0)) if 'due' in quote else None,
                buyer_advance=str(quote['advance']) if 'advance' in quote else None,
                reimbursement_condition='seller_verifies_issue' if 'advance' in quote else None,
                return_needed=bool(quote),applies_to='return_shipping_only',reason=memory.get('reason'))
        if 'original_shipping' in focus:
            add('original_shipping',recorded=item.get('shipping') is not None,
                amount=str(item['shipping']) if item.get('shipping') is not None else None,refund_of_shipping_checked=False)
        if 'photos' in focus:
            add('evidence_requirements',photos_required=False,video_required=False,max_photos=3,
                photo_mime=['image/jpeg','image/png'],video_upload_supported=False,
                checklist=EVIDENCE_CHECKLIST.get(memory.get('reason'),['商品或包装实际状态，面单隐去手机号和地址']),
                reported_materials=memory.get('evidence',[]),reported_does_not_mean_uploaded=True,
                seller_may_request_supplement=True)
        if selected and focus & {'progress','refund','offer','return','outbound','platform','next_step','amount_followup','freight'}:
            add('case',case_no=selected['case_no'],status=selected['status'],status_label=selected['status_label'],
                owner=selected.get('owner'),due_at=selected.get('due_at'),overdue=selected.get('overdue',False),
                quantity=selected.get('quantity'),kind=selected.get('kind'),buyer_actions=selected.get('actions',[]),
                accepted_offer=selected.get('agreement'),pending_offer=(selected.get('prompt') or {}).get('offer'))
        if selected and focus & {'refund','freight','original_shipping','amount_followup'}:
            add('case_finance',refund=selected.get('refund_summary'),recorded_refund=selected.get('recorded_refund'),
                refund_attempt=selected.get('refund_attempt'),return_reimbursement_due=selected.get('return_reimbursement_due'),
                bank_arrival_verified=False)
        if selected and 'return' in focus:
            add('return_parcel',case_no=selected['case_no'],status=selected['status'],details=selected.get('return_details'),
                details_pending=selected.get('return_details_pending'),carrier_returned=selected.get('carrier_returned'),
                transit_request=selected.get('transit_request'),interception_failed=selected.get('interception_failed'),
                due_at=selected.get('due_at'))
            pending=bool(selected.get('return_details_pending'))
            can_ship='ship' in selected.get('actions',[])
            add('handling_form',case_no=selected['case_no'],form='return_tracking',
                waiting_for='shop_return_address' if pending else 'buyer_tracking' if can_ship else 'current_case_step',
                required_fields=['carrier','tracking'] if can_ship else [],submit_allowed=can_ship,
                must_use_current_case=True,chat_submission_supported=True)
        if selected and 'outbound' in focus:
            parcel=selected.get('outbound')
            add('outbound_parcel',parcel={key:parcel.get(key) for key in ('parcel_no','stage','carrier','tracking','shipped_at','received_at','problem')} if parcel else None,
                awaiting_address_confirmation=bool(selected.get('outbound_pending') and not parcel),review=selected.get('delivery_review'),
                reply=selected.get('delivery_reply'))
            if selected.get('outbound_pending') and not parcel:
                add('handling_form',case_no=selected['case_no'],form='delivery_address',
                    required_fields=['recipient','address','phone'],waiting_for='buyer_confirmation',
                    must_use_current_case=True,chat_submission_supported=True)
        if 'platform' in focus:
            add('platform_intervention',eligibility=result.get('intervention'),shop_application_required=selected is None)
        if focus & {'policy_menu','return_window','warranty','response_time','shop_guidance'}:
            keys={'return_window':'window','warranty':'service','response_time':'response','shop_guidance':'guidance'}
            clauses=policy_clauses(item)
            requested=set(clauses) if 'policy_menu' in focus else {keys[topic] for topic in focus if topic in keys}
            add('purchase_policy',version=item['terms_version'],title=item['terms_title'],
                clauses={key:clauses[key] for key in requested if key in clauses})
        if focus & {'shop_current','platform_floor'} or memory.get('policy_reference') and 'basis' in focus:
            add('current_policy',policy=item.get('current_policy'),purchase_version=item['terms_version'],
                version_reference=memory.get('policy_reference'))
        if result.get('evidence_requested'):
            add('policy_evidence',quotes=result.get('evidence',[]),policy_title=item['terms_title'],
                purchase_version=item['terms_version'],match_found=bool(result.get('evidence')),
                coverage=(result.get('retrieval') or {}).get('coverage'),
                aspects=(result.get('retrieval') or {}).get('aspects',[]))
    if (memory.get('issues') or memory.get('issue_description') or memory.get('requested_kind') or memory.get('last_action')) and focus-{'logistics','product','out_of_scope'}:
        add('customer_task',issue_state=memory.get('issue_state'),reason=memory.get('reason'),
            issues=[{**row,'reason_label':REASONS.get(row['reason'])} for row in memory.get('issues',[])],description=memory.get('issue_description'),
            requested_kind=memory.get('requested_kind'),requested_quantity=memory.get('quantity'),
            quantity_mode=memory.get('quantity_mode'),processing_paused=memory.get('processing_paused',False),
            target_spec=memory.get('target_spec'),evidence=memory.get('evidence',[]),
            target_spec_availability_verified=False,target_spec_requires_shop_confirmation=True,
            last_confirmed_action=memory.get('last_action'),desired_is_not_executed=True)
    if memory.get('evidence') or 'photos' in focus or result.get('action_draft'):
        add('submission_channels',photo_mime=['image/jpeg','image/png'],max_photos=3,video_upload_supported=False,
            reported_material_can_be_described_in_note=True)
    if result.get('action_draft') and result.get('action_draft_relevant',True):
        if memory.get('fulfillment') and selected and str(memory.get('fulfillment_case'))==str(selected['case_no']):
            add('fulfillment_draft',fields=memory['fulfillment'],case_no=selected['case_no'],submitted=False)
        preview=result['action_draft']
        visible=('state','action','label','case_no','title','variant','kind','kind_label','reason_label','quantity',
                 'note','max_product_refund','payload','offer','photos_allowed','missing','available_kinds','remaining_quantity','requested_quantity','message')
        add('action_preview',preview={key:preview[key] for key in visible if key in preview},executed=False,explicit_confirmation_required=True)
    if focus & {'eligibility','fit_exchange','next_step','offer','platform'}:
        preview=result.get('action_draft') or {}
        state=('paused' if memory.get('processing_paused') else 'existing_case' if selected and selected.get('is_active')
               else preview.get('state','not_prepared'))
        add('task_progress',state=state,missing=preview.get('missing',[]),
            pending_action=preview.get('action'),case_no=selected.get('case_no') if selected else None,
            next_owner=selected.get('owner') if selected else 'buyer',
            preserved={'quantity':memory.get('quantity'),'kind':memory.get('requested_kind'),
                       'target_spec':memory.get('target_spec')},
            execution_requires_confirmation=True)
    return {f'F{index}':fact for index,fact in enumerate(facts,1)}
