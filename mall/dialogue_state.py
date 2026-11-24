"""维护商品会话的事实、纠正记录和有限长度历史，兼容已有对话记录。"""

from copy import deepcopy
import hashlib
import json
import re

from app.mall.rules import KINDS, REASONS


MEMORY_KEYS = ("reason", "issue_description", "requested_kind", "target_spec", "rejected_reasons",
               "asked_facts", "focus", "arrival_damage", "preference_report", "issue_report", "issue_state", "policy_reference",
               "issues", "quantity", "quantity_quote", "quantity_mode", "evidence", "case_action", "action_note", "last_action", "memory_revision", "processing_paused", "fulfillment", "fulfillment_case")

CASE_ACTIONS = ("ship", "confirm_delivery_address", "accept_offer", "reject_offer", "dispute", "withdraw", "revise", "evidence", "provide_evidence", "close_prompt", "confirm_delivery")
EVIDENCE_KINDS = ("unboxing_video", "photos", "packaging", "shipping_label", "invoice", "other")


def task_signature(memory: dict) -> str:
    """只比较会改变办理内容的状态；查物流等插问不使确认卡失效。"""
    fields = ("reason", "issue_description", "requested_kind", "target_spec", "quantity", "quantity_mode", "issues", "evidence", "case_action", "action_note", "processing_paused", "fulfillment", "fulfillment_case")
    return hashlib.sha256(json.dumps({key:memory.get(key) for key in fields},ensure_ascii=False,sort_keys=True).encode()).hexdigest()


def validate_observation(value: dict) -> None:
    """新增参数同样要经过结构校验，模型不能自建权限或把凭证说成已上传。"""
    allowed = {"quote","issue_quote","issue_state","reason","requested_kind","target_spec","retract",
               "quantity","issues","evidence","case_action","action_note_quote","cancel_action","fulfillment"}
    if not isinstance(value,dict) or set(value)-allowed or not isinstance(value.get('quote'),str) or not 1 <= len(value['quote']) <= 500:
        raise ValueError('事实引用格式错误')
    for key in ('issue_quote','target_spec','action_note_quote'):
        if key in value and (not isinstance(value[key],str) or not 1 <= len(value[key]) <= 500):
            raise ValueError('事实文字格式错误')
    for key, choices in (('reason',REASONS),('requested_kind',KINDS),('issue_state',('active','resolved','packaging_only','unclear')),('case_action',CASE_ACTIONS)):
        if key in value and (not isinstance(value[key],str) or value[key] not in choices):
            raise ValueError('事实类型错误')
    for key in ('retract','cancel_action'):
        if key in value and type(value[key]) is not bool:
            raise ValueError('更正参数格式错误')
    if 'quantity' in value:
        row=value['quantity']
        if (not isinstance(row,dict) or set(row)-{'value','quote','mode'} or not isinstance(row.get('quote'),str) or len(row['quote'])>100
                or row.get('mode','explicit') not in ('explicit','all_remaining')
                or row.get('mode','explicit')=='explicit' and (type(row.get('value')) is not int or not 1 <= row['value'] <= 100000)):
            raise ValueError('件数参数格式错误')
    if 'fulfillment' in value:
        fields=value['fulfillment']
        if not isinstance(fields,dict) or set(fields)-{'carrier','tracking','recipient','address','phone'}:
            raise ValueError('履约字段错误')
        for field in fields.values():
            if (not isinstance(field,dict) or set(field)!={'value','quote'} or not all(isinstance(v,str) and 1<=len(v)<=240 for v in field.values())
                    or field['value'] not in field['quote']):
                raise ValueError('履约字段必须有原话依据')
    for key in ('issues','evidence'):
        rows=value.get(key,[])
        if not isinstance(rows,list) or len(rows)>6:
            raise ValueError('事实列表过长')
        for row in rows:
            expected={'id','reason','quote','state'} if key=='issues' else {'kind','quote','state'}
            required=({'quote','state'} if row.get('id') else {'reason','quote','state'}) if key=='issues' and isinstance(row,dict) else expected
            if not isinstance(row,dict) or set(row)-expected or required-set(row) or not isinstance(row['quote'],str) or not 1 <= len(row['quote']) <= 500:
                raise ValueError('事实列表格式错误')
            if key=='issues' and ('reason' in row and (not isinstance(row['reason'],str) or row['reason'] not in REASONS) or row['state'] not in ('active','resolved') or 'id' in row and not re.fullmatch(r'issue-[1-9][0-9]{0,5}',str(row['id']))):
                raise ValueError('问题事实格式错误')
            if key=='evidence' and (row['kind'] not in EVIDENCE_KINDS or row['state'] not in ('reported_available','reported_missing')):
                raise ValueError('凭证事实格式错误')


def _literal_quantity(quote: str, value: int) -> bool:
    """数值必须与原话里的件数一致，不把商品购买数量当成申请数量。"""
    digits={'一':1,'二':2,'两':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9,'十':10}
    def number(text):
        if text.isascii():
            return int(text)
        if '十' in text:
            left,right=text.split('十',1)
            return digits.get(left,1)*10+digits.get(right,0)
        return digits.get(text)
    return any(number(found)==value for found in re.findall(r'([0-9]+|[一二两三四五六七八九十]+)\s*(?:件|个|只|条|套|份|根)',quote))


def _extend_task(previous: dict, result: dict, observation: dict, question: str, available: list[str]) -> None:
    """以事件记录问题生命周期；旧问题、办理意愿和凭证分别更新。"""
    supported=lambda quote: bool(quote and any(quote in text for text in available))
    issues=deepcopy(previous.get('issues',[]))
    if not issues and previous.get('reason') in REASONS and previous.get('issue_description'):
        issues=[{'id':'issue-1','reason':previous['reason'],'quote':previous['issue_description'],'state':'active'}]
    journal=list(previous.get('issue_events',[]))
    if (observation.get('retract') and not observation.get('issues') or observation.get('issue_state') in ('resolved','packaging_only') and not result.get('reason')):
        for issue in issues:
            if issue['state']=='active':
                issue['state']='resolved'
                journal.append({'issue':issue['id'],'state':'resolved','quote':observation['quote']})
    updates=observation.get('issues',[])
    if not updates and result.get('reason') in REASONS:
        quote=result.get('issue_description')
        if quote and supported(quote):
            updates=[{'reason':result['reason'],'quote':quote,'state':'active'}]
    touched=set()
    for update in updates:
        if not supported(update['quote']) and not any(row['id']==update.get('id') and row['quote']==update['quote'] for row in issues):
            continue
        match=next((row for row in issues if row['id']==update.get('id')),None)
        if update.get('id') and (match is None or 'reason' in update and match['reason']!=update['reason']):
            continue
        update={**update,'reason':update.get('reason') or match['reason']}
        if match is None:
            match=next((row for row in issues if row['reason']==update['reason'] and row['quote']==update['quote'] and row['id'] not in touched),None)
        if match is None:
            match=next((row for row in issues if row['reason']==update['reason'] and row['id'] not in touched),None)
        if match and update['quote'] not in question and match['state']!=update['state'] and observation['quote'] not in question:
            continue
        if match is None:
            match={'id':f"issue-{max([int(row['id'].split('-')[1]) for row in issues] or [0])+1}"}
            issues.append(match)
        touched.add(match['id'])
        changed=any(match.get(key)!=update[key] for key in ('reason','quote','state'))
        match.update({key:update[key] for key in ('reason','quote','state')})
        if changed:
            journal.append({'issue':match['id'],'state':match['state'],'quote':observation['quote'] if observation['quote'] in question else update['quote']})
    result['issues']=issues[-6:]
    result['issue_events']=journal[-32:]
    active=[row for row in result['issues'] if row['state']=='active']
    if observation.get('issues'):
        primary=next((row for row in active if row['reason']==result.get('reason')),active[0] if active else None)
        if primary:
            result.update(reason=primary['reason'],issue_description=primary['quote'],issue_report=primary['quote'],issue_state='active')
        else:
            for field in ('reason','issue_description','issue_report','requested_kind','target_spec'):
                result.pop(field,None)
    quantity=observation.get('quantity')
    if quantity and supported(quantity['quote']) and quantity.get('mode')=='all_remaining' and quantity['quote'] in question and re.search(r'全部|全都|都退|都换|都补|剩下的都',quantity['quote']):
        result.update(quantity_mode='all_remaining',quantity_quote=quantity['quote'])
        result.pop('quantity',None)
    elif quantity and supported(quantity['quote']) and _literal_quantity(quantity['quote'],quantity.get('value')):
        if quantity['quote'] in question or not previous.get('quantity') or previous['quantity']==quantity['value']:
            result.update(quantity=quantity['value'],quantity_quote=quantity['quote'],quantity_mode='explicit')
    if result.get('reason')!=previous.get('reason'):
        result.pop('quantity',None)
        result.pop('quantity_quote',None)
        result.pop('quantity_mode',None)
        if quantity and quantity['quote'] in question and _literal_quantity(quantity['quote'],quantity.get('value')):
            result.update(quantity=quantity['value'],quantity_quote=quantity['quote'],quantity_mode='explicit')
        elif quantity and quantity.get('mode')=='all_remaining' and quantity['quote'] in question and re.search(r'全部|全都|都退|都换|都补|剩下的都',quantity['quote']):
            result.update(quantity_mode='all_remaining',quantity_quote=quantity['quote'])
    evidence={row['kind']:deepcopy(row) for row in previous.get('evidence',[])}
    for row in observation.get('evidence',[]):
        if supported(row['quote']) and (row['quote'] in question or row['kind'] not in evidence or evidence[row['kind']]['state']==row['state']):
            evidence[row['kind']]=dict(row)
    result['evidence']=list(evidence.values())
    if observation.get('case_action') and observation['quote'] in question:
        result['processing_paused']=False
        result['case_action']=observation['case_action']
        note=observation.get('action_note_quote',observation['quote'])
        if note in question:
            result['action_note']=note
    fulfillment=deepcopy(previous.get('fulfillment',{}))
    for key,field in observation.get('fulfillment',{}).items():
        if field['quote'] in question and field['value'] in field['quote']:
            # 单号前缀不是用户明确提供的快递公司；模型不能用SF等前缀猜承运商。
            tracking=(observation.get('fulfillment',{}).get('tracking') or {}).get('value') or fulfillment.get('tracking','')
            if key=='carrier' and any(field['value'] in candidate for candidate in [tracking,*re.findall(r'[A-Za-z0-9]{5,}',question)]) and not re.search(
                    r'(?:快递公司|承运商|物流公司|用的是|走的是|通过).{0,6}'+re.escape(field['value']),question):
                continue
            fulfillment[key]=field['value']
    if fulfillment:
        result['fulfillment']=fulfillment
    if observation.get('cancel_action') and observation['quote'] in question:
        result['processing_paused']=True
        for field in ('requested_kind','case_action','action_note','target_spec'):
            result.pop(field,None)
    elif observation.get('requested_kind') and supported(observation['quote']):
        result['processing_paused']=False
    if task_signature(result)!=task_signature(previous):
        result['memory_revision']=previous.get('memory_revision',0)+1
    result['schema_version']=3


def snapshot(context: dict, recent: list[dict], item: dict, version: int | None) -> dict:
    """从可信数据库生成模型可见的商品摘要，仅携带买家提供的履约草稿，不读取订单原地址或账号口令。"""
    memory = {key: deepcopy(context[key]) for key in MEMORY_KEYS if context.get(key)}
    memory["recent"] = [
        {"user": str(row["question"])[:500], "assistant": str(row["result"].get("answer", ""))[:600]}
        for row in reversed(recent[:6])
    ]
    memory["product"] = {key: str(item[key]) for key in ("title", "variant", "stage", "terms_version") if key in item}
    if recent and recent[0]["result"].get("order_candidates"):
        memory["candidates"] = [{key:str(row[key]) for key in ("order_id","title","variant","paid_unit","purchased") if key in row}
                                for row in recent[0]["result"]["order_candidates"][:6]]
    memory["version"] = version
    return memory


def apply_observation(context: dict, observation: dict | None, question: str, recent: list[dict]) -> tuple[dict, bool]:
    """模型提供事实更新，描述必须来自买家原话，并记录每次纠正的依据。"""
    result = deepcopy(context)
    if not observation:
        return result, False
    quote = observation.get("quote", "").strip()
    available = [question, *[str(row["question"]) for row in recent[:6]]]
    supported = bool(quote and any(quote in text for text in available))
    reason = observation.get("reason")
    issue_state = observation.get("issue_state")
    if issue_state=='unclear':
        # 模型同时给出待澄清状态和猜测原因时，只保存不确定性，不把猜测变成事实。
        reason=None
        observation={key:value for key,value in observation.items() if key not in ('reason','issues')}
    if reason == "wrong_purchase" and observation.get("requested_kind") in KINDS:
        # 原缺件/故障解决后，个人退换意愿仍是一个新的有效事项。
        issue_state = "active"
    resolved = issue_state == "resolved" or (issue_state == "packaging_only" and reason != "wrong_purchase")
    if resolved:
        reason = None
    if reason in context.get("rejected_reasons", []) and quote not in question:
        supported = False
    changed = resolved or observation.get("retract", False) or (reason in REASONS and reason != result.get("reason"))
    if (observation.get("retract") or resolved) and supported:
        if result.get("reason"):
            result["rejected_reasons"] = list(dict.fromkeys([*result.get("rejected_reasons", []), result["reason"]]))
        for key in ("reason", "issue_description", "issue_report", "asked_facts", "preference_report", "arrival_damage", "requested_kind", "target_spec"):
            result.pop(key, None)
    if reason in REASONS and supported:
        if changed:
            for field in ("target_spec", "requested_kind", "arrival_damage", "preference_report", "missing_description", "missing_guide"):
                result.pop(field, None)
        result["reason"] = reason
        issue_quote = observation.get("issue_quote", "")
        if issue_quote and any(issue_quote in text for text in available):
            result["issue_description"] = issue_quote
        elif changed or not result.get("issue_description"):
            result["issue_description"] = quote
        result["issue_report"] = result.get("issue_description", quote)
        if changed:
            result.pop("asked_facts", None)
    kind = observation.get("requested_kind")
    if kind in KINDS and supported and not resolved:
        result["requested_kind"] = kind
    target = observation.get("target_spec")
    if target and target in question:
        result["target_spec"] = target
    if supported:
        if issue_state:
            result["issue_state"] = issue_state
        events = result.get("fact_events", [])
        result["fact_events"] = [*events[-7:], {"quote": quote, "reason": result.get("reason"),
                                               "requested_kind": result.get("requested_kind"),
                                               "retracted": bool(observation.get("retract"))}]
        _extend_task(context,result,observation,question,available)
    return result, bool(changed and supported)


def merge_fallback(previous: dict, updated: dict, result: dict) -> dict:
    """模型不可用时保留已确认记忆；规则识别到纠正后清除关联草稿。"""
    memory = deepcopy(previous)
    if result.get("reset_suggestion") or ("reason" in updated and updated["reason"] != previous.get("reason")):
        for key in ("reason", "issue_description", "issue_report", "requested_kind", "target_spec", "arrival_damage", "preference_report"):
            memory.pop(key, None)
    memory.update(updated)
    return memory


def observation_schema() -> dict:
    """约束模型提取的业务意愿及其原文依据。"""
    return {"type": "object", "additionalProperties": False, "required": ["quote"],
            "properties": {
                "fulfillment":{"type":"object","additionalProperties":False,"properties":{
                    key:{"type":"object","additionalProperties":False,"required":["value","quote"],"properties":{
                    "value":{"type":"string"},"quote":{"type":"string","description":"逐字引用本轮给出该字段的原话，value必须是原话子串"}}}
                    for key in ('carrier','tracking','recipient','address','phone')}},
                "quote": {"type": "string", "description": "逐字引用当前或历史买家消息中的问题描述，最多500字"},
                "issue_quote": {"type": "string", "description": "实际故障、缺件或不合适的原话；只问物流费用不是问题描述，不提供此字段"},
                "issue_state": {"type": "string", "enum": ["active", "resolved", "packaging_only", "unclear"],
                                "description": "商品问题仍存在、已排除、仅包装异常而商品完好、尚未查明"},
                "reason": {"type": "string", "enum": list(REASONS),"description":"not_received为未拿到原包裹；拆箱后空箱或少商品/配件是missing；具体表现未知时不填原因"},
                "requested_kind": {"type": "string", "enum": list(KINDS)},
                "target_spec": {"type": "string", "description": "逐字引用本轮买家想换的规格"},
                "retract": {"type": "boolean", "description": "买家明确纠正或撤回之前的问题"},
                "quantity":{"type":"object","additionalProperties":False,"required":["quote"],"properties":{
                    "value":{"type":"integer","minimum":1,"maximum":100000},"mode":{"type":"string","enum":["explicit","all_remaining"],"description":"明确数字或明确全部处理当前商品；all_remaining由服务器计算"},"quote":{"type":"string","description":"逐字引用申请数量，如退一件或全部退；不是购买总件数"}}},
                "issues":{"type":"array","maxItems":6,"items":{"type":"object","additionalProperties":False,"required":["quote","state"],"properties":{
                    "id":{"type":"string","description":"修改已知问题时沿用其issue编号"},"reason":{"type":"string","enum":list(REASONS)},
                    "quote":{"type":"string","description":"具体问题或已排除事实的买家原话"},"state":{"type":"string","enum":["active","resolved"]}}}},
                "evidence":{"type":"array","maxItems":6,"items":{"type":"object","additionalProperties":False,"required":["kind","state","quote"],"properties":{
                    "kind":{"type":"string","enum":list(EVIDENCE_KINDS)},"state":{"type":"string","enum":["reported_available","reported_missing"]},"quote":{"type":"string"}}}},
                "case_action":{"type":"string","enum":list(CASE_ACTIONS),"description":"仅用户明确想办理原售后单动作时填写，查询能力或假设不算办理意愿"},
                "action_note_quote":{"type":"string","description":"逐字引用本轮给店铺或平台的说明"},
                "cancel_action":{"type":"boolean","description":"暂不办理或取消当前办理意愿；不表示商品恢复正常，也不撤回真实售后单"},
            }}
