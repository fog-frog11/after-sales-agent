"""复核条款是否支持拟发送的客服结论，并校验逐字原文。"""

import re
from dataclasses import asdict

from app.integrations.deepseek import ModelError
from app.policies.search import PolicyClaim, PolicyReviewer, request_policy_json


RECEIVED_REFUND = re.compile(
    r"(?:退(?:款|回来的钱)|这笔钱|款项)(?:已(?:经)?|都|也)?(?:到[账帐]了|到了|收到了|一到手|回到|进了)"
    r"|到[账帐](?:之)?后|收到退款(?:之)?后|收到这笔退款(?:之)?后"
)
TRANSFER = re.compile(r"(?:转|划|汇)(?:给|入|进|到|至|出)|转账|充值")
REMAINING_REFUND = re.compile(r"补退|少退|退款差额|剩余.{0,6}退款")


def missing_post_refund_terms(claim: PolicyClaim) -> bool:
    """资金到账后的转移需要有覆盖到账后阶段的条款。"""
    received = RECEIVED_REFUND.search(claim.question)
    if (received is None or not TRANSFER.search(claim.question, received.end())
            or REMAINING_REFUND.search(claim.question)):
        return False
    return not any(RECEIVED_REFUND.search(source.text) and TRANSFER.search(source.text) for source in claim.sources)


def verbatim_evidence(row: dict, claim: PolicyClaim) -> bool:
    """校验每条已引用原文都有对应摘录，允许原文换行和空格差异。"""
    evidence = row.get("evidence")
    if row.get("decision") != "supported" or not isinstance(evidence, list):
        return False
    sources = {source.article_id: "".join(source.text.split()) for source in claim.sources}
    verified = set()
    for excerpt in evidence:
        if not isinstance(excerpt, dict):
            return False
        article, quote = excerpt.get("article_id"), excerpt.get("quote")
        if not isinstance(article, str) or article not in sources or not isinstance(quote, str):
            return False
        normalized = "".join(quote.split())
        if not normalized or normalized not in sources[article]:
            return False
        verified.add(article)
    return bool(verified) and verified == sources.keys()


def deepseek_policy_reviewer(api_key: str) -> PolicyReviewer:
    """为每条用户消息批量复核已经生成的答复。"""
    def review(question: str, claims: list[PolicyClaim]) -> set[int]:
        """比较用户想做的事与条款，再验证模型列出的原文片段。"""
        claims = [claim for claim in claims if not missing_post_refund_terms(claim)]
        if not claims:
            return set()
        result = request_policy_json(api_key, (
            "你是独立的售后条款复核员。输入包含原问题和待复核的若干项。"
            "每项都有单独的问题、拟发送答复和实际引用的条款。待复核答复是草稿。"
            "先判断用户现在要做的动作、当前业务阶段、操作对象或主体。"
            "只有原文明确覆盖该动作、阶段和对象，而且答复的实质结论均可从原文推出，才标 supported。"
            "还没办理与已办理完毕后是不同阶段。发起退款与款项到账后的用途是不同动作。"
            "条款未约束该动作时，禁止和允许都没有依据。原文列出适用条件时，答复可以按条件说明。"
            "用原问题补全省略的指代，逐项判断，其他项的结论不能替代本项原文。"
            "输出 json 对象，字段 items 为数组，每一项格式为 "
            '{"number":1,"decision":"supported","evidence":[{"article_id":"编号","quote":"连续原文"}]}。'
            "支持时给出每条引用中能直接支撑结论的连续原文，逐字摘录，不加省略号或自行概括。"
            '不能支持时，decision 为 "needs_review"，evidence 为空数组。'
        ), {"original_question": question, "claims": [asdict(claim) for claim in claims]}, max_tokens=1200)
        items = result.get("items")
        if not isinstance(items, list):
            raise ModelError("DeepSeek 未返回有效的条款适用性校验结果。")
        submitted = {claim.number: claim for claim in claims}
        accepted = set()
        seen = set()
        for row in items:
            number = row.get("number") if isinstance(row, dict) else None
            if type(number) is not int or number not in submitted or number in seen:
                raise ModelError("DeepSeek 返回的条款复核事项编号无效。")
            seen.add(number)
            if verbatim_evidence(row, submitted[number]):
                accepted.add(number)
        return accepted

    return review
