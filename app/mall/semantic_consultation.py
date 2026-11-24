"""把模型理解转换为已有业务查询，保存可解释的商品会话状态。"""

from datetime import date

from app.mall.consultation import _answer_order, _followup_reply, _model_clarification, answer
from app.mall.dialogue_state import apply_observation, task_signature
from app.mall.tool_facts import build_facts
from app.mall.chat_actions import prepare_action
from app.mall.rules import KINDS, REASONS, assess_eligibility


def _execute(item: dict, question: str, context: dict, cases: list[dict], topics: list[str],
            plan: dict, today: date, *, active_case: dict | None, recent: list[dict],
            retrieval: dict | None = None) -> tuple[dict, dict]:
    """一次规划可同时查询多个事项，表单仅采纳有原话支撑的事实。"""
    observation=dict(plan.get('observation') or {})
    if (context.get('processing_paused') and not set(topics)&{'eligibility','fit_exchange','next_step'}
            and not observation.get('case_action') and not (observation.get('quantity') and observation['quantity'].get('quote','') in question)):
        for field in ('requested_kind','target_spec','quantity'):
            observation.pop(field,None)
    context=dict(context)
    selected_case=active_case or (cases[0] if cases else None)
    current_case_key=str(selected_case['case_no']) if selected_case else None
    if context.get('fulfillment_case')!=current_case_key:
        context.pop('fulfillment',None)
    if observation.get('fulfillment'):
        context['fulfillment_case']=current_case_key
    memory, reset = apply_observation(context, observation or None, question, recent)
    known_reason = memory.get("reason")
    # 同时少配件和主机故障时，不能只查第一个分类就否定退货意愿。
    requested_kind=memory.get('requested_kind')
    if known_reason and requested_kind and not active_case:
        eligible=[issue for issue in memory.get('issues',[]) if issue['state']=='active'
                  and requested_kind in assess_eligibility(item,issue['reason'],today).kinds]
        if requested_kind not in assess_eligibility(item,known_reason,today).kinds and len(eligible)==1:
            primary=eligible[0]
            memory.update(reason=primary['reason'],issue_description=primary['quote'])
            known_reason=primary['reason']
            reset=True
    if observation.get('cancel_action') and set(topics)<={'eligibility','fit_exchange','product','next_step'}:
        result=_followup_reply(item,'本轮已停止准备办理，没有执行原单操作。')
        result['response_kind']='intent_paused'
        result['reset_suggestion']=True
        return result,memory
    intent_changed=observation.get('requested_kind') and observation['requested_kind']!=context.get('requested_kind')
    quantity_changed=observation.get('quantity') and any(memory.get(key)!=context.get(key) for key in ('quantity','quantity_mode'))
    if known_reason and not active_case and not observation.get('cancel_action') and (intent_changed or quantity_changed):
        topics=list(dict.fromkeys([*topics,'eligibility']))
    if topics == ["out_of_scope"]:
        result = _followup_reply(item, "我在这里帮你处理订单售后，物流、退换货或退款进度都可以问我。")
        result["response_kind"] = "out_of_scope"
        return result, memory
    follow_up = plan.get("follow_up")
    inquiry = set(topics) <= {"eligibility", "fit_exchange", "product"}
    if follow_up and inquiry and known_reason not in REASONS and not active_case:
        result = _model_clarification(item, follow_up, memory)
        if result:
            if plan.get("observation"):
                memory["issue_report"] = plan["observation"]["quote"]
            result["reset_suggestion"] = reset
            return result, memory
    if "fit_exchange" in topics:
        topics = [topic for topic in topics if topic != "fit_exchange"]
        topics.append("eligibility")
    if "next_step" in topics:
        topics = [topic for topic in topics if topic != "next_step"]
        topics.append("progress" if active_case else "eligibility")
    if memory.get("issue_state") in ("resolved", "packaging_only") and not known_reason and not active_case:
        topics = [topic for topic in topics if topic not in {"eligibility", "fit_exchange"}]
        if not topics:
            result = _followup_reply(item, "已撤回先前的问题和申请建议，商品目前完好。" +
                                      ("外包装异常可以保留照片。" if memory["issue_state"] == "packaging_only" else ""))
            result["reset_suggestion"] = True
            return result, memory
    if known_reason and not topics:
        topics = ["eligibility"]
    if 'amount_followup' in topics:
        prior=[topic for topic in context.get('focus',[]) if topic in ('freight','original_shipping','refund','product','offer')]
        if len(prior)==1:
            topics=[topic for topic in topics if topic!='amount_followup']+prior
    if set(topics) & {"basis", "amount_followup", "shop_current", "platform_floor", "policy_menu"}:
        result, policy_context = answer(item, question, None, memory, cases[0] if cases else None, today,
                           active_case=active_case, topics=topics,
                           last_result=recent[0]["result"] if recent else None)
        if policy_context.get("policy_reference"):
            memory["policy_reference"] = policy_context["policy_reference"]
    else:
        query_reason = known_reason if set(topics) & {"eligibility", "freight", "photos"} else None
        result, _ = _answer_order(item, question, None, memory, cases[0] if cases else None, today,
                                  active_case, topics, confirmed_reason=query_reason, trusted_reason=True)
    if retrieval:
        if retrieval["evidence"] or not result.get("evidence"):
            result["evidence"] = retrieval["evidence"]
        result["retrieval"] = {"routes": retrieval["routes"], "candidates": retrieval["candidates"],
                               "coverage":retrieval.get('coverage'),"aspects":retrieval.get('aspects',[]),
                               "timings_ms":retrieval.get('timings_ms'),
                               "read_loop":retrieval.get('read_loop'),
                               "used_for_answer": bool(retrieval["evidence"])}
        result["evidence_requested"] = "basis" in topics or bool(result.get("evidence_requested"))
        if result["evidence_requested"] and retrieval["evidence"]:
            result["answer"] += "\n" + retrieval["answer"]
    if reset and memory.get("issue_state") in ("resolved", "packaging_only"):
        result["answer"] = "买家已排除先前的商品问题，咨询中的旧申请建议已撤回。\n" + result["answer"]
    if known_reason and result.get("kinds"):
        description = memory.get("issue_description", "")
        if memory.get("target_spec") and memory["target_spec"] not in description:
            description += "。希望换成" + memory["target_spec"]
        result["suggested_description"] = description or None
    requested = memory.get("requested_kind")
    if requested in result.get("kinds", {}):
        result["recommended_kind"] = requested
        result["answer"] = "本单可申请" + KINDS[requested] + "。\n" + result["answer"]
        if requested == "exchange":
            result["answer"] += ("\n目标规格为" + memory["target_spec"] + "，由店铺确认可提供的规格。"
                                  if memory.get("target_spec") else "\n可在申请说明填写想换的规格，店铺会核对。")
    result["reset_suggestion"] = reset
    result["conversation_facts"] = {
        key: memory[key] for key in ("reason", "issue_description", "requested_kind", "target_spec")
        if memory.get(key)
    }
    if "photos" in topics:
        result["submission_requirements"] = "照片为选填。没有开箱视频也可提交申请，店铺会根据现有材料核对，必要时再要求补充。"
    memory["focus"] = topics
    memory["schema_version"] = 3
    result['executed_topics']=topics
    return result, memory


def execute(item: dict, question: str, context: dict, cases: list[dict], topics: list[str],
            plan: dict, today: date, *, active_case: dict | None, recent: list[dict],
            retrieval: dict | None = None) -> tuple[dict, dict]:
    """统一给所有语义分支挂上业务字段，模型不再读取旧答复拆句。"""
    result,memory=_execute(item,question,context,cases,topics,plan,today,active_case=active_case,recent=recent,retrieval=retrieval)
    result['task_signature']=task_signature(memory)
    result['task_revision']=memory.get('memory_revision',0)
    current=next((case for case in cases if case.get('is_active') or case.get('outbound_pending')),cases[0] if cases else None)
    result['current_case']={key:current.get(key) for key in ('case_no','status','status_label','intervention')} if current else None
    if current:
        result['current_case']['is_active']=bool(current.get('is_active') or current.get('outbound_pending'))
    result['suggested_quantity']=memory.get('quantity')
    if result.get('kinds'):
        active=list(dict.fromkeys(row['quote'] for row in memory.get('issues',[]) if row['state']=='active'))
        if active:
            result['suggested_description']='；'.join(active)[:500]
            if memory.get('target_spec'):
                result['suggested_description']=(result['suggested_description']+'；希望换成'+memory['target_spec'])[:500]
    observation=plan.get('observation') or {}
    result['action_draft_relevant']=bool(set(topics)&{'eligibility','fit_exchange','next_step'} or observation.get('case_action') or observation.get('fulfillment')
        or observation.get('quantity') and observation['quantity'].get('quote','') in question
        or observation.get('requested_kind') and observation['requested_kind']!=context.get('requested_kind'))
    requested=bool(set(topics)&{'eligibility','fit_exchange','next_step'} or observation.get('requested_kind') or observation.get('quantity') or observation.get('case_action') or observation.get('fulfillment'))
    if not observation.get('cancel_action') and (memory.get('issue_state') not in ('resolved','packaging_only') or memory.get('reason')=='wrong_purchase' or memory.get('case_action')):
        proposal=prepare_action(item,memory,cases,today,requested=requested)
        if proposal:
            result['action_draft']=proposal
            if proposal.get('state')=='needs_input' and proposal.get('action') in ('ship','confirm_delivery_address'):
                labels={'carrier':'快递公司','tracking':'运单号','recipient':'收件人','address':'收货地址','phone':'联系电话'}
                missing='、'.join(labels.get(key,key) for key in proposal['missing'])
                result['answer']='还需补充'+missing+'，资料齐全后再核对确认卡。'
    result['tool_facts']=build_facts(item,result,memory,result.get('executed_topics',topics),cases,today)
    return result,memory
