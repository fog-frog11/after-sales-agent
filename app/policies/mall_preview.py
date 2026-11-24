"""商家在本机对照买家正在使用的检索与 E5 混合召回。"""

import logging
import os
import sys
from pathlib import Path
from functools import lru_cache
from threading import Lock
from time import perf_counter

from app.policies.evidence import PolicySource
from app.policies.mall_citations import find_citation
from app.policies.mall_rank import BM25
from app.policies.retrieval_ranking import reciprocal_rank_fusion
from app.policies.semantic_terms import E5Encoder, SemanticTerms
from app.settings import ROOT
from app.mall.dialogue import checked_mall_citations, order_policy_sources


logger = logging.getLogger(__name__)
model_lock = Lock()


@lru_cache(maxsize=1)
def preview_encoder() -> E5Encoder:
    """商家首次请求混合试查时才加载本机模型。"""
    deps = str(ROOT / ".local/retrieval-deps")
    if deps not in sys.path:
        sys.path.append(deps)
    return E5Encoder(Path(os.environ.get("MALL_E5_MODEL_DIR") or ROOT / ".local/models/multilingual-e5-small"))


@lru_cache(maxsize=8)
def preview_index(shop_id: str, sku: str, articles: tuple[PolicySource, ...]) -> SemanticTerms:
    """只复用商品范围和所有条款原文均相同的向量。"""
    return SemanticTerms(preview_encoder(), list(articles), include_windows=False)


def compare_policy_preview(item: dict, question: str, hybrid: bool) -> dict:
    """沿用买家引用规则，返回当前生效内容和两条检索路径的结果。"""
    _, articles = order_policy_sources(item)
    started = perf_counter()
    answer, evidence, _ = checked_mall_citations(item, question)
    baseline = {"answer": answer, "evidence": evidence, "ms": round((perf_counter() - started) * 1000, 2)}
    data = {"edition": item["terms_version"], "sku": item["sku"],
            "candidate_count": len(articles), "lexical": baseline}
    if not hybrid:
        return data
    if len(articles) > 160 or not model_lock.acquire(blocking=False):
        return {**data, "hybrid_status": "deferred"}
    try:
        started = perf_counter()
        semantic = preview_index(item["shop_id"], item["sku"], tuple(articles))
        prepared_at = perf_counter()
        words = BM25(articles)
        by_id = {article.article_id: article for article in articles}

        def search(aspect: str, _scoped: list[PolicySource]) -> tuple[str, PolicySource | None]:
            """融合已限定范围里的稀疏、稠密名次，再检查原文。"""
            dense = semantic.dense_ranking(semantic.query_vector(aspect), words.limit)
            keys = reciprocal_rank_fusion(words.rank(aspect), dense)[:words.limit]
            return find_citation(aspect, [by_id[key] for key in keys])

        answer, evidence, _ = checked_mall_citations(item, question, rank=search)
        return {**data, "hybrid_status": "ok", "hybrid": {"answer": answer, "evidence": evidence,
                "prepare_ms": round((prepared_at - started) * 1000, 2),
                "ms": round((perf_counter() - prepared_at) * 1000, 2)}}
    except Exception as error:
        # 模型或依赖缺失时仍返回规则路径结果，日志不记录买家的问题和正文。
        logger.warning("商城条款试查模型不可用：%s", type(error).__name__)
        return {**data, "hybrid_status": "unavailable"}
    finally:
        model_lock.release()
