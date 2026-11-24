"""验证统一会话、工具执行、依据回复和检索范围的关键边界。"""

import json
import unittest
from datetime import date
from contextlib import nullcontext
from unittest.mock import Mock, patch

from app.mall.agent import MallAgent, PLANNING_TOOL
from app.mall.dialogue_state import apply_observation, merge_fallback, snapshot
from app.mall.grounded_reply import reply_facts, validate_reply
from app.mall.semantic_consultation import execute
from app.policies.mall_retrieval import MallRetrieval, scope_key
from app.policies.evidence import PolicySource
from app.storage.postgres_mall import PostgresMall
from app.mall.rules import MallQuestion
from app.tickets.service_requests import ServiceError
from test_mall_agent import shipped_product


class SemanticDialogueTests(unittest.TestCase):
    """用户原话、状态更正和业务数据共同决定动作。"""

    def test_model_observation_reaches_tools_and_correction_clears_old_draft(self):
        planner = Mock(return_value={"name": PLANNING_TOOL, "arguments": json.dumps({
            "topics": ["eligibility"], "observation": {"quote": "插电后完全没反应", "reason": "quality",
                                                       "requested_kind": "return_refund"}})})
        callback = Mock(return_value={})
        MallAgent(planner).run("插电后完全没反应", True, lambda *_: None, respond_semantic=callback)
        observation = callback.call_args.args[2]["observation"]
        result, memory = execute(shipped_product(), "插电后完全没反应", {}, [], ["eligibility"],
                                 {"observation": observation}, date(2026, 11, 19), active_case=None, recent=[])
        self.assertEqual(result["recommended_kind"], "return_refund")
        self.assertEqual(result["suggested_description"], "插电后完全没反应")
        changed, reset = apply_observation(memory, {"quote": "是我没插好，现在好了", "retract": True},
                                           "是我没插好，现在好了", [])
        self.assertTrue(reset)
        self.assertNotIn("reason", changed)
        self.assertIn("quality", changed["rejected_reasons"])

    def test_hallucinated_facts_do_not_enter_memory(self):
        updated, _ = apply_observation({}, {"reason": "quality", "quote": "已经坏了"}, "买了什么", [])
        self.assertNotIn("reason", updated)
        recent = [{"question": "围巾偏小", "result": {"answer": "查到了"}, "context": {}}]
        visible = snapshot({"reason": "wrong_purchase", "target_spec": "大一码"}, recent,
                           shipped_product(), 9)
        self.assertEqual(visible["recent"][0]["user"], "围巾偏小")
        self.assertEqual(visible["reason"], "wrong_purchase")
        self.assertEqual(visible["version"], 9)

    def test_retracted_historical_reason_and_model_failure_keep_memory_consistent(self):
        context = {"reason":"wrong_item", "issue_description":"发错颜色", "requested_kind":"return_refund",
                   "rejected_reasons":["missing"]}
        rows = [{"question":"少了一条线", "result":{}}]
        updated, reset = apply_observation(context,{"reason":"missing", "quote":"少了一条线"},"问下物流",rows)
        self.assertFalse(reset)
        self.assertEqual(updated["reason"],"wrong_item")
        preserved = merge_fallback(context,{"focus":["logistics"]},{})
        self.assertEqual(preserved["requested_kind"],"return_refund")
        cleared = merge_fallback(context,{"reason":None},{"reset_suggestion":True})
        self.assertNotIn("requested_kind",cleared)

    def test_concurrent_turn_replans_once_with_fresh_snapshot(self):
        planner = Mock(return_value={"name":PLANNING_TOOL,"arguments":'{"topics":["logistics"]}'})
        store = PostgresMall("unused",planner,model_dialogue=True)
        conn = Mock()
        conn.execute.return_value.fetchall.side_effect = [[],[
            {"turn_no":9,"question":"想换货","context":{"requested_kind":"exchange"},"result":{"answer":"可以申请"}}]]
        with patch.object(store,"connect",side_effect=lambda:nullcontext(conn)), patch.object(
                store,"_items",return_value=[shipped_product()]), patch(
                'app.storage.postgres_mall.applicable_supplements',return_value=[]), patch.object(
                store,"_answer_consultation",side_effect=[ServiceError(409,"已更新"),{"answer":"已签收","route":"function_call"}]) as execute_query:
            self.assertEqual(store.consult("buyer-001","CN-100001",1,MallQuestion(question="快递呢"))["answer"],"已签收")
        self.assertEqual(planner.call_count,2)
        self.assertEqual(execute_query.call_args.kwargs['planned_version'],9)
        self.assertEqual(planner.call_args.kwargs['dialogue']['requested_kind'],'exchange')

    def test_confirmed_issue_outlives_recent_message_window(self):
        memory={'reason':'missing','issue_description':'少了充电线','requested_kind':'reship','issue_state':'active'}
        item=shipped_product()
        for _ in range(9):
            _,memory=execute(item,'物流状态怎样',memory,[],['logistics'],{},date(2026,11,19),active_case=None,recent=[])
        result,memory=execute(item,'刚才那个还能办吗',memory,[],['eligibility'],{},date(2026,11,19),active_case=None,recent=[])
        self.assertEqual(memory['issue_description'],'少了充电线')
        self.assertEqual(result['recommended_kind'],'reship')
        self.assertIn('reship',result['kinds'])

    def test_generated_failure_is_saved_and_does_not_overwrite_verified_answer(self):
        planner = Mock(return_value={"name":PLANNING_TOOL,"arguments":'{"topics":["product"]}'})
        store = PostgresMall("unused",planner,model_dialogue=True,
                             composer=lambda _: '{"sentences":[{"text":"退款999元","facts":["F1"]}]}')
        conn = Mock()
        conn.execute.return_value.fetchall.return_value=[]
        with patch.object(store,"connect",side_effect=lambda:nullcontext(conn)), patch.object(
                store,"_items",return_value=[shipped_product()]), patch(
                'app.storage.postgres_mall.applicable_supplements',return_value=[]), patch.object(
                store,"_answer_consultation",return_value={"answer":"实付39元","route":"function_call","_writer_turn":4}):
            result=store.consult("buyer-001","CN-100001",1,MallQuestion(question="多少元"))
        self.assertEqual(result['answer'],'实付39元')
        self.assertEqual(result['generation_status'],'fallback')
        self.assertIn('UPDATE public.mall_consultations',conn.execute.call_args.args[0])

    def test_generation_can_repair_one_invalid_reference_without_canned_reply(self):
        planner=Mock(return_value={'name':PLANNING_TOOL,'arguments':'{"topics":["product"]}'})
        composer=Mock(side_effect=['{"sentences":[{"text":"实付999元","facts":["F1"]}]}',
                                  '{"sentences":[{"text":"这件实付39元。","facts":["F1"]}]}'])
        store=PostgresMall('unused',planner,model_dialogue=True,composer=composer)
        conn=Mock()
        conn.execute.return_value.fetchall.return_value=[]
        with patch.object(store,'connect',side_effect=lambda:nullcontext(conn)), patch.object(
                store,'_items',return_value=[shipped_product()]), patch(
                'app.storage.postgres_mall.applicable_supplements',return_value=[]), patch.object(
                store,'_answer_consultation',return_value={'answer':'实付39元','route':'function_call','_writer_turn':4}):
            result=store.consult('buyer-001','CN-100001',1,MallQuestion(question='多少钱'))
        self.assertEqual(result['answer_source'],'model')
        self.assertEqual(result['generation_attempts'],2)
        self.assertIn('validation_feedback',composer.call_args.args[0])

    def test_reply_validates_amounts_actions_and_references(self):
        facts = {"F1": "本单可申请退货，核实后由商家承担寄回运费。", "F2": "报价 ¥9.00"}
        self.assertEqual(validate_reply('{"sentences":[{"text":"这单可以申请退货。","facts":["F1"]}]}', facts),
                         "这单可以申请退货。")
        for text, refs in (("已经帮你提交申请", ["F1"]), ("报价18元", ["F2"]), ("商家承担寄回运费", ["F1"]),
                           ("可以退", ["F9"])):
            with self.subTest(text=text), self.assertRaises(ValueError):
                validate_reply(json.dumps({"sentences": [{"text": text, "facts": refs}]}), facts)
        self.assertEqual(validate_reply('{"sentences":[{"text":"报价9元","facts":["F1"]}]}',{"F1":"报价9.00元"}),"报价9元")
        with self.assertRaises(ValueError):
            validate_reply('{"sentences":[{"text":"你想申请补发的话，可以在页面确认提交。","facts":["F1"]}]}',
                           {"F1":"少件已撤回，商品完好。"}, allowed_actions=[])
        self.assertEqual(validate_reply('{"sentences":[{"text":"你刚才想申请仅退款。","facts":["F1"]}]}',
            {"F1":"顾客想申请的方式：仅退款"},allowed_actions=[]),'你刚才想申请仅退款。')
        with self.assertRaises(ValueError):
            validate_reply('{"sentences":[{"text":"之前的售后申请撤回就行。","facts":["F1"]}]}',
                {"F1":"咨询中的申请建议已撤回，未提交售后"},case_exists=False)

    def test_order_candidate_numbers_are_actual_reply_facts(self):
        facts=reply_facts({'answer':'找到了匹配商品。','order_candidates':[
            {'order_id':'CN-400001','title':'围巾','variant':'红色','paid_unit':'29.00'}]})
        source=next(key for key,text in facts.items() if 'paid_unit=29.00' in text)
        raw=json.dumps({'sentences':[{'text':'找到了这条红色围巾，实付单价29元。','facts':[source]}]})
        self.assertIn('29元',validate_reply(raw,facts))

    def test_fee_question_preserves_actual_issue_and_packaging_only_clears_draft(self):
        context = {"reason":"missing","issue_description":"把手没收到","requested_kind":"reship"}
        updated,_=apply_observation(context,{"quote":"邮费多少","reason":"missing"},"邮费多少",[])
        self.assertEqual(updated['issue_description'],'把手没收到')
        result, memory=execute(shipped_product(),"把手找到了，商品没坏，纸盒扁了",context,[],['eligibility'],
            {'observation':{'quote':'把手找到了，商品没坏，纸盒扁了','retract':True,'issue_state':'packaging_only'}},
            date(2026,11,19),active_case=None,recent=[])
        self.assertFalse(result['kinds'])
        self.assertNotIn('reason',memory)
        self.assertTrue(result['reset_suggestion'])
        changed,_=apply_observation(memory,{'quote':'但我还是不想要，退掉','reason':'wrong_purchase',
            'issue_state':'resolved','requested_kind':'return_refund'},'但我还是不想要，退掉',[])
        self.assertEqual(changed['reason'],'wrong_purchase')
        self.assertEqual(changed['requested_kind'],'return_refund')
        self.assertEqual(changed['issue_state'],'active')

    def test_retrieval_rejects_foreign_scope_and_keeps_old_versions_distinct(self):
        item = shipped_product(shop_id="shop-a", sku="A")
        self.assertNotEqual(scope_key(item), scope_key({**item, "shop_id": "shop-b"}))
        self.assertNotEqual(scope_key(item), scope_key({**item, "terms_version": 3}))
        retriever = MallRetrieval("")
        # 稀疏降级与向量不可用时，候选仍严格来自本单条款。
        key = scope_key(item)
        retriever.cache[key] = (False, None)
        retriever.retry_after[key] = float('inf')
        result = retriever.search(item, "查本单条款：寄回运费谁承担？")
        self.assertTrue(result["evidence"])
        self.assertTrue(all("第 2 版" in row["source"] for row in result["evidence"]))

    def test_retriever_retries_dependency_and_rejects_foreign_text(self):
        item=shipped_product(shop_id='shop-a',sku='A')
        key=scope_key(item)
        retriever=MallRetrieval('http://unused')
        retriever.index=Mock()
        retriever.index.search.return_value=[PolicySource('foreign','别店规则','freight','所有运费免费')]
        retriever.cache[key]=(False,None)
        retriever.retry_after[key]=0
        with patch('app.policies.mall_retrieval.preview_encoder',side_effect=ValueError('未配置模型')):
            result=retriever.search(item,'寄回运费谁承担')
        retriever.index.index_articles.assert_called_once()
        self.assertIn('milvus_bm25',result['routes'])
        self.assertTrue(result['evidence'])
        self.assertNotIn('所有运费免费',str(result['evidence']))
