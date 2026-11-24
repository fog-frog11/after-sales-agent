"""在同一订单范围内为多个明确诉求分配条款。"""

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from itertools import zip_longest


class PolicyIndexError(Exception):
    """Milvus 条款索引暂时不可读。"""


@dataclass(frozen=True)
class PolicySource:
    """一段可展示的条款原文及其手册出处。"""

    article_id: str
    title: str
    source: str
    text: str


@dataclass(frozen=True)
class PolicyAspect:
    """一个明确提出的诉求及其可引用的条款编号。"""

    question: str
    article_ids: list[str]


@dataclass(frozen=True)
class PolicyEvidencePlan:
    """一次检索的去重条款和可选的逐项证据分配。"""

    sources: list[PolicySource]
    aspects: list[PolicyAspect] = field(default_factory=list)


PolicyPlanLookup = Callable[[str, str], PolicyEvidencePlan]
BOUNDARY = re.compile(
    r"[？?；;]+|(?<=[吗么])[，,]|[，,。！!]\s*(?:另外|还有|顺便|其次|以及|再问(?:一下|下)?|还想问)"
)
FOLLOW_UP = re.compile(r"^(?:另外|还有|顺便|其次|以及|再问(?:一下|下)?|还想问)\s*[，,：:]?\s*")
CONTEXT_END = re.compile(r"(?:如果|要是|假如|只要).{0,80}$")
SPOKEN_TERMS = ((re.compile(r"到付"), "货到付款"), (re.compile(r"谁.{0,4}证明|谁.{0,4}证据|拿出证据"), "举证责任"))
RETURN_CONTEXT = re.compile(r"退[货件换]|寄回|退的时候|[一同]起.{0,3}寄|退回|返还")
GIFT_TERMS = re.compile(r"赠品|附送|附带|配件|随单送|随附|搭送|赠送|送的|加赠")
SHIPPING_TERMS = re.compile(r"运费|物流费|快递费|寄回.{0,6}费|寄.{0,5}(?:费用|花的钱)")
AGREEMENT_TERMS = re.compile(r"约定|说好|商量好|同意承担|谈妥|讲好|协商")
REFUND_METHOD = re.compile(r"原路退|改退|退成|退款.{0,14}(?:换|方式|渠道|退到)|退[到成回].{0,6}(?:券|支付账户)")
ALTERNATE_RECIPIENT = re.compile(r"代签|代收|替我签收|帮我签收")


def split_aspects(question: str) -> list[str]:
    """只识别明确分开的两至三个中文问点，保留条件句。"""
    parts = [FOLLOW_UP.sub("", part.strip(" ，,。；;？?")).strip() for part in BOUNDARY.split(question)]
    parts = list(dict.fromkeys(part for part in parts if part))
    if not 2 <= len(parts) <= 3 or any(len(part) < 4 for part in parts):
        return []
    # 分号前的条件短句有时修饰后面的问法，整体交给原流程。
    if any(CONTEXT_END.match(part) and not re.search(r"[,，]|[吗么如何]", part) for part in parts[:-1]):
        return []
    return parts


def query_aspect(part: str, *, context: str = "") -> str:
    """为少量明确的口语词补上手册惯用术语。"""
    additions = [term for pattern, term in SPOKEN_TERMS if pattern.search(part) and term not in part]
    if "到付" in part and re.search(r"退货|退换|寄回|寄给", context):
        additions.extend(["退换货", "寄回", "运费"])
    return " ".join([part, *additions])


def expand_policy_query(query: str, context: str) -> str:
    """把明确意图的口语补成手册用语，原有问题文本仍用于生成答复。"""
    additions = []
    if GIFT_TERMS.search(query) and RETURN_CONTEXT.search(context):
        additions.extend(["赠品", "配件", "一并退回", "返还"])
    if AGREEMENT_TERMS.search(query) and (SHIPPING_TERMS.search(context)
                                         or (RETURN_CONTEXT.search(context) and re.search(r"费用|承担", query))):
        additions.extend(["运费纠纷", "协商一致", "运费承担"])
    if REFUND_METHOD.search(query):
        additions.extend(["原路退款", "支付方式", "退款方式"])
    if ALTERNATE_RECIPIENT.search(query):
        additions.extend(["签收规范", "收货人信息", "他人作为收货人"])
    return " ".join([query, *(term for term in additions if term not in query)])


def plan_evidence(question: str, search: Callable[[str, int, bool], list[PolicySource]], *,
                  expand_queries: bool = False) -> PolicyEvidencePlan:
    """保护整句前三条，多问再轮流补充局部依据并限制总量。"""
    parts = split_aspects(question)
    whole_query = expand_policy_query(question, question) if expand_queries and not parts else question
    whole = search(whole_query, 5 if parts else 3, False)
    if not parts:
        return PolicyEvidencePlan(whole)
    try:
        queries = [query_aspect(part, context=question) for part in parts]
        if expand_queries:
            queries = [expand_policy_query(query, question) for query in queries]
        groups = [search(query, 2, True) for query in queries]
    except PolicyIndexError:
        return PolicyEvidencePlan(whole[:3])
    ordered = {entry.article_id: entry for entry in whole[:3]}
    for choices in zip_longest(*groups):
        for entry in choices:
            if entry is not None:
                ordered.setdefault(entry.article_id, entry)
    for entry in whole:
        ordered.setdefault(entry.article_id, entry)
    selected = list(ordered.values())[:5]
    allowed = {entry.article_id for entry in selected}
    shared = [entry.article_id for entry in whole[:3]]
    aspects = [
        PolicyAspect(question=part, article_ids=list(dict.fromkeys(
            [entry.article_id for entry in hits if entry.article_id in allowed] + shared
        )))
        for part, hits in zip(parts, groups, strict=True)
    ]
    return PolicyEvidencePlan(selected, aspects) if selected else PolicyEvidencePlan([])
