"""把聊天确认卡接到原业务事务，确认、审计与幂等结果一起提交。"""

from datetime import datetime
import hashlib
import json
from uuid import UUID
from zoneinfo import ZoneInfo

from psycopg.types.json import Jsonb

from app.access import Account
from app.mall.chat_actions import ChatConfirmation, case_signature, prepare_action
from app.mall.dialogue_state import task_signature
from app.mall.rules import MallApplication, MallOperation
from app.tickets.service_requests import ServiceError

CHINA=ZoneInfo('Asia/Shanghai')


def _confirmation_hash(form: ChatConfirmation, note: str) -> str:
    body={'note':note,'photos':[row.model_dump(mode='json') for row in form.photos]}
    return hashlib.sha256(json.dumps(body,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def confirm(store, buyer: str, order_id: str, item_no: int, draft_id: UUID, form: ChatConfirmation) -> dict:
    """卡片不能修改动作/金额/商品；状态改变或客户改口后必须重新准备。"""
    account=Account(buyer,'buyer')
    key=(buyer,order_id,item_no,form.turn_no)
    query='''SELECT turn_no,result,context FROM public.mall_consultations
             WHERE buyer_id=%s AND order_id=%s AND item_no=%s AND turn_no=%s'''
    with store.connect() as conn:
        source=conn.execute(query,key).fetchone()
        if source is None:
            raise ServiceError(404,'查不到这张办理确认卡。')
        draft=source['result'].get('action_draft') or {}
        if draft.get('id')!=str(draft_id) or draft.get('state')!='ready':
            raise ServiceError(409,'确认卡已变化，请重新核对。')
        note=form.note or draft['note']
        fingerprint=_confirmation_hash(form,note)
        def previous_receipt(row):
            receipt=row['result'].get('action_execution')
            if receipt and row['result'].get('confirmation_hash')!=fingerprint:
                raise ServiceError(409,'这张卡已经确认，修改内容需要重新准备。')
            return receipt
        receipt=previous_receipt(source)
        if receipt:
            return receipt
        # 与现有原单操作采用相同的锁顺序：原单在先，包裹在后。
        view=None
        if draft['action']!='submit':
            row=store._case(conn,account,int(draft['case_no']),lock=True)
            if row['order_id']!=order_id:
                raise ServiceError(409,'原售后单与确认卡不一致。')
            view=store._display(row,account)
            view['refund_summary']=None
        package=conn.execute('''SELECT p.package_no FROM public.mall_packages p JOIN public.mall_orders o USING(order_id)
            JOIN public.mall_items i USING(order_id,package_no)
            WHERE p.order_id=%s AND o.buyer_id=%s AND i.item_no=%s FOR NO KEY UPDATE OF p''',
            (order_id,buyer,item_no)).fetchone()
        if package is None:
            raise ServiceError(404,'查不到这件订单商品。')
        source=conn.execute(query+' FOR UPDATE',key).fetchone()
        receipt=previous_receipt(source)
        if receipt:
            return receipt
        latest=conn.execute('''SELECT turn_no,context FROM public.mall_consultations
            WHERE buyer_id=%s AND order_id=%s AND item_no=%s ORDER BY turn_no DESC LIMIT 1''',
            (buyer,order_id,item_no)).fetchone()
        memory=latest['context']
        if task_signature(memory)!=draft['memory_signature'] or memory.get('memory_revision',0)!=draft.get('memory_revision',0):
            raise ServiceError(409,'你已更新问题或办理意愿，请核对最新确认卡。')
        item=store._items(conn,buyer,order_id,item_no)[0]
        cases=[view] if view is not None else store._page(conn,account,order_id=order_id,item_no=item_no,exact=True)['cases']
        current=prepare_action(item,memory,cases,datetime.now(CHINA).date(),requested=True)
        fields=('action','case_no','order_id','item_no','kind','reason','quantity','terms_version','max_product_refund','payload')
        if current is None or current.get('state')!='ready' or any(current.get(key)!=draft.get(key) for key in fields):
            raise ServiceError(409,'原单、资格或方案已变化，请重新核对办理内容。')
        if view is not None and case_signature(view)!=draft['case_signature']:
            raise ServiceError(409,'店铺已更新原单，请核对最新方案后再确认。')
        if form.photos and not draft['photos_allowed']:
            raise ServiceError(400,'这项操作无需附照片。')
        if draft['action']=='submit':
            application=MallApplication(submission_id=draft_id,item_no=item_no,quantity=draft['quantity'],
                kind=draft['kind'],reason=draft['reason'],description=note,photos=form.photos)
            saved=store.submit(account,order_id,application,connection=conn)
        else:
            operation=MallOperation(operation_id=draft_id,action=draft['action'],note=note,photos=form.photos,
                                    **draft.get('payload',{}))
            saved=store.operate(account,int(draft['case_no']),operation,connection=conn)
        receipt={'draft_id':str(draft_id),'operation_id':str(draft_id),'case_no':str(saved['case_no']),
                 'order_id':order_id,'item_no':item_no,'action':draft['action'],'label':draft['label'],
                 'status':saved['status'],'status_label':saved['status_label'],
                 'is_active':bool(saved['is_active'] or saved.get('outbound_pending')),
                 'confirmed_at':datetime.now(CHINA).isoformat()}
        conn.execute('UPDATE public.mall_consultations SET result=result || %s WHERE turn_no=%s',
            (Jsonb({'action_execution':receipt,'confirmation_hash':fingerprint,
                    'case_no':receipt['case_no'],'kinds':{},'eligibility':None}),form.turn_no))
        conn.execute('''UPDATE public.mall_consultations
            SET result=jsonb_set(result,'{action_draft,state}','"outdated"'::jsonb)
            WHERE buyer_id=%s AND order_id=%s AND item_no=%s AND turn_no<>%s
              AND result->'action_draft'->>'state'='ready' AND NOT result ? 'action_execution' ''',key)
        memory=dict(memory,last_action=receipt,memory_revision=memory.get('memory_revision',0)+1)
        memory.pop('case_action',None)
        memory.pop('action_note',None)
        if draft['action'] in ('ship','confirm_delivery_address'):
            memory.pop('fulfillment',None)
            memory.pop('fulfillment_case',None)
        if draft['action']=='withdraw':
            for field in ('requested_kind','quantity','quantity_quote','target_spec'):
                memory.pop(field,None)
        conn.execute('UPDATE public.mall_consultations SET context=%s,result=result || %s WHERE turn_no=%s',
            (Jsonb(memory),Jsonb({'task_signature':task_signature(memory),'task_revision':memory['memory_revision'],'current_case':receipt}),latest['turn_no']))
        return receipt
