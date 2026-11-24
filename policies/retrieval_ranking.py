"""融合独立检索路径，并用候选条款的局部证据调整顺序。"""

import re
from collections.abc import Sequence

from app.policies.search import PolicySource


def reciprocal_rank_fusion(*rankings: Sequence[str], k: int = 60,
                           weights: Sequence[float] | None = None) -> list[str]:
    """按倒数名次累加，每条路径对同一条款只贡献一次。"""
    scores: dict[str, float] = {}
    route_weights = (1.0,) * len(rankings) if weights is None else weights
    for ranking, weight in zip(rankings, route_weights, strict=True):
        for rank, article_id in enumerate(dict.fromkeys(ranking), start=1):
            scores[article_id] = scores.get(article_id, 0) + weight / (k + rank)
    return sorted(scores, key=scores.__getitem__, reverse=True)


def article_text(article: PolicySource) -> str:
    """索引时保留条款标题，避免公开语料的标题重复。"""
    body = article.text.strip()
    return body if body.startswith(f"{article.title}\n") else f"{article.title}\n{body}"


def evidence_windows(article: PolicySource, *, size: int = 400, overlap: int = 100) -> list[str]:
    """在正文中依次取重叠窗口，给每个窗口保留条款标题。"""
    if not 0 <= overlap < size:
        raise ValueError("重叠长度应小于证据窗口长度。")
    body = article.text.strip().removeprefix(f"{article.title}\n")
    body = re.sub(r"\s+", " ", body).strip()
    parts = []
    for start in range(0, len(body), size - overlap):
        parts.append(f"{article.title}\n{body[start:start + size]}".strip())
        if start + size >= len(body):
            break
    return parts or [article.title]
