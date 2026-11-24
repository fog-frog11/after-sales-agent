"""使用 Milvus REST API 导入政策条款并执行中文 BM25 检索。"""

import json
from copy import deepcopy
from urllib.error import URLError
from urllib.request import Request, urlopen

from app.policies.evidence import PolicyEvidencePlan, PolicyIndexError, PolicySource, plan_evidence
from app.policies.jd_reference import JD_REFERENCE_SCOPE, JD_REFERENCE_SOURCES


COLLECTION = "after_sales_policy_articles"
# Milvus 将原文转成用于 BM25 排名的稀疏向量。
SCHEMA = {
    "autoId": False,
    "enabledDynamicField": False,
    "fields": [
        {"fieldName": "id", "dataType": "VarChar", "isPrimary": True, "elementTypeParams": {"max_length": 30}},
        {"fieldName": "shop_id", "dataType": "VarChar", "elementTypeParams": {"max_length": 60}},
        {"fieldName": "title", "dataType": "VarChar", "elementTypeParams": {"max_length": 255}},
        {"fieldName": "source", "dataType": "VarChar", "elementTypeParams": {"max_length": 255}},
        {
            "fieldName": "text", "dataType": "VarChar",
            "elementTypeParams": {
                "max_length": 4096, "enable_analyzer": True,
                "analyzer_params": json.dumps({"type": "chinese"}),
            },
        },
        {"fieldName": "sparse", "dataType": "SparseFloatVector"},
    ],
    "functions": [{
        "name": "policy_bm25", "type": "BM25", "inputFieldNames": ["text"],
        "outputFieldNames": ["sparse"], "params": {},
    }],
}


class MilvusPolicies:
    """管理本项目的条款索引和按店铺查询。"""

    def __init__(self, base_url: str, *, collection_name: str = COLLECTION, analyzer: str = "chinese"):
        """保存本机 Milvus 地址，启动时无需提前联网。"""
        self.base_url = base_url.rstrip("/")
        self.collection_name = collection_name
        self.analyzer = analyzer

    def request(self, path: str, payload: dict, timeout: int = 8):
        """发出一条 REST 请求，将连接和协议错误转成索引错误。"""
        request = Request(
            f"{self.base_url}/v2/vectordb/{path}",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json", "Request-Timeout": str(timeout)},
        )
        try:
            with urlopen(request, timeout=timeout + 1) as response:
                result = json.load(response)
            if result["code"] == 0:
                return result.get("data")
        except (URLError, TimeoutError, OSError, ValueError, TypeError, KeyError):
            raise PolicyIndexError(f"暂时无法连接 Milvus 政策索引（{path}）。") from None
        raise PolicyIndexError(f"Milvus 操作失败（{path}，错误码 {result['code']}）。")

    def index_articles(self, articles: list[dict]) -> None:
        """创建集合与索引，使用条款编号重复导入，随后加载集合。"""
        target = {"collectionName": self.collection_name}
        if not self.request("collections/has", target)["has"]:
            schema = deepcopy(SCHEMA)
            text_field = next(field for field in schema["fields"] if field["fieldName"] == "text")
            text_field["elementTypeParams"]["analyzer_params"] = json.dumps({"type": self.analyzer})
            self.request("collections/create", {
                **target,
                "schema": schema,
                "indexParams": [{
                    "fieldName": "sparse", "indexName": "policy_bm25_idx", "metricType": "BM25",
                    "params": {"index_type": "SPARSE_INVERTED_INDEX"},
                }],
            }, timeout=30)
        for start in range(0, len(articles), 250):
            self.request("entities/upsert", {**target, "data": articles[start:start + 250]}, timeout=30)
        self.request("collections/load", target, timeout=30)

    @staticmethod
    def scope_filter(shop_id: str, source: str | tuple[str, ...] | None) -> str:
        """把店铺及适用的出处白名单编码为 Milvus 过滤条件。"""
        filters = [f"shop_id == {json.dumps(shop_id, ensure_ascii=False)}"]
        if isinstance(source, tuple):
            filters.append(f"source in {json.dumps(source, ensure_ascii=False)}")
        elif source is not None:
            filters.append(f"source == {json.dumps(source, ensure_ascii=False)}")
        return " and ".join(filters)

    def count_articles(self, shop_id: str, *, source: str | tuple[str, ...] | None = None) -> int:
        """读取指定范围内强一致的条款数，用于核对可检索数据。"""
        rows = self.request("entities/query", {
            "collectionName": self.collection_name,
            "filter": self.scope_filter(shop_id, source),
            "outputFields": ["count(*)"],
            "consistencyLevel": "Strong",
        }, timeout=30)
        return int(rows[0]["count(*)"])

    def search(self, shop_id: str, question: str, *, source: str | tuple[str, ...] | None = None,
               limit: int = 3, timeout: int = 8) -> list[PolicySource]:
        """先按店铺和可选出处过滤，查出供直接引用或融合的 BM25 候选。"""
        rows = self.request("entities/search", {
            "collectionName": self.collection_name,
            "data": [question],
            "annsField": "sparse",
            "filter": self.scope_filter(shop_id, source),
            "outputFields": ["shop_id", "title", "source", "text"],
            "limit": limit,
            "consistencyLevel": "Strong",
            "searchParams": {"metric_type": "BM25", "params": {}},
        }, timeout=timeout)
        allowed_sources = (source,) if isinstance(source, str) else source
        return [
            PolicySource(str(row["id"]), row["title"], row["source"], row["text"])
            for row in rows
            if row["shop_id"] == shop_id and (allowed_sources is None or row["source"] in allowed_sources)
        ]

    def search_evidence(self, shop_id: str, question: str, *,
                        source: str | tuple[str, ...] | None = None) -> PolicyEvidencePlan:
        """让整句和每个问点沿用订单可信范围，限制补充查询耗时。"""
        def find(text: str, limit: int, follow_up: bool) -> list[PolicySource]:
            """给多诉求补充查询设置独立的短超时。"""
            return self.search(shop_id, text, source=source, limit=limit, timeout=2 if follow_up else 8)

        return plan_evidence(question, find, expand_queries=True)

    def search_jd_reference(self, scope: str, question: str) -> PolicyEvidencePlan:
        """按数据库确认的经营渠道过滤国内订单公开条款。"""
        return self.search_evidence(JD_REFERENCE_SCOPE, question, source=JD_REFERENCE_SOURCES[scope])
