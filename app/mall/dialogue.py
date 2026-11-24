"""解析商品咨询中的省略问句，并把每个结论指回下单条款或业务记录。"""

import re
from collections.abc import Callable

from app.policies.evidence import PolicySource, split_aspects
from app.policies.mall_citations import LOOKUP_PREFIX, find_citation, needs_citation_check
from app.policies.mall_rank import BM25

from app.mall.rules import policy_clauses


SUBJECTS = {
    "product": "购买明细", "logistics": "原包裹物流", "freight": "寄回运费",
    "original_shipping": "原配送费", "refund": "商品退款", "progress": "售后进度",
    "offer": "店铺方案", "return": "寄回信息", "outbound": "售后包裹", "platform": "平台介入",
    "eligibility": "办理资格", "photos": "拍照举证", "return_window": "退换期限",
    "warranty": "维修期限", "response_time": "店铺响应期限", "shop_guidance": "本店办理指引",
    "shop_current": "店铺现行承诺", "platform_floor": "平台最低保障",
}
SOURCE_NAMES = {"window": "退换期限", "service": "维修与未收货售后", "freight": "寄回运费", "response": "店铺响应与平台介入"}
POLICY_TOPICS = {"return_window": "window", "warranty": "service", "response_time": "response"}
RECORDS = {"product": "下单时的商品明细", "logistics": "原包裹的物流记录", "original_shipping": "下单时的配送费结算记录",
           "refund": "这件商品的售后单和退款登记", "progress": "售后单的处理记录", "offer": "售后单的协商记录",
           "return": "售后单的寄回记录", "outbound": "店铺寄出的售后包裹记录"}
CITATION_PROMPTS = ["查本单条款：退货运费谁承担？", "查本单条款：退货申请期限是多久？", "查本单条款：缺件能补发吗？"]


def resolve_citation_query(question: str, topics: list[str], context: dict) -> str | None:
    """保留尚未获得依据的具体承诺，避免下一轮错误引用其他话题。"""
    if needs_citation_check(question):
        return question
    if "basis" in topics and not any(topic in SUBJECTS for topic in topics):
        return context.get("citation_query")
    return None


def other_query_parts(question: str) -> list[str]:
    """额外承诺与已明确的业务问题一起提出时，保留其他问点。"""
    if LOOKUP_PREFIX.search(question.strip()):
        return []
    parts = split_aspects(question)
    return [part for part in parts if not needs_citation_check(part)] if any(
        needs_citation_check(part) for part in parts
    ) else []


def order_policy_sources(item: dict) -> tuple[str, list[PolicySource]]:
    """用同一份基础条款及审核记录构造购买范围内的候选。"""
    source = f"下单条款 · 第 {item['terms_version']} 版"
    clauses = [PolicySource(key, SOURCE_NAMES.get(key, ""), source, text)
               for key, text in policy_clauses(item).items() if text]
    clauses.extend(supplement_sources(item))
    if guide := item.get("missing_supplement"):
        clauses.append(PolicySource("missing_guide", "缺件补发说明", f"店铺已审核说明 #{guide['id']}", guide["note"]))
    return source, clauses


def checked_mall_citations(item: dict, question: str, *,
                           rank: Callable[[str, list[PolicySource]], tuple[str, PolicySource | None]] | None = None,
                           assessment: list | None = None
                           ) -> tuple[str, list[dict], list[str]]:
    """只查订单快照与当时已审核的补发说明，逐项返回确有依据的原文。"""
    query = LOOKUP_PREFIX.sub("", question.strip()).strip()
    # 模型改写可能用顿号连接两个独立问句；保留普通商品/凭证名词列表。
    query = re.sub(r'((?:是否|能否|谁)[^、？?]{1,30})、(?=[^、？?]{1,20}(?:谁|是否|能否|如何|多少|怎么))',r'\1？',query)
    source, clauses = order_policy_sources(item)
    messages, evidence, choices = [], {}, []
    for aspect in split_aspects(query) or [query]:
        label, match = (rank or ranked_citation)(aspect, clauses)
        if match and label in ("退换或申请期限", "寄回运费", "平台介入"):
            # 资格、运费和平台处理权限以基础条款为准。
            _, match = find_citation(aspect, [clause for clause in clauses if clause.source == source])
        if assessment is not None:
            assessment.append({'question':aspect,'label':label,'supported':match is not None,
                               'article_id':match.article_id if match else None})
        if match:
            evidence[match.article_id] = {"id": match.article_id, "text": match.text, "source": match.source}
            messages.append(f"「{label}」找到了本单的条款原文。")
        elif label == "clarify":
            messages.append("你想查哪一项条款？可以试试寄回运费、售后期限或店铺的补发说明。")
            choices = CITATION_PROMPTS
        else:
            messages.append(f"我查了这笔订单的下单条款，目前找不到能核实「{label}」的原文。可以请店铺确认。")
    return "\n".join(dict.fromkeys(messages)), list(evidence.values()), choices


def supplement_sources(item: dict, topic: str | None = None) -> list[PolicySource]:
    """把购买时适用的已审补充条款转换成可引用的来源。"""
    return [PolicySource(f"supplement-{row['id']}", row["title"], f"店铺已审核条款 #{row['id']}", row["note"])
            for row in item.get("supplements", []) if row["title"] and (topic is None or row["topic"] == topic)]


def ranked_citation(question: str, clauses: list[PolicySource]) -> tuple[str, PolicySource | None]:
    """先在适用条款里召回，再逐条检查原文是否足以引用。"""
    index = {clause.article_id: clause for clause in clauses}
    return find_citation(question, [index[key] for key in BM25(clauses).rank(question)])
AMOUNTS = {"freight", "original_shipping", "refund", "product", "offer"}
EXPLAIN = re.compile(r"凭什么|依据|根据(?:什么|哪)|哪(?:一)?条(?:款|规定|规则)|哪(?:一)?条[？?呢呀。]*$|条款原文|怎么(?:算出|得出|知道)的|为什么.{0,7}(?:我[出付]|自己[出付]|自付|不能退|不让退)|^(?:那|这是)?(?:为啥|为什么|怎么算的|怎么算出来的)[呢呀啊吗？?。]*$")
BARE_AMOUNT = re.compile(r"^(?:那|所以|到底|具体|一共|总共|大概|要|是|得|我|还要|合计)*(?:多少(?:钱|块)?|几块钱)[呢呀啊吗？?。]*$")
TERM_INTENTS = (
    ("warranty", re.compile(r"保修|质保|保多久|维修期")),
    ("response_time", re.compile(r"(?:店铺|店家|商家|卖家|客服).{0,8}(?:多久|多长时间|几小时).{0,6}(?:回|处理)|(?:多久|几小时).{0,4}(?:回复|响应)|响应期")),
    ("return_window", re.compile(r"(?:退换|退货|无理由).{0,5}(?:几天|多少天|期限)|(?:几天|多少天|多久).{0,5}(?:内.{0,2})?(?:能退|可退|可以退)|退换期限")),
)
POLICY_MENU = ["退换期限是几天？", "这件能保修多久？", "店铺多久回复？"]


def dialogue_topics(question: str) -> list[str]:
    """识别用户要求的回答方式和可由购买条款直接回答的问题。"""
    text = question.strip()
    topics = [topic for topic, pattern in TERM_INTENTS if pattern.search(text)]
    if re.search(r"(?:现在|现行|新版|最新|新下单|新订单|新买).{0,12}(?:条款|规则|保障|运费|退货|退换|维修|保修|寄回)|(?:店铺|本店).{0,5}(?:新规|现行承诺)", text):
        topics.append("shop_current")
    if re.search(r"平台.{0,6}(?:底线|最低|保障|规则|条款|规定)", text):
        topics.append("platform_floor")
    if re.search(r"(?:寄回|退货).{0,4}怎么包|(?:店铺|本店|商家).{0,6}(?:指引|怎么拍|举证建议)", text):
        topics.append("shop_guidance")
    if EXPLAIN.search(text):
        topics.append("basis")
    if BARE_AMOUNT.fullmatch(text):
        topics.append("amount_followup")
    if re.fullmatch(r"(?:(?:那|我|现在|然后|接下来|下一步|该|要|应该)*(?:怎么办|怎么做|做什么|干什么|怎么弄)|(?:接下来|下一步|然后)呢?)[啊呢？?。]*", text):
        topics.append("next_step")
    if re.search(r"(?:有哪些|有什么|看看|看下|查看|介绍).{0,6}(?:售后条款|售后规则|售后政策)|(?:条款|规则).{0,3}有哪些", text):
        topics.append("policy_menu")
    return topics


def last_subjects(context: dict, last: dict) -> list[str]:
    """读取本商品最近的话题，并兼容旧版已保存、尚无 focus 字段的回复。"""
    if "focus" in context:
        return [key for key in context["focus"] if key in SUBJECTS]
    topics = [key for key in last.get("topics", []) if key in SUBJECTS]
    if not topics and last.get("freight"):
        return ["freight"]
    if not topics and last.get("eligibility"):
        return ["eligibility"]
    return topics


def scoped_term(item: dict, topic: str, reason: str | None) -> str:
    """根据该商品参与范围和该店下单版本答复期限。"""
    if topic == "warranty":
        return (f"这件商品支持维修，期限是签收后 {item['warranty_days']} 天。"
                if item["repairable"] else "这件商品下单时没有约定维修服务。")
    if topic == "response_time":
        return f"店铺办理这笔售后的响应期限是 {item['reply_hours']} 小时。"
    if reason in ("quality", "wrong_item", "missing"):
        return f"质量问题、发错或少件，可在签收后 {item['fault_days']} 天内申请。"
    return (f"这件商品支持签收后 {item['return_days']} 天内无理由退换。"
            if item["change_of_mind"] else "这件商品下单时没有约定无理由退换。")


def published_answer(policy: dict, scope: list[str], facets: list[str], purchased: int, cite: bool) -> tuple[str, list[dict]]:
    """分别回答平台、现行店铺和旧单的适用范围；询问出处时只附相关部分。"""
    messages, evidence = [], []
    if "platform_floor" in scope:
        floor = policy["floor"]
        facts = {"return_window": f"参与无理由退换的商品至少 {floor['return_days']} 天",
                 "fault": f"质量售后至少 {floor['fault_days']} 天",
                 "response_time": f"店铺须在 {floor['reply_hours']} 小时内响应",
                 "warranty": f"支持维修的商品至少保障 {floor['warranty_days']} 天",
                 "freight": f"无理由寄回报价最高 ¥{floor['max_postage']:.2f}"}
        selected = [facts[key] for key in facets if key in facts]
        messages.append(f"平台第 {floor['version']} 版要求：" + "，".join(selected or [facts["return_window"], facts["fault"], facts["response_time"]]) + "。")
    if "shop_current" in scope:
        facts = {"freight": f"寄回报价 ¥{policy['return_postage']:.2f}，每款商品至少有 ¥{policy['item_credit']:.2f} 寄回权益",
                 "warranty": f"支持维修的商品保障 {policy['warranty_days']} 天", "response_time": f"店铺在 {policy['reply_hours']} 小时内响应",
                 "return_window": f"参与无理由退换的商品可在签收后 {policy['return_days']} 天内申请",
                 "shop_guidance": policy["guidance"].rstrip("。")}
        chosen = [key for key in facets if key in facts] or ["return_window", "freight"]
        messages.append(f"店铺第 {policy['version']} 版约定：" + "，".join(facts[key] for key in chosen) + "。")
        if purchased != policy["version"]:
            messages.append(f"你这单下单时用的是第 {purchased} 版。")
        if cite:
            paragraphs = policy_clauses(policy)
            for key in chosen:
                clause_id = POLICY_TOPICS.get(key, "guidance" if key == "shop_guidance" else "freight")
                evidence.append({"id": clause_id, "text": paragraphs[clause_id].split("。")[0] + "。"})
                if key == "freight":
                    evidence.append({"id": "credit", "text": paragraphs["credit"]})
    return "".join(messages), evidence


def explain_source(item: dict, subjects: list[str], reason: str | None, case: dict | None) -> tuple[str, list[dict]]:
    """按用户追问的事项挑选原句；业务进度和举证建议分别说明来源。"""
    if not subjects:
        return "你想核对哪一项的依据？比如寄回运费、退换期限，或退款进度。", []
    fragments, citations = [], {}
    for subject in subjects:
        if subject in RECORDS or subject == "freight" and case and case.get("refund_summary"):
            source = RECORDS.get(subject, "售后单的寄回运费明细")
            fragments.append(f"{SUBJECTS[subject]}查的是{source}。")
            continue
        if subject in ("photos", "shop_guidance"):
            if item.get("guidance"):
                citations["guidance"] = {"id": "guidance", "text": item["guidance"]}
                continue
            fragments.append("拍照是为了让店铺看清外箱和商品的受损情况，属于举证建议。")
            continue
        key = POLICY_TOPICS.get(subject)
        if subject == "platform":
            key = "response"
        elif subject == "freight":
            if item["stage"] == "in_transit":
                fragments.append("这版条款没有给拦截或拒收定价，费用要由店铺向承运方核实。")
                continue
            key = "freight"
        elif subject == "eligibility":
            if case:
                fragments.append(f"这件商品已有售后单 #{case['case_no']}，办理进度可在原单查看。")
                continue
            if item["stage"] == "in_transit" and reason in ("wrong_purchase", "cancel"):
                fragments.append("在途退回根据当前包裹状态办理，需要店铺核实拦截或拒收结果。")
                continue
            key = "window" if item["stage"] == "delivered" else "service"
        if not key or key in citations:
            continue
        paragraph = policy_clauses(item)[key]
        sentences = [part + "。" for part in paragraph.split("。") if part]
        index = 0
        if key == "freight" and reason in ("quality", "wrong_item") or key == "window" and reason in ("quality", "wrong_item", "missing"):
            index = 1
        if key == "service" and subject != "warranty":
            index = 1
        if key == "response" and subject == "platform":
            index = 1
        excerpt = sentences[index]
        if key == "freight" and reason not in ("wrong_purchase", "quality", "wrong_item"):
            excerpt = paragraph
        citations[key] = {"id": key, "text": excerpt}
        fragments.append(f"「{SOURCE_NAMES[key]}」")
    if citations:
        names = "、".join(f"「{SOURCE_NAMES.get(key, '本店办理指引')}」" for key in citations)
        records = [part for part in fragments if not part.startswith("「")]
        return ("".join(records) + f"依据是《{item['terms_title']}》第 {item['terms_version']} 版的{names}。"), list(citations.values())
    return "".join(fragments), []
