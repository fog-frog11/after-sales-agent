"""在同一批独立标注条款题上比较持久化双路检索、融合与窗口排序。"""

import argparse
from datetime import date, datetime
import hashlib
import json
import math
from pathlib import Path
from statistics import mean
import sys
from time import perf_counter

from app.policies.evidence import split_aspects
from app.policies.retrieval_ranking import reciprocal_rank_fusion
from app.policies.semantic_terms import E5Encoder, SemanticTerms
from app.policies.mall_rank import BM25
from app.settings import ROOT, local_setting
from app.storage.milvus_mall_hybrid import MilvusMallHybrid
from scripts.evaluate_mall_terms import load_corpus


def relevance_groups(shop, version, labels):
    """空标签代表缺少支持原文，不得放入可评分题的分母。"""
    return [{f'{shop}/{version}/{tag}' for tag in group} for group in labels if group] if version else []


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--distractor-scopes',type=int,default=100)
    parser.add_argument('--out',type=Path)
    parser.add_argument('--offline',action='store_true',help='只比较本机算法，不写入或查询 Milvus')
    parser.add_argument('--questions',type=Path,default=ROOT/'scripts/fixtures/mall_terms_questions.json')
    parser.add_argument('--reuse-index',action='store_true',help='读取已存在的同一语料索引，先核对各范围条数')
    args=parser.parse_args()
    sys.path.append(str(ROOT/'.local/retrieval-deps'))
    encoder=E5Encoder(ROOT/'.local/models/multilingual-e5-small')
    index=None if args.offline else MilvusMallHybrid(local_setting('MILVUS_URL'),collection_name='mall_policy_hybrid_benchmark_v3')
    if args.offline:
        args.distractor_scopes=0
    editions,scopes=load_corpus()
    signature=hashlib.sha256((ROOT/'scripts/fixtures/mall_terms_catalog.json').read_bytes()).hexdigest()[:12]
    semantics={}
    lexical={}
    identifiers={}
    rows=[]
    started=perf_counter()
    def records(scope,articles):
        return [{'id':hashlib.sha256((scope+a.article_id).encode()).hexdigest()[:30],
                 'shop_id':scope,'title':a.title,'source':a.article_id,'text':a.text} for a in articles]
    for key,articles in scopes.items():
        scope='gold-'+signature+'-'+key[0]+'-'+key[1]
        identifiers[key]=scope
        semantics[key]=SemanticTerms(encoder,articles)
        lexical[key]=BM25(articles)
        if index and not args.reuse_index:
            index.index_vectors(records(scope,articles),semantics[key].article_vectors)
        elif index and index.count_articles(scope)!=len(articles):
            raise ValueError('复用索引的购买范围条数不一致')
    first=next(iter(scopes))
    distractors=scopes[first]
    for number in range(args.distractor_scopes):
        scope=f'noise-{signature}-{number}'
        if not args.reuse_index:
            index.index_vectors(records(scope,distractors),semantics[first].article_vectors)
        elif index.count_articles(scope)!=len(distractors):
            raise ValueError('复用索引的干扰范围条数不一致')
    prepare_ms=(perf_counter()-started)*1000
    fixture=json.loads(args.questions.read_text('utf-8'))
    for case_id,shop,bought,question,labels in fixture['cases']:
        purchased=date.fromisoformat(bought)
        version=max((key for key,when in editions.items() if when<=purchased),key=editions.__getitem__,default=None)
        key=(shop,version)
        gold=relevance_groups(shop,version,labels)
        paths={name:[] for name in ('milvus_bm25','milvus_e5','rrf','window_rerank','window_prior_rerank','three_route_rrf','three_route_prior_rerank')}
        timing={name:0.0 for name in paths}
        if version:
            articles=scopes[key]
            by_id={a.article_id:a for a in articles}
            semantic=semantics[key]
            for query in split_aspects(question) or [question]:
                start=perf_counter()
                local_sparse=lexical[key].rank(query)
                local_ms=(perf_counter()-start)*1000
                start=perf_counter()
                sparse=index.search(identifiers[key],query,limit=12) if index else lexical[key].rank(query)
                sparse_ms=(perf_counter()-start)*1000
                start=perf_counter()
                vector=semantic.query_vector(query)
                dense=index.search_dense(identifiers[key],vector,limit=12) if index else semantic.dense_ranking(vector,12)
                dense_ms=(perf_counter()-start)*1000
                if index:
                    for hit in [*sparse,*dense]:
                        if hit.source not in by_id or hit.text!=by_id[hit.source].text:
                            raise AssertionError('返回了范围外或原文不一致的条款')
                    sparse=[hit.source for hit in sparse]
                    dense=[hit.source for hit in dense]
                start=perf_counter()
                fused=reciprocal_rank_fusion(sparse,dense)[:12]
                fused_ms=(perf_counter()-start)*1000
                start=perf_counter()
                ranked=semantic.window_ranking(vector,fused)
                conservative=reciprocal_rank_fusion(fused,ranked)
                window_ms=(perf_counter()-start)*1000
                start=perf_counter()
                three=reciprocal_rank_fusion(local_sparse,sparse,dense)[:12]
                three_fused_ms=(perf_counter()-start)*1000
                start=perf_counter()
                three_ranked=semantic.window_ranking(vector,three)
                three_conservative=reciprocal_rank_fusion(three,three_ranked)
                three_window_ms=(perf_counter()-start)*1000
                for name,ranking,cost in (
                    ('milvus_bm25',sparse,sparse_ms),('milvus_e5',dense,dense_ms),
                    ('rrf',fused,sparse_ms+dense_ms+fused_ms),
                    ('window_rerank',ranked,sparse_ms+dense_ms+fused_ms+window_ms),
                    ('window_prior_rerank',conservative,sparse_ms+dense_ms+fused_ms+window_ms),
                    ('three_route_rrf',three,local_ms+sparse_ms+dense_ms+three_fused_ms),
                    ('three_route_prior_rerank',three_conservative,local_ms+sparse_ms+dense_ms+three_fused_ms+three_window_ms)):
                    paths[name].append(ranking)
                    timing[name]+=cost
        scored={}
        for name,rankings in paths.items():
            flattened=list(dict.fromkeys(article for ranking in rankings for article in ranking[:3]))
            hit=bool(gold) and all(set(flattened)&group for group in gold)
            relevant=set().union(*gold) if gold else set()
            reciprocal=max((1/(rank+1) for ranking in rankings for rank,article in enumerate(ranking) if article in relevant),default=0)
            dcg=sum(1/math.log2(rank+2) for rank,article in enumerate(flattened) if article in relevant)
            ideal=sum(1/math.log2(rank+2) for rank in range(min(len(flattened),len(relevant))))
            scored[name]={'all_aspects_hit_at_3':hit,'mrr':reciprocal,'ndcg_of_merged_top3':dcg/ideal if ideal else 0,
                          'query_ms':timing[name],'rankings':rankings}
        rows.append({'id':case_id,'question':question,'edition':version,'expected':[sorted(g) for g in gold],'strategies':scored})
    supported=[row for row in rows if row['expected']]
    metrics={}
    for name in ('milvus_bm25','milvus_e5','rrf','window_rerank','window_prior_rerank','three_route_rrf','three_route_prior_rerank'):
        costs=sorted(row['strategies'][name]['query_ms'] for row in rows)
        metrics[name]={'supported_questions':len(supported),'all_aspects_hit_at_3':sum(row['strategies'][name]['all_aspects_hit_at_3'] for row in supported),
            'mrr':mean(row['strategies'][name]['mrr'] for row in supported),
            'ndcg_of_merged_top3':mean(row['strategies'][name]['ndcg_of_merged_top3'] for row in supported),
            'p95_ms':costs[max(0,math.ceil(len(costs)*.95)-1)]}
    report={'questions':len(rows),'gold_scopes':len(scopes),'distractor_scopes':args.distractor_scopes,
        'question_fixture':args.questions.name,'unsupported_questions':sum(not row['expected'] for row in rows),
        'indexed_articles':sum(len(book) for book in scopes.values())+args.distractor_scopes*len(distractors),
        'scope_violations':0 if index else None,'persistent_milvus':bool(index),
        'strategy_labels':{'milvus_bm25':'Milvus BM25' if index else '本机 BM25','milvus_e5':'Milvus E5' if index else '本机 E5'},
        'prepare_ms':prepare_ms,'metrics':metrics,'rows':rows,
        'notes':['Hit@3按每个分问的前三名合并后要求覆盖所有问点；NDCG按去重合并列表计算。',
                 '无有效原文标签的问题单列，不放入有原文题的覆盖率分母。原始候选返回不等于业务答复承诺。',
                 '没有独立交叉编码器；窗口排序仍使用E5。','噪声范围复制相似条款，只验证范围隔离，不计作新增原创条款。']}
    output=args.out or ROOT/'.local/evaluations'/('policy-pipeline-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'.json')
    output.parent.mkdir(parents=True,exist_ok=True)
    with output.open('x',encoding='utf-8') as handle:
        json.dump(report,handle,ensure_ascii=False,indent=2)
    print(json.dumps({key:value for key,value in report.items() if key!='rows'},ensure_ascii=False,indent=2))
    print(output)


if __name__=='__main__':
    main()
