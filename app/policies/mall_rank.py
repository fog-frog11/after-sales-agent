"""商城条款的轻量 BM25，支持有业务上下文的检索扩词。"""

import math
import re
from collections import Counter
from statistics import mean

from app.policies.evidence import PolicySource
from app.policies.retrieval_ranking import article_text, reciprocal_rank_fusion


TOKEN_RUN = re.compile(r"[\u3400-\u9fff]+|[a-z0-9]+")
HARDWARE = re.compile(r"螺[丝钉]|垫片|定位销|螺帽|螺母|五金")
MISSING = re.compile(r"没给|没发|缺|少|漏|补发|补寄")


def tokens(text: str) -> list[str]:
    """生成确定性的中文单字、相邻双字和字母数字词。"""
    result = []
    for run in TOKEN_RUN.findall(text.lower()):
        if run.isascii():
            result.append(run)
        else:
            result.extend(run)
            result.extend(run[i:i + 2] for i in range(len(run) - 1))
    return result


class BM25:
    """仅对调用方提供的适用条款建索引，以固定顺序打破同分。"""

    def __init__(self, articles: list[PolicySource], limit: int = 12, *, expand: bool = True):
        self.limit = limit
        self.expand = expand
        self.docs = {a.article_id: Counter(tokens(article_text(a))) for a in articles}
        self.lengths = {key: sum(body.values()) for key, body in self.docs.items()}
        self.average = mean(self.lengths.values()) if self.lengths else 1
        df = Counter(word for body in self.docs.values() for word in body)
        self.idf = {word: math.log1p((len(articles) - count + 0.5) / (count + 0.5))
                    for word, count in df.items()}

    def _rank(self, query: str) -> list[str]:
        """按词频和文档长度计算范围内的 BM25。"""
        query_terms = set(tokens(query))
        scores = {}
        for key, body in self.docs.items():
            norm = 1.2 * (0.25 + 0.75 * self.lengths[key] / self.average)
            score = sum(self.idf[word] * 2.2 * body[word] / (body[word] + norm)
                        for word in sorted(body.keys() & query_terms))
            if score:
                scores[key] = score
        return sorted(scores, key=scores.__getitem__, reverse=True)[:self.limit]

    def rank(self, query: str) -> list[str]:
        """仅在描述五金缺件时搜索品类用语，保留原问法的排序信号。"""
        original = self._rank(query)
        if self.expand and HARDWARE.search(query) and MISSING.search(query):
            expanded = self._rank(query + " 装配 小五金 缺件 部件编号")
            return reciprocal_rank_fusion(original, expanded)[: self.limit]
        return original
