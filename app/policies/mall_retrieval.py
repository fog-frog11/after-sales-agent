"""在订单适用条款内融合 Milvus BM25 与 E5 语义召回，并排序证据窗口。"""

import hashlib
import json
import logging
from threading import RLock
from time import monotonic, perf_counter

from app.mall.dialogue import checked_mall_citations, order_policy_sources
from app.policies.mall_citations import find_citation
from app.policies.mall_preview import preview_encoder
from app.policies.mall_rank import BM25
from app.policies.retrieval_ranking import reciprocal_rank_fusion
from app.policies.semantic_terms import SemanticTerms
from app.storage.milvus_policies import MilvusPolicies
from app.storage.milvus_mall_hybrid import MilvusMallHybrid


logger = logging.getLogger(__name__)


def scope_key(item: dict) -> str:
    """店铺、商品与条款内容共同确定不可混用的检索快照。"""
    _, articles = order_policy_sources(item)
    body = json.dumps([item["shop_id"], item["sku"], item["terms_version"],
                       [(a.article_id, a.source, a.text) for a in articles]], ensure_ascii=False)
    return hashlib.sha256(body.encode()).hexdigest()[:40]


class MallRetrieval:
    """复用已有模型和 Milvus 适配器，原文仍取自当前订单的可信候选。"""

    def __init__(self, url: str, *, persistent_dense: bool = False, window_rerank: bool = False):
        self.index = MilvusPolicies(url, collection_name="mall_order_policy_evidence_v3") if url else None
        self.hybrid_index=MilvusMallHybrid(url) if url and persistent_dense else None
        self.hybrid_ready={}
        self.window_rerank=window_rerank
        self.cache = {}
        self.retry_after = {}
        self.lock = RLock()

    def search(self, item: dict, question: str) -> dict:
        """先约束范围，稀疏和稠密双路召回后融合、证据窗口排序和引用核验。"""
        started=perf_counter()
        query_times=[]
        key = scope_key(item)
        _, articles = order_policy_sources(item)
        by_id = {article.article_id: article for article in articles}
        words = BM25(articles)
        routes = ["scoped_bm25"]
        semantic = None
        with self.lock:
            cached = self.cache.get(key)
            retry = monotonic() >= self.retry_after.get(key, 0)
            if cached is None or retry and (not cached[0] and self.index or cached[1] is None
                    or self.hybrid_index and cached[1] is not None and not self.hybrid_ready.get(key)):
                ready, semantic = cached or (False, None)
                if semantic is None:
                    try:
                        semantic = SemanticTerms(preview_encoder(), articles,include_windows=self.window_rerank)
                    except Exception as error:
                        logger.warning("商城语义模型降级：%s", type(error).__name__)
                rows=[{"id":hashlib.sha256((key+a.article_id).encode()).hexdigest()[:30],
                       "shop_id":key,"title":a.title,"source":a.article_id,"text":a.text} for a in articles]
                if semantic is not None and self.hybrid_index and not self.hybrid_ready.get(key):
                    try:
                        self.hybrid_index.index_vectors(rows,semantic.article_vectors)
                        self.hybrid_ready[key]=True
                        ready=True
                    except Exception as error:
                        logger.warning('商城双路索引降级：%s',type(error).__name__)
                if self.index and not ready:
                    try:
                        self.index.index_articles(rows)
                        ready=True
                    except Exception as error:
                        logger.warning('商城Milvus索引降级：%s',type(error).__name__)
                cached = (ready, semantic)
                if key not in self.cache and len(self.cache) >= 16:
                    oldest = next(iter(self.cache))
                    self.cache.pop(oldest)
                    self.retry_after.pop(oldest, None)
                    self.hybrid_ready.pop(oldest,None)
                self.cache[key] = cached
                self.retry_after[key] = monotonic() + 30
            ready, semantic = cached

        def rank(aspect: str, scoped: list) -> tuple:
            """Milvus 返回的编号必须再次属于数据库快照。"""
            query_started=perf_counter()
            local_sparse = words.rank(aspect)
            sparse = None
            if ready:
                try:
                    index=self.hybrid_index if self.hybrid_ready.get(key) else self.index
                    hits = index.search(key, aspect, limit=12, timeout=3)
                    accepted = [hit.source for hit in hits if hit.source in by_id and hit.text == by_id[hit.source].text]
                    if accepted:
                        sparse = accepted
                    if "milvus_bm25" not in routes:
                        routes.append("milvus_bm25")
                except Exception as error:
                    logger.warning("商城 Milvus 查询降级：%s", type(error).__name__)
            ranking = reciprocal_rank_fusion(local_sparse,sparse) if sparse else local_sparse
            if semantic:
                with self.lock:
                    vector = semantic.query_vector(aspect)
                    dense = semantic.dense_ranking(vector, 12)
                    if self.hybrid_ready.get(key):
                        try:
                            hits=self.hybrid_index.search_dense(key,vector,limit=12)
                            accepted=[hit.source for hit in hits if hit.source in by_id and hit.text==by_id[hit.source].text]
                            if accepted:
                                dense=accepted
                            if 'milvus_dense' not in routes:
                                routes.append('milvus_dense')
                        except Exception as error:
                            logger.warning('商城稠密查询降级：%s',type(error).__name__)
                    fused = reciprocal_rank_fusion(local_sparse,sparse,dense)[:12] if sparse else reciprocal_rank_fusion(local_sparse,dense)[:12]
                    ranking = reciprocal_rank_fusion(fused,semantic.window_ranking(vector,fused)) if self.window_rerank else fused
                if "e5_dense" not in routes:
                    routes.extend(["e5_dense", "rrf"]+(["evidence_window_rerank","rrf_prior"] if self.window_rerank else []))
            result=find_citation(aspect, [by_id[article_id] for article_id in ranking])
            query_times.append({'question':aspect,'elapsed_ms':round((perf_counter()-query_started)*1000,2)})
            return result

        assessment=[]
        answer, evidence, choices = checked_mall_citations(item, question, rank=rank,assessment=assessment)
        supported=sum(row['supported'] for row in assessment)
        coverage='supported' if assessment and supported==len(assessment) else 'partial' if supported else 'unsupported'
        return {"scope": key, "answer": answer, "evidence": evidence, "quick_replies": choices,
                "routes": routes, "candidates": len(articles),'coverage':coverage,'aspects':assessment,
                'timings_ms':{'total':round((perf_counter()-started)*1000,2),'queries':query_times}}
