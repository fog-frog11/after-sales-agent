"""判断已确认店铺订单里需要查期限、订单资料或售后条款的事项。"""

import re

from app.integrations.deepseek import ORDER_ID_PATTERN
from app.orders.dialogue_tools import explicit_order_operations
from app.orders.logistics import HISTORICAL_ORDER_PATTERN
from app.orders.olist_items import ITEM_IDENTITY_QUESTION, ORDER_DETAILS_QUESTION
from app.policies.evidence import split_aspects


DEADLINE = re.compile(
    r"退(?:换)?货(?:期限|时限)|超(?:出|过)?.{0,4}(?:期限|[七7]天|十五天|15天)|"
    r"(?:多久|几天|多少天).{0,8}退(?![款钱费])|退货.{0,3}(?:还有|剩).{0,4}(?:多久|几天|多少天)|"
    r"(?:还能|还可以|可以|能不能|可不可以|能否|能)退(?:货)?(?:吗|么|[。？?！!，,]|$)"
)
CLAUSE_DETAIL = re.compile(
    r"运费|邮费|快递费|到付|取件|照片|拍照|拍[哪啥什]|凭证|破损|压[坏扁]|包装|"
    r"退款|发票|赠品|配件|换货|质保|保修|维修|上门|怎么退|如何退|步骤|手续|条件"
)
OLIST_RULE_QUESTION = re.compile(
    r"退货|退件|退换|退款|无理由|换货|退回|拒收|瑕疵|故障|质量|破损|包装|拍照|赔偿|运费险|保修|质保|维修|"
    r"发票|保价|补差价|赠品|货不对板|商品不符|投诉|售后条款|卖家不.{0,5}(?:发货|履约)|"
    r"(?:能否|可否|可以|能不能|能|想|申请).{0,6}取消|能.{0,3}退(?:吗|么|[？?]|$)|"
    r"(?:想要?|我要|申请|帮我)退(?=[，,。！!？?]|$)|寄回.{0,12}(?:钱|费用|运费|谁出|谁付)"
)
SUMMARY_ONLY = re.compile(
    r"(?:(?:请|帮我|麻烦|能否|能不能)?(?:查(?:询|找|看)?|看|核对)(?:一?下|看)?"
    r"(?:这笔|这一笔|这单|订单|的|一下)*|(?:切换|更换|改查|换)(?:到|成|为)?(?:这笔|这一笔|订单)?|"
    r"(?:这笔|这单|该)?(?:订单)?(?:资料|信息|状态|签收(?:日期|时间)?|哪天签收|签收了吗|申请人工复核|转人工|人工复核)"
    r"(?:呢|吗|么)?)"
)
PUNCTUATION = " ，,。；;：:？?！!（）()"


def order_question_text(text: str) -> str:
    """把订单编号从买家的实际问题中移出，避免影响条款分词。"""
    sanitized = ORDER_ID_PATTERN.sub(" ", HISTORICAL_ORDER_PATTERN.sub(" ", text))
    return " ".join(sanitized.split()).strip(PUNCTUATION)


def select_shop_question(question: str, reply: str | None) -> tuple[str | None, bool]:
    """条款问题走同一会话，订单卡仍保留原本的期限判定。"""
    parts = list(dict.fromkeys(
        text for raw in (question, reply) if raw and (text := order_question_text(raw))
        and text not in {"或", "和", "及", "还是", "或者"}
    ))
    if not parts:
        return None, True
    full_question = "？".join(parts)
    questions = split_aspects(full_question) or [full_question]
    clauses = []
    show_summary = False
    for item in questions:
        match = DEADLINE.search(item)
        summary_only = bool(SUMMARY_ONLY.fullmatch(item.replace(" ", "")) or ORDER_DETAILS_QUESTION.search(item))
        if match or summary_only:
            show_summary = True
        if ((not match and not summary_only and not ITEM_IDENTITY_QUESTION.search(item))
                or CLAUSE_DETAIL.search(DEADLINE.sub("", item))):
            clauses.append(item)
    return ("？".join(clauses)[:300] if clauses else None), show_summary


def split_fact_from_clause(text: str) -> tuple[str, str] | None:
    """只有逗号两侧分别是明确的查单与售后诉求时，才拆开。"""
    for separator in re.finditer(r"[，,]", text):
        left, right = text[:separator.start()].strip(), text[separator.end():].strip()
        if explicit_order_operations(left) and OLIST_RULE_QUESTION.search(right):
            return right, left
        if explicit_order_operations(right) and OLIST_RULE_QUESTION.search(left):
            return left, right
    return None


def select_historical_questions(text: str) -> tuple[str, str]:
    """拆开公开售后规则问题和需要查订单、商品或人工进度的问题。"""
    question = order_question_text(text)
    rules, order = [], []
    for part in split_aspects(question) or [question]:
        if paired := split_fact_from_clause(part):
            rule, fact = paired
            rules.append(rule)
            order.append(fact)
        elif OLIST_RULE_QUESTION.search(part) or DEADLINE.search(part):
            rules.append(part)
        else:
            order.append(part)
    return "？".join(rules)[:300], "？".join(order)
