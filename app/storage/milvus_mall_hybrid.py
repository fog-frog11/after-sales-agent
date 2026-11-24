"""订单条款的 Milvus 稀疏、稠密持久化索引。"""

from copy import deepcopy
import math

from app.policies.evidence import PolicyIndexError, PolicySource
from app.storage.milvus_policies import MilvusPolicies, SCHEMA


COLLECTION='mall_policy_hybrid_e5_v3'
DIMENSION=384


class MilvusMallHybrid(MilvusPolicies):
    def __init__(self,url: str,*,collection_name: str=COLLECTION):
        super().__init__(url,collection_name=collection_name)

    def index_vectors(self,articles: list[dict],vectors) -> None:
        """使用独立集合保留旧索引，向量维度与 E5 模型版本固定。"""
        if len(articles)!=len(vectors) or any(len(vector)!=DIMENSION or any(not math.isfinite(float(x)) for x in vector) for vector in vectors):
            raise ValueError('E5向量与条款不匹配')
        target={'collectionName':self.collection_name}
        if not self.request('collections/has',target)['has']:
            schema=deepcopy(SCHEMA)
            schema['fields'].append({'fieldName':'dense','dataType':'FloatVector','elementTypeParams':{'dim':str(DIMENSION)}})
            self.request('collections/create',{**target,'schema':schema,'indexParams':[
                {'fieldName':'sparse','indexName':'terms_bm25','metricType':'BM25','params':{'index_type':'SPARSE_INVERTED_INDEX'}},
                {'fieldName':'dense','indexName':'terms_e5','metricType':'COSINE','params':{'index_type':'FLAT'}}]},timeout=30)
        rows=[{**row,'dense':[float(value) for value in vector]} for row,vector in zip(articles,vectors,strict=True)]
        for start in range(0,len(rows),250):
            self.request('entities/upsert',{**target,'data':rows[start:start+250]},timeout=30)
        self.request('collections/load',target,timeout=30)

    def search_dense(self,scope: str,vector,*,limit: int=12,timeout: int=3) -> list[PolicySource]:
        rows=self.request('entities/search',{'collectionName':self.collection_name,'annsField':'dense',
            'data':[[float(value) for value in vector]],'filter':self.scope_filter(scope,None),
            'outputFields':['shop_id','title','source','text'],'limit':limit,'consistencyLevel':'Strong',
            'searchParams':{'metric_type':'COSINE','params':{}}},timeout=timeout)
        if any(row['shop_id']!=scope for row in rows):
            raise PolicyIndexError('Milvus稠密结果超出订单范围')
        return [PolicySource(str(row['id']),row['title'],row['source'],row['text']) for row in rows]
