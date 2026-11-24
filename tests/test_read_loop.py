"""只读循环不能丢弃原问点、换范围、无限重查或在失败时丢失已有依据。"""
import unittest
from unittest.mock import Mock,patch
from app.mall.read_loop import retrieve_with_followup,evidence_decider,business_decider

def evidence(coverage='partial',labels=('运费','会员'),supported=(True,False),scope='same'):
    return {'coverage':coverage,'scope':scope,'aspects':[{'label':l,'supported':s} for l,s in zip(labels,supported)],'candidates':4}

class ReadLoopTests(unittest.TestCase):
    def test_business_followup_accepts_only_bounded_read_topics(self):
        for content in ['{"topics":["refund","progress"]}','{"topics":["ship"]}','{"topics":[],"order_id":"foreign"}']:
            with patch('app.mall.read_loop.deepseek_chat',return_value={'content':content}):
                if 'refund' in content:self.assertEqual(business_decider('unused')({}),['refund','progress'])
                else:
                    with self.assertRaises(ValueError):business_decider('unused')({})
    def test_provider_json_contract_and_original_user_goal(self):
        with patch('app.mall.read_loop.deepseek_chat',return_value={'content':'{"action":"stop"}'}) as request:
            evidence_decider('unused')({'question':'原问题'})
            self.assertIn('JSON',request.call_args.args[1]['messages'][0]['content'])
        r=Mock();r.search.return_value=evidence()
        decide=Mock(return_value={'action':'stop'})
        retrieve_with_followup(r,{},'改写后的问题',decide,user_question='原问题')
        self.assertEqual(decide.call_args.args[0]['question'],'原问题')
    def test_supported_skips_model(self):
        retriever=Mock();retriever.search.return_value=evidence('supported')
        decide=Mock();retrieve_with_followup(retriever,{},'q',decide)
        decide.assert_not_called()
    def test_improves_with_at_most_two_queries(self):
        r=Mock();r.search.side_effect=[evidence(),evidence('supported',supported=(True,True))]
        result=retrieve_with_followup(r,{},'q',lambda _:dict(action='search',query='better'))
        self.assertEqual(result['coverage'],'supported');self.assertEqual(r.search.call_count,2)
    def test_dropping_unsupported_question_cannot_improve_coverage(self):
        r=Mock();r.search.side_effect=[evidence(),evidence('supported',('运费',),(True,))]
        self.assertEqual(retrieve_with_followup(r,{},'q',lambda _:dict(action='search',query='better'))['coverage'],'partial')
    def test_foreign_scope_and_model_failure_keep_verified_result(self):
        for second in [evidence(scope='foreign'),TimeoutError()]:
            r=Mock();r.search.side_effect=[evidence(),second]
            result=retrieve_with_followup(r,{},'q',lambda _:dict(action='search',query='better'))
            self.assertEqual(result['scope'],'same');self.assertEqual(result['coverage'],'partial')
    def test_write_parameters_and_repeated_query_never_execute(self):
        for decision in [dict(action='refund'),dict(action='search',query='q'),dict(action='search',query='x',order_id='foreign')]:
            r=Mock();r.search.return_value=evidence()
            retrieve_with_followup(r,{},'q',lambda _:decision)
            self.assertEqual(r.search.call_count,1)

    def test_original_question_cannot_be_lost_by_two_successive_rewrites(self):
        r=Mock();r.search.side_effect=[evidence('unsupported',('clarify',),(False,)),evidence('supported',('运费',),(True,))]
        def original(item,question,*,assessment):
            assessment.extend([{'label':'运费'},{'label':'会员'}])
        with patch('app.mall.dialogue.checked_mall_citations',side_effect=original):
            result=retrieve_with_followup(r,{},'模糊改写',lambda _:dict(action='search',query='只问运费'),user_question='运费和会员')
        self.assertEqual(result['coverage'],'unsupported')
