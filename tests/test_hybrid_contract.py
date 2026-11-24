"""验证双路索引的向量、范围与故障恢复契约，不依赖在线 Milvus。"""

import unittest
from unittest.mock import Mock,patch
from types import SimpleNamespace

from app.policies.evidence import PolicyIndexError
from app.storage.milvus_mall_hybrid import MilvusMallHybrid,DIMENSION
from app.policies.mall_retrieval import MallRetrieval,scope_key
from app.policies.evidence import PolicySource
from test_mall_agent import shipped_product


class HybridContractTests(unittest.TestCase):
    def test_live_sparse_result_preserves_local_route_and_rejects_untrusted_text(self):
        """Milvus 返回不同名次时保留范围内词法；外店和被修改的原文不能参与融合。"""
        item=shipped_product(shop_id='market-001',sku='A')
        articles=[PolicySource(code,code,'条款',code+'原文') for code in ('local','sparse','dense')]
        retriever=MallRetrieval('http://unused',persistent_dense=True,window_rerank=True)
        retriever.hybrid_index=Mock()
        retriever.hybrid_index.search.return_value=[
            SimpleNamespace(source='foreign',text='范围外原文'),
            SimpleNamespace(source='local',text='被修改的原文'),
            SimpleNamespace(source='sparse',text='sparse原文')]
        retriever.hybrid_index.search_dense.return_value=[SimpleNamespace(source='dense',text='dense原文')]
        semantic=Mock()
        semantic.dense_ranking.return_value=['dense']
        semantic.window_ranking.side_effect=lambda vector,ranking:list(reversed(ranking))
        retriever.cache['scope']=(True,semantic)
        retriever.hybrid_ready['scope']=True
        retriever.retry_after['scope']=float('inf')
        def checked(item,question,*,rank,assessment=None):
            rank(question,articles)
            return '',[],[]
        with patch('app.policies.mall_retrieval.scope_key',return_value='scope'), patch(
            'app.policies.mall_retrieval.order_policy_sources',return_value=('',articles)
        ), patch('app.policies.mall_retrieval.BM25') as words, patch(
            'app.policies.mall_retrieval.checked_mall_citations',side_effect=checked
        ), patch('app.policies.mall_retrieval.find_citation') as citation:
            words.return_value.rank.return_value=['local']
            result=retriever.search(item,'退货运费谁承担')
        self.assertEqual({a.article_id for a in citation.call_args.args[1]},{'local','sparse','dense'})
        self.assertIn('milvus_dense',result['routes'])
        self.assertIn('rrf_prior',result['routes'])

    def test_invalid_vectors_never_reach_database(self):
        index=MilvusMallHybrid('http://unused')
        index.request=Mock()
        for vectors in ([[0.0]*383],[[float('nan')]*DIMENSION],[]):
            with self.assertRaises(ValueError):
                index.index_vectors([{'id':'1'}],vectors)
        index.request.assert_not_called()

    def test_two_indexes_and_vectors_use_the_same_text_scope(self):
        index=MilvusMallHybrid('http://unused')
        index.request=Mock(side_effect=lambda path,*a,**k:{'has':False} if path=='collections/has' else {})
        row={'id':'1','shop_id':'scope-a','source':'freight','title':'寄回运费','text':'核实故障后由店铺承担。'}
        index.index_vectors([row],[[0.1]*DIMENSION])
        create=next(call.args[1] for call in index.request.call_args_list if call.args[0]=='collections/create')
        self.assertEqual({i['metricType'] for i in create['indexParams']},{'BM25','COSINE'})
        saved=next(call.args[1]['data'][0] for call in index.request.call_args_list if call.args[0]=='entities/upsert')
        self.assertEqual(saved['shop_id'],row['shop_id'])
        self.assertEqual(saved['text'],row['text'])
        self.assertEqual(len(saved['dense']),DIMENSION)

    def test_dense_scope_violation_is_rejected(self):
        index=MilvusMallHybrid('http://unused')
        index.request=Mock(return_value=[{'id':'1','shop_id':'foreign','title':'别店','source':'freight','text':'别店原文'}])
        with self.assertRaises(PolicyIndexError):
            index.search_dense('scope-a',[0.1]*DIMENSION)
        self.assertEqual(index.request.call_args.args[1]['filter'],'shop_id == "scope-a"')

    def test_dense_index_recovers_after_sparse_fallback(self):
        item=shipped_product(shop_id='market-001',sku='A')
        retriever=MallRetrieval('http://unused')
        retriever.index=Mock()
        retriever.hybrid_index=Mock()
        semantic=Mock()
        semantic.article_vectors=[[0.0]*DIMENSION]
        key=scope_key(item)
        retriever.cache[key]=(True,semantic)
        retriever.retry_after[key]=0
        # 这次不需要构建第二个模型；只补上上次失败的持久化稠密索引。
        with patch('app.policies.mall_retrieval.checked_mall_citations',return_value=('',[],[])):
            retriever.search(item,'依据呢')
        retriever.hybrid_index.index_vectors.assert_called_once()
        self.assertTrue(retriever.hybrid_ready[key])
        retriever.index.index_articles.assert_not_called()
