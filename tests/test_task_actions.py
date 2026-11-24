"""结构化事实、问题生命周期和确认卡的业务边界。"""

from datetime import date
from decimal import Decimal
import json
import unittest
from unittest.mock import Mock

from app.mall.chat_actions import prepare_action
from app.mall.dialogue_state import apply_observation, snapshot, task_signature, validate_observation
from app.mall.grounded_reply import reply_facts, validate_reply
from app.mall.semantic_consultation import execute
from app.mall.tool_facts import build_facts
from app.mall.agent import MallAgent, PLANNING_TOOL
from test_mall_agent import shipped_product


TODAY=date(2026,11,19)


def item(**changes):
    return shipped_product(order_id='CN-880001',item_no=1,shop_id='market-001',sku='sku-1',refund_balance=Decimal('78'),**changes)


class TaskActionTests(unittest.TestCase):
    def test_invalid_model_arguments_receive_one_schema_repair(self):
        planner=Mock(side_effect=[{'name':PLANNING_TOOL,'arguments':json.dumps({'topics':['photos'],'evidence':[]})},
            {'name':PLANNING_TOOL,'arguments':json.dumps({'topics':['photos'],'observation':{'quote':'没有视频','evidence':[
                {'kind':'unboxing_video','state':'reported_missing','quote':'没有视频'}]}})}])
        agent=MallAgent(planner)
        result=agent.select_queries({'question':'没有视频','use_model':True,'semantic':True,'dialogue':{}})
        self.assertEqual(result['route'],'function_call')
        self.assertEqual(planner.call_count,2)
        self.assertIn('validation_feedback',planner.call_args.kwargs['dialogue'])
        self.assertEqual(result['observation']['evidence'][0]['state'],'reported_missing')

    def test_schema_repair_has_a_hard_limit(self):
        planner=Mock(return_value={'name':'execute_unknown_action','arguments':'{}'})
        result=MallAgent(planner).select_queries({'question':'处理一下','use_model':True,'semantic':True})
        self.assertEqual(result['route'],'fallback')
        self.assertEqual(planner.call_count,2)

    def test_explicit_quantity_missing_from_plan_requests_repair(self):
        initial={'quote':'想换一件','requested_kind':'exchange'}
        planner=Mock(side_effect=[{'name':PLANNING_TOOL,'arguments':json.dumps({'topics':['eligibility'],'observation':initial})},
            {'name':PLANNING_TOOL,'arguments':json.dumps({'topics':['eligibility'],'observation':{
                **initial,'quantity':{'value':1,'quote':'换一件'}}})}])
        result=MallAgent(planner).select_queries({'question':'想换一件','use_model':True,'semantic':True})
        self.assertEqual(result['observation']['quantity']['value'],1)
        self.assertEqual(planner.call_count,2)

    def test_reported_video_does_not_create_an_upload_channel(self):
        facts={'F1':{'kind':'customer_task','data':{'evidence':[{'kind':'unboxing_video','state':'reported_available'}]}},
               'F2':{'kind':'submission_channels','data':{'video_upload_supported':False}}}
        with self.assertRaises(ValueError):
            validate_reply(json.dumps({'sentences':[{'text':'请直接上传开箱视频。','facts':['F1']}]}),facts)
        self.assertIn('注明',validate_reply(json.dumps({'sentences':[{'text':'可以在说明里注明已保留开箱视频。','facts':['F1','F2']}]}),facts))
        for text in ('可以上传照片，视频不需要。','这次不强制上传开箱视频。','没有视频也可以提交照片。'):
            self.assertEqual(validate_reply(json.dumps({'sentences':[{'text':text,'facts':['F1','F2']}]}),facts),text)

    def test_typed_facts_do_not_contain_legacy_answer(self):
        result,memory=execute(item(),'插电没反应，我想退一件',{},[],['eligibility'],{'observation':{
            'quote':'插电没反应，我想退一件','reason':'quality','requested_kind':'return_refund',
            'quantity':{'value':1,'quote':'退一件'}}},TODAY,active_case=None,recent=[])
        result['answer']='固定回复或编造的999999元'
        facts=reply_facts(result)
        self.assertTrue(all(isinstance(row,dict) for row in facts.values()))
        self.assertNotIn('999999',json.dumps(facts,ensure_ascii=False))
        self.assertEqual(result['action_draft']['quantity'],1)
        self.assertEqual(result['action_draft']['max_product_refund'],'39.00')

    def test_multiple_issues_resolve_one_without_losing_the_other(self):
        question='少了一条线，而且主机无法开机'
        memory,_=apply_observation({}, {'quote':question,'reason':'quality','issues':[
            {'reason':'missing','quote':'少了一条线','state':'active'},
            {'reason':'quality','quote':'主机无法开机','state':'active'}]},question,[])
        missing=next(row for row in memory['issues'] if row['reason']=='missing')
        before=task_signature(memory)
        changed,_=apply_observation(memory,{'quote':'线找到了，主机还是无法开机','retract':True,'issues':[
            {'id':missing['id'],'reason':'missing','quote':'线找到了','state':'resolved'}]},'线找到了，主机还是无法开机',[])
        self.assertEqual(changed['reason'],'quality')
        self.assertEqual([row['reason'] for row in changed['issues'] if row['state']=='active'],['quality'])
        self.assertNotEqual(task_signature(changed),before)
        self.assertTrue(changed['issue_events'])

    def test_issue_id_can_resolve_original_quote_beyond_recent_window(self):
        memory={'reason':'quality','issue_description':'无法开机','issues':[
            {'id':'issue-1','reason':'missing','quote':'少了线','state':'active'},
            {'id':'issue-2','reason':'quality','quote':'无法开机','state':'active'}]}
        observation={'quote':'线找到了，机器仍无法开机','issues':[
            {'id':'issue-1','quote':'少了线','state':'resolved'},
            {'id':'issue-2','quote':'无法开机','state':'active'}]}
        validate_observation(observation)
        changed,_=apply_observation(memory,observation,observation['quote'],[])
        self.assertEqual(changed['issues'][0]['state'],'resolved')
        self.assertEqual(changed['reason'],'quality')
        self.assertEqual(changed['issue_events'][-1]['quote'],observation['quote'])

    def test_two_faults_of_the_same_reason_keep_separate_ids(self):
        question='屏幕裂了，而且充不进电'
        memory,_=apply_observation({}, {'quote':question,'reason':'quality','issues':[
            {'reason':'quality','quote':'屏幕裂了','state':'active'},
            {'reason':'quality','quote':'充不进电','state':'active'}]},question,[])
        self.assertEqual(len(memory['issues']),2)
        first=memory['issues'][0]
        changed,_=apply_observation(memory,{'quote':'屏幕是保护膜，充电还是不行','issues':[
            {'id':first['id'],'quote':'屏幕是保护膜','state':'resolved'}]},'屏幕是保护膜，充电还是不行',[])
        self.assertEqual([row['quote'] for row in changed['issues'] if row['state']=='active'],['充不进电'])

    def test_quantity_requires_literal_application_count(self):
        memory,_=apply_observation({}, {'quote':'买了两件，想退一件','quantity':{'value':1,'quote':'退一件'}},'买了两件，想退一件',[])
        self.assertEqual(memory['quantity'],1)
        unchanged,_=apply_observation(memory,{'quote':'看下物流','quantity':{'value':2,'quote':'退两件'}},'看下物流',[])
        self.assertEqual(unchanged['quantity'],1)
        all_items,_=apply_observation(memory,{'quote':'那全部退吧','quantity':{'mode':'all_remaining','quote':'全部退'}},'那全部退吧',[])
        self.assertEqual(all_items['quantity_mode'],'all_remaining')
        self.assertNotIn('quantity',all_items)

    def test_evidence_is_reported_not_uploaded_and_stale_update_cannot_revert(self):
        memory,_=apply_observation({}, {'quote':'我没有录像','evidence':[{'kind':'unboxing_video','quote':'没有录像','state':'reported_missing'}]},'我没有录像',[])
        self.assertEqual(memory['evidence'][0]['state'],'reported_missing')
        with self.assertRaises(ValueError):
            validate_observation({'quote':'我有照片','evidence':[{'kind':'photos','quote':'有照片','state':'uploaded'}]})

    def test_pause_does_not_resolve_defect_or_withdraw_real_case(self):
        memory={'reason':'quality','issue_description':'无法开机','requested_kind':'exchange'}
        result,changed=execute(item(),'先别办了',memory,[],['eligibility'],{'observation':{'quote':'先别办了','cancel_action':True}},TODAY,active_case=None,recent=[])
        self.assertEqual(changed['reason'],'quality')
        self.assertNotIn('requested_kind',changed)
        self.assertTrue(changed['processing_paused'])
        self.assertNotIn('action_draft',result)

    def test_cancelling_and_resuming_same_payload_has_a_new_revision(self):
        question='无法开机，想换一件'
        _,memory=execute(item(),question,{},[],['eligibility'],{'observation':{'quote':question,
            'issue_quote':'无法开机','reason':'quality','requested_kind':'exchange','quantity':{'value':1,'quote':'换一件'}}},TODAY,active_case=None,recent=[])
        first=prepare_action(item(),memory,[],TODAY,requested=True)
        _,paused=execute(item(),'先不办',memory,[],['eligibility'],{'observation':{'quote':'先不办','cancel_action':True}},TODAY,active_case=None,recent=[])
        _,resumed=execute(item(),'还是换一件',paused,[],['eligibility'],{'observation':{'quote':'还是换一件',
            'requested_kind':'exchange','quantity':{'value':1,'quote':'换一件'}}},TODAY,active_case=None,recent=[])
        self.assertEqual(task_signature(memory),task_signature(resumed))
        self.assertGreater(resumed['memory_revision'],first['memory_revision'])

    def test_preview_needs_quantity_and_never_clamps_excess(self):
        memory={'reason':'quality','issue_description':'无法开机','requested_kind':'return_refund'}
        self.assertEqual(prepare_action(item(),memory,[],TODAY,requested=True)['missing'],['quantity'])
        over=prepare_action(item(),{**memory,'quantity':3},[],TODAY,requested=True)
        self.assertEqual(over['state'],'needs_input')
        self.assertEqual(over['remaining_quantity'],2)
        all_items=prepare_action(item(),{**memory,'quantity_mode':'all_remaining'},[],TODAY,requested=True)
        self.assertEqual(all_items['quantity'],2)
        self.assertEqual(all_items['max_product_refund'],'78.00')

    def test_multi_issue_eligibility_checks_requested_solution(self):
        question='少了线，而且主机不能开机，想退货退款'
        result,memory=execute(item(),question,{},[],['eligibility'],{'observation':{
            'quote':question,'reason':'missing','requested_kind':'return_refund','issues':[
                {'reason':'missing','quote':'少了线','state':'active'},
                {'reason':'quality','quote':'主机不能开机','state':'active'}]}},TODAY,active_case=None,recent=[])
        self.assertEqual(memory['reason'],'quality')
        self.assertIn('return_refund',result['kinds'])
        self.assertEqual(len(memory['issues']),2)

    def test_unclear_state_does_not_commit_a_guessed_reason(self):
        result,memory=execute(item(),'用起来感觉不太对',{},[],['eligibility'],{'observation':{
            'quote':'用起来感觉不太对','issue_state':'unclear','reason':'quality'},
            'follow_up':{'fact':'expected_vs_actual','question':'使用时具体出现了什么情况？'}},TODAY,active_case=None,recent=[])
        self.assertNotIn('reason',memory)
        self.assertEqual(result['response_kind'],'clarification')
        self.assertFalse(result['kinds'])

    def test_read_query_cannot_resume_paused_application(self):
        memory={'reason':'quality','issue_description':'无法开机','requested_kind':'return_refund','quantity':1,'processing_paused':True}
        self.assertIsNone(prepare_action(item(),memory,[],TODAY,requested=True))
        _,paused=execute(item(),'看看原快递',{'reason':'quality','issue_description':'无法开机','processing_paused':True},[],['logistics'],
            {'observation':{'quote':'看看原快递','requested_kind':'exchange','quantity':{'value':1,'quote':'之前换一件'}}},TODAY,active_case=None,recent=[])
        self.assertTrue(paused['processing_paused'])
        self.assertNotIn('requested_kind',paused)

    def test_accept_offer_card_uses_offer_quantity_and_version(self):
        case={'case_no':'10','status':'submitted','is_active':True,'quantity':2,'kind':'return_refund',
              'actions':['accept_offer','reject_offer'],'prompt':{'id':'proposal-1','offer':{'kind':'partial_refund','quantity':1,'amount':'20.00'}}}
        draft=prepare_action(item(),{'case_action':'accept_offer','action_note':'同意这个方案'},[case],TODAY,requested=True)
        self.assertEqual(draft['quantity'],1)
        self.assertEqual(draft['kind'],'partial_refund')
        self.assertEqual(draft['payload']['prompt_id'],'proposal-1')

    def test_unknown_original_shipping_not_inferred_free(self):
        facts={'F1':{'kind':'original_shipping','data':{'recorded':False,'amount':None}}}
        with self.assertRaises(ValueError):
            validate_reply('{"sentences":[{"text":"原配送费为0元。","facts":["F1"]}]}',facts)

    def test_case_type_and_fact_type_are_separate(self):
        case={'case_no':'10','status':'submitted','status_label':'待店铺响应','kind':'return_refund','actions':['withdraw']}
        facts=build_facts(item(),{'case_no':'10'},{},['progress'],[case],TODAY)
        saved=next(row for row in facts.values() if row['kind']=='case')
        self.assertEqual(saved['data']['kind'],'return_refund')

    def test_task_survives_history_window_and_read_queries_do_not_change_signature(self):
        memory={'reason':'quality','issue_description':'开不了机','quantity':1,'requested_kind':'return_refund'}
        signature=task_signature(memory)
        for _ in range(8):
            _,memory=execute(item(),'查物流',memory,[],['logistics'],{},TODAY,active_case=None,recent=[])
        self.assertEqual(task_signature(memory),signature)
        visible=snapshot(memory,[],item(),99)
        self.assertEqual(visible['quantity'],1)
        self.assertEqual(visible['requested_kind'],'return_refund')
