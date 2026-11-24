"""准备可核对的聊天办理卡；执行权只在后端确认接口。"""

import hashlib
import json
from uuid import uuid4
from pydantic import ValidationError

from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator
from typing import Annotated

from app.mall.dialogue_state import task_signature
from app.mall.photos import MAX_PHOTOS, PhotoInput
from app.mall.rules import KINDS, REASONS, assess_eligibility, ReturnDetails, MallOperation
from app.mall.offers import refundable


LABELS={'submit':'提交售后申请','accept_offer':'接受店铺方案','reject_offer':'拒绝店铺方案',
        'ship':'登记寄回运单','confirm_delivery_address':'确认售后收货信息',
        'dispute':'申请平台介入','withdraw':'撤回售后申请','revise':'重新提交原申请',
        'evidence':'补充说明与凭证','provide_evidence':'回复店铺补证要求','close_prompt':'关闭当前待办',
        'confirm_delivery':'确认收到售后包裹'}


class ChatConfirmation(BaseModel):
    """用户可修改说明或附照片，不能在确认请求里替换商品、金额或动作。"""
    model_config=ConfigDict(extra='forbid')
    turn_no: Annotated[int,Field(strict=True,gt=0,le=2**63-1)] | Annotated[str,StringConstraints(pattern=r'^[1-9][0-9]{0,18}$')]
    note: Annotated[str,StringConstraints(strip_whitespace=True,min_length=1,max_length=500)] | None=None
    photos:list[PhotoInput]=Field(default_factory=list,max_length=MAX_PHOTOS)

    @field_validator('turn_no')
    @classmethod
    def exact_turn_number(cls,value):
        number=int(value)
        if number>2**63-1:
            raise ValueError('轮次编号超出范围')
        return number


def case_signature(case: dict) -> str:
    fields=('case_no','status','updated_at','quantity','kind','prompt','agreement','outbound','actions',
            'delivery_review','refund_attempt','return_details_pending')
    body=jsonable_encoder({key:case.get(key) for key in fields})
    return hashlib.sha256(json.dumps(body,ensure_ascii=False,sort_keys=True).encode()).hexdigest()


def prepare_action(item: dict, memory: dict, cases: list[dict], today, *, requested: bool) -> dict | None:
    """资格、方案版本、件数和退款上限均由现有业务数据计算。"""
    if not requested or memory.get('processing_paused') or not item.get('order_id') or not item.get('item_no'):
        return None
    case=next((row for row in cases if row.get('is_active')),cases[0] if cases else None)
    action=memory.get('case_action')
    signature=task_signature(memory)
    if action:
        if case is None:
            return {'state':'unavailable','action':action,'missing':['existing_case'],
                    'message':'当前商品还没有原售后单，需要先向店铺提交申请。'}
        allowed=case.get('actions',[])
        if action=='provide_evidence' and action not in allowed and 'evidence' in allowed:
            action='evidence'
        if action not in allowed:
            return {'state':'unavailable','action':action,'case_no':case['case_no'],'allowed_actions':allowed,
                    'message':'原售后单当前阶段不允许这项操作。'}
        payload={}
        if action in ('ship','confirm_delivery_address'):
            values=memory.get('fulfillment',{}) if str(memory.get('fulfillment_case'))==str(case['case_no']) else {}
            required=('carrier','tracking') if action=='ship' else ('recipient','address','phone')
            missing=[key for key in required if not values.get(key)]
            if missing:return {'state':'needs_input','action':action,'missing':missing,'case_no':case['case_no']}
            try:
                if action=='ship':
                    validated=MallOperation(operation_id=uuid4(),action='ship',note='登记寄回运单',carrier=values['carrier'],tracking=values['tracking'])
                    payload.update(carrier=validated.carrier,tracking=validated.tracking)
                else:
                    payload['delivery_address']=ReturnDetails(**{key:values[key] for key in required}).model_dump()
                    parcel=case.get('outbound') or {}
                    payload.update(delivery_parcel_no=parcel.get('parcel_no'),delivery_address_version=parcel.get('address_version'))
            except ValidationError as error:
                return {'state':'needs_input','action':action,'missing':[str(row['loc'][-1]) for row in error.errors()],
                        'case_no':case['case_no'],'message':'请核对填写的资料格式。'}
        if action in ('accept_offer','reject_offer','provide_evidence','close_prompt'):
            payload['prompt_id']=(case.get('prompt') or {}).get('id')
        if action=='confirm_delivery':
            payload['delivery_parcel_no']=(case.get('outbound') or {}).get('parcel_no')
        offer=(case.get('prompt') or {}).get('offer') if action in ('accept_offer','reject_offer') else None
        return {'id':str(uuid4()),'state':'ready','action':action,'label':LABELS[action],
                'case_no':case['case_no'],'case_signature':case_signature(case),'memory_signature':signature,
                'memory_revision':memory.get('memory_revision',0),
                'order_id':item['order_id'],'item_no':item['item_no'],'title':item['title'],'variant':item['variant'],
                'quantity':offer['quantity'] if offer else case['quantity'],'kind':offer['kind'] if offer else case['kind'],'note':memory.get('action_note') or LABELS[action],
                'offer':offer,
                'payload':jsonable_encoder(payload),'photos_allowed':action in ('evidence','provide_evidence','dispute','revise'),
                'confirmation_required':True}
    if case and case.get('is_active'):
        return None
    reason=memory.get('reason')
    if reason not in REASONS:
        return {'state':'needs_input','missing':['reason']}
    eligibility=assess_eligibility(item,reason,today)
    kind=memory.get('requested_kind')
    if kind is None and len(eligibility.kinds)==1:
        kind=eligibility.kinds[0]
    if not eligibility.kinds:
        return {'state':'unavailable','missing':[],'message':eligibility.message}
    if kind not in eligibility.kinds:
        return {'state':'needs_input','missing':['kind'],'available_kinds':eligibility.choices}
    remaining=item.get('remaining_quantity',item['quantity'])
    quantity=remaining if memory.get('quantity_mode')=='all_remaining' else memory.get('quantity') or (1 if remaining==1 else None)
    if quantity is None:
        return {'state':'needs_input','missing':['quantity'],'remaining_quantity':remaining,'kind':kind}
    if quantity>remaining:
        return {'state':'needs_input','missing':['quantity'],'remaining_quantity':remaining,'requested_quantity':quantity,
                'message':'申请件数超过当前剩余可申请数量。'}
    descriptions=list(dict.fromkeys(row['quote'] for row in memory.get('issues',[]) if row['state']=='active'))
    descriptions.extend(row['quote'] for row in memory.get('evidence',[]) if row['quote'] not in descriptions)
    description='；'.join(descriptions) or memory.get('issue_description','')
    if memory.get('target_spec'):
        description+='；希望换成'+memory['target_spec']
    if not description:
        return {'state':'needs_input','missing':['description']}
    return {'id':str(uuid4()),'state':'ready','action':'submit','label':LABELS['submit'],
            'memory_signature':signature,'memory_revision':memory.get('memory_revision',0),'order_id':item['order_id'],'item_no':item['item_no'],
            'title':item['title'],'variant':item['variant'],'kind':kind,'kind_label':eligibility.choices[kind],
            'reason':reason,'reason_label':REASONS[reason],'quantity':quantity,'note':description[:500],
            'max_product_refund':format(refundable(item,quantity),'.2f') if kind in ('refund_only','return_refund') else None,
            'terms_version':item['terms_version'],'photos_allowed':True,'confirmation_required':True}
