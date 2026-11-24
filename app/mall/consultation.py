"""根据当前商品和上一轮已确认事实回答商城售后咨询。"""

import re
from datetime import date, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.policies.evidence import split_aspects
from app.policies.mall_citations import needs_citation_check

from app.mall.dialogue import (
    AMOUNTS, BARE_AMOUNT, POLICY_MENU, POLICY_TOPICS, SUBJECTS, dialogue_topics, explain_source,
    checked_mall_citations, last_subjects, other_query_parts, published_answer, ranked_citation,
    resolve_citation_query, scoped_term, supplement_sources,
)
from app.mall.rules import DELIVERY_PROBLEMS, KINDS, REASONS, RETURN_KINDS, Eligibility, assess_eligibility, freight_quote, identify_reason, policy_clauses


FREIGHT = re.compile("运费|邮费|配送费|垫付|谁出|寄回费|拦截费|拒收费")
ORIGINAL_SHIPPING = re.compile(r"原.{0,4}(?:配送费|运费|邮费)|配送费|发货运费|下单运费|首程运费")
RETURN_SHIPPING = re.compile(r"(?:退货|寄回|退回).{0,4}(?:运费|邮费|费用)|垫付|寄回费|拦截费|拒收费")
PACKAGING = re.compile(r"(?:外包装|外箱|外盒|包装|纸箱|盒子)[^，,。！!？?；;]{0,8}?(?:破|烂|压扁|压坏|瘪|损坏|进水|变形)")
PACKAGING_INTACT = re.compile(r"(?:外包装|外箱|外盒|包装|纸箱|盒子)[^，,。！!？?；;]{0,6}(?:没有破|没破|没坏|完好)")
ONLY_PACKAGING = re.compile(r"(?:只是|就是|只有)(?:外包装|外箱|外盒|包装|纸箱|盒子)")
UNDAMAGED = re.compile(r"(?:商品|里面|东西|本体).{0,8}(?:没坏|没有坏|没损坏|没破|完好|没事|没有损坏)")
ITEM_DAMAGE = re.compile(r"(?:商品|里面|东西|本体).{0,10}(?:坏|损坏|破|碎|裂|进水)|^(?:也坏了|坏了|也有损坏|有损坏)[。！]?$")
EMPTY_PACKAGE = re.compile(r"空(?:盒|箱|包裹)|只(?:有|收到)(?:一个)?(?:空盒|外盒|包装|纸箱)|(?:盒子?|箱子?|包裹)(?:里面|里)(?:什么都没有|没有商品|啥也没有)")
ISSUE_QUESTION = re.compile(r"怎么办|怎么处理|能不能|是否|[吗么]$")
EXPLICIT_LOGISTICS = re.compile(r"(?:查|看).{0,3}(?:物流|快递|运单)|(?:快递|物流|包裹|运单).{0,5}(?:到哪|走到|发出|进度|签收了吗)|原包裹")
MISSING_EVIDENCE = re.compile(
    r"(?:没(?:有)?(?:拍|留|录)?)(?:下|到)?(?:开箱|拆箱)?(?:照片|视频|凭证)"
    r"|(?:开箱|拆箱)(?:时|的时候)?没(?:有)?(?:拍|留|录)(?:照片|视频|凭证|像)"
    r"|(?:开箱|拆箱)(?:的)?(?:视频|录像|照片)(?:我)?(?:没拍|没录|没留|没有|找不到|丢了)"
    r"|(?:拆|开)(?:包裹|箱)(?:时|的时候)(?:忘了|忘记录|忘记)(?:拍|录)"
)
EVIDENCE_SUBMISSION = re.compile(r"^(?:那|所以)?(?:这样|这种情况|没(?:有)?材料)(?:还|也)?(?:可以|能)(?:提交|申请)(?:售后|吗|吧|么)*[？?！。]*$")
EVIDENCE_CHECKLIST = {
    "missing": ["外箱全貌和破损位置", "开箱后的箱内物品和包装", "面单与运单号，遮住手机号和地址"],
    "wrong_item": ["实收商品全貌", "商品型号或规格标签", "外包装及面单，遮住手机号和地址"],
    "quality": ["商品全貌与故障或损坏位置", "必要时附上外包装破损照片"],
}
PROGRESS = re.compile(r"进度|售后状态|退款状态|到哪步|处理到哪|谁处理|处理好|(?:售后|退款|退货|换货|维修).{0,8}到哪")
OFFER_QUERY = re.compile(r"(?:店铺|商家|卖家|协商|这个|当前|已确认).{0,8}方案|方案.{0,8}(?:接受|确认|是什么|怎样|金额)|"
                         r"(?:同意|接受).{0,8}(?:怎样|怎么样|怎么办|之后|以后|然后|会)")
REFUND_QUERY = re.compile(r"退了(?:吗|没|多少)|退多少|(?:钱|退款).{0,6}到账|退款金额|退款进度|退款状态|退款失败|什么时候退|有没有退款")
INTERVENTION_QUERY = re.compile(
    r"人工\s*(?:介[入进]|进[入来]|客服|复核)|(?:平台|官方).{0,5}(?:介[入进]|客服|仲裁|处理争议|复核)|"
    r"转\s*人工|找\s*人工|小二.{0,3}介入|申请.{0,4}(?:仲裁|复核)|怎么.{0,4}复核"
)
RETURN_EXECUTION = re.compile(r"退货地址|寄回地址|寄到哪|往哪寄|寄给谁|退到哪|怎么寄回|何时寄回|什么时候寄回|最晚.{0,4}寄|(?:寄回|退回).{0,3}期限|寄回运单|确认收件")
TRANSIT_RETURN_QUERY = re.compile(r"拦截|拒收")
OUTBOUND_EXECUTION = re.compile(
    r"售后包裹|新运单|新包裹|重新寄|重寄|再次发错|"
    r"(?:换货|补发|维修返还|修好的).{0,12}(?:运单|物流|单号|快递|发货|包裹|地址|收到|到哪|破损|发错|没发|坏了|寄了吗)|"
    r"(?:没收到|没有收到).{0,8}(?:换货|补发|维修返还)"
)
REPLACEMENT_PARCEL = re.compile(r"替换给我|换给我|换过来|换新的|补寄|重寄|重新寄|售后包裹|新包裹|新运单")
ORIGINAL_PARCEL = re.compile(r"原包裹|原订单|当初寄|下单时寄|第一次寄|两个包裹|两边包裹")
DELIVERY_DECISION_MESSAGES = {
    "platform_dispatch": "平台已要求店铺发出包裹。",
    "platform_reissue": "平台已要求店铺重寄，原运单和重寄进度都可在原单查看。",
    "platform_continue_delivery": "平台核对后决定继续跟踪本次运单。",
}
PHOTO_GUIDANCE = "可以拍外包装全貌、破损处近照和受损商品的照片。拍包裹标签时遮住手机号、住址等无关信息。"
PRODUCT_QUERY = re.compile(r"买的?(?:是)?(?:什么|啥)|买了(?:什么|啥|几[个件]|多少件)|几件|规格|什么型号|什么颜色|实付单价|成交价")
PHOTO_QUERY = re.compile(r"拍.{0,6}(?:照|图)|(?:照片|凭证).{0,5}(?:要|传|拍)|怎么举证")
PHOTO_REQUEST = re.compile(r"照片(?:要|该|得)?拍(?:哪些|什么|啥)|(?:要|该|得)?拍(?:哪些|什么|啥)(?:照片|图)|怎么拍照")
POSTAGE_FOLLOWUP = re.compile(r"邮费|邮资|谁掏|谁付|垫钱|垫付|自付|运费险|抵扣|寄回去.{0,4}钱")
REFUND_FOLLOWUP = re.compile(r"退款.{0,5}(?:到哪|退到哪|回来没)|钱.{0,6}(?:回来|退回|退到|到了没)")
LOGISTICS_QUERY = re.compile(r"物流|快递|什么时候到|哪天到|送到哪|到货没")
PARCEL_SUBJECTS = {"return", "outbound", "logistics"}
CHAT_REPLIES = {
    "damage": ["里面也坏了", "商品没坏"],
    "freight": ["买错了", "商品有质量问题", "商家发错了"],
    "reason": ["买错了", "商品有质量问题", "商家发错了", "少件漏发"],
}
REASON_REPLIES = {"wrong_purchase": "买错了", "quality": "商品有质量问题", "wrong_item": "商家发错了",
                  "missing": "少件漏发", "cancel": "取消未发货商品", "not_received": "没有收到"}
DIRECT_REPLIES = frozenset(reply for choices in CHAT_REPLIES.values() for reply in choices) | set(REASON_REPLIES.values()) | {"不是这个原因"}
ORDER_CODE_IN_TEXT = re.compile(r"(?<![A-Za-z0-9])CN-[0-9]{4,10}(?![A-Za-z0-9])", re.I)
ELIGIBILITY_CONFIRMATION = re.compile(r"(?:那|所以)?(?:我)?(?:这(?:个|件|单)(?:商品)?)?(?:还)?(?:可以|能|能不能|可不可以)申请售后(?:吧|吗|么)?[？?。！!]*")
MISSING_REPORT = re.compile(r"(?:[^，。？?！!；;,\n]{0,30}少了[^，。？?！!；;,\n]{1,25}|(?:充电线|鼠标线|配件)没给我)[。！!]*")
MISSING_RETRACTION = re.compile(r"(?:(?:线|配件|充电头|充电线|东西)?找到了[，,。 ]*(?:只是|就是|只有)(?=外盒|外箱|外包装|包装|纸箱|盒子))")
FOUND_WITH_FAULT = re.compile(r"^(?:刚|刚刚)?(?:线|配件|充电线|鼠标线)?(?:刚)?(?:翻到|找到)了[，,。 ]+(?:但是|不过)?[^，。？?]{0,18}(?:开不了机|不通电|坏了)[。！!]*$")
RESHIP_REQUEST = re.compile(r"(?:能|可不可以|想|怎么|申请).{0,5}(?:补寄|补发)|(?:补寄|补发)(?:吗|么)")
RESHIP_QUESTION = re.compile(r"(?:(?:充电)?线|配件|这个|缺的那件)?(?:能|可以|能不能|可不可以)(?:单独)?(?:补发|补寄)(?:吗|么|吧)?")
QUESTION_PARTS = re.compile(r"[，,。；;！!？?\n]+")
NONFACTUAL = re.compile(r"如果|假如|假设|比如(?:说)?|万一|要是")
USE_PREFERENCE = re.compile(
    r"(?:坐|穿|戴|用|躺|靠|背)(?:上去|起来|着|上|了之后)?"
    r"(?:感觉|觉得|有点|很|太|不太|不够)?"
    r"(?:不舒服|不舒适|不合适|不习惯|磨脚|夹耳|硌得慌|硌人|卡脚|太硬|太软|太紧|太松|偏硬|偏紧"
    r"|效果(?:不太好|不好|不理想|一般|不明显|没达到预期))"
)
RETURN_ELIGIBILITY = re.compile(
    r"(?:能不能|可不可以|可以|能|想|还可|要).{0,3}退(?:货|掉|了|吗|么|吧|不)|"
    r"退货(?:行不行|可以吗|能办吗)"
)
FIT_REPORT = re.compile(
    r"偏(?:小|大|短|长|紧|松)|(?:尺码|尺寸|鞋码).{0,5}(?:不合适|不合身|太小|太大)|"
    r"(?:穿|戴|系)(?:上|着|起来).{0,4}(?:太紧|太松|太小|太大|夹脚)|"
    r"(?:小|大)了(?:一|两|半|\d)(?:码|号)"
)
SIZE_EXCHANGE = re.compile(r"换.{0,5}(?:[大小](?:一|两|半|\d)?[码号]|[大小][一点些]|[大小][码号]|尺码|尺寸|(?<![a-z])(?:XXL|XL|[SML])(?:码|号|了吗|吗|吧|$)|\d{2,3}[码号])", re.I)
SIZE_MISMATCH = re.compile(
    r"(?:尺码表|页面|详情页|标注|描述).{0,10}(?:不符|不一致|不一样)|"
    r"比.{0,10}(?:尺码表|页面|详情页|标注|描述).{0,8}(?:短|长|大|小)|"
    r"(?:尺码表|页面|详情页|标注|描述).{0,12}(?:实际|量出来)"
)
SIZE_FOLLOW_UP = re.compile(r"(?:那就|想要|换成|换|要)?(?:[大小](?:一|两|半|\d)[码号]|(?:XXL|XL|[SML]|\d{2,3})[码号])[吧。！!]*", re.I)
NON_SIZE_AMOUNT = re.compile(r"退款|运费|配送费|邮费|金额|额度|赔偿")
CONFIRM_REASON = re.compile(r"(?:是|是的|对|对的|没错|就是这个意思|那就退吧)[。！! ]*")
REJECT_REASON = re.compile(r"(?:不对|不是|不是这个原因|我?不是这个意思|不是质量问题|不是买错了|不是少件)[。！! ]*")
ARRIVED_DAMAGED = re.compile(
    r"(?:收到|到货|买来|开箱|拆开|打开|刚拆|刚开盒).{0,22}(?:已经|就发现|发现|本来就|一看就)"
    r".{0,9}(?:碎|裂|断|破|损坏|坏了|漏液|变形)"
)
CLARIFICATION_FACTS = {
    "damage_timing": "开箱时已经损坏，还是使用后才损坏",
    "affected_part": "商品本身、配件或外包装，哪一部分有问题",
    "expected_vs_actual": "实际收到或使用的情况和预期有什么差别",
    "requested_outcome": "买家想核对退换、补发还是先说明情况",
}
WHOLE_RETURN = re.compile(r"(?:那|所以)?(?:我|这个|这件)?(?:是不是|要不要|要|还要|得|需要|必须|用不用)?(?:把)?(?:整套|整单)(?:都)?(?:寄回|退回)(?:吗|么|吧)?[？?。！!]*")
PARCEL_FOLLOWUP = re.compile(r"(?:那|所以)?(?:发了(?:吗|没)|发货了吗|寄出了吗|单号呢|运单号呢|到哪了)[？?。！!]*")
BASIS_ONLY = re.compile(r"(?:那)?(?:依据呢|依据是什么|按哪条规定|根据哪条|哪条规定|有依据吗)[？?。！!]*")
MISSING_RESOLVED = re.compile(r"(?:找到了[，,。 ]*)?(?:原来)?(?:没有少|没少|不缺)(?:了|东西|配件)?[。！!]*")
QUICK_CHECKS = re.compile(r"(?:请问|帮我查一下)?(?:退货运费谁出|退货邮费咋算|寄回运费谁出|钱退了吗|退款到账了吗|"
                          r"寄回运单|寄回地址(?:在哪|是什么|是多少)?|补发(?:到哪了|发了吗|发货了吗|的运单号是多少)|"
                          r"原包裹(?:到哪了|发货了吗)|售后进度(?:怎么样)?)[？?。！!]*")


def order_search_term(question: str) -> str | None:
    """识别买家明确的找单请求；当前商品的物流和售后问题继续走原流程。"""
    text = question.strip().rstrip("？?。！!").strip()
    for pattern in (
        r"(?:请)?(?:帮我)?(?:搜订单|找订单|搜索订单)[：:\s]*(.*)",
        r"(?:请)?(?:帮我)?(?:查找|找一下|找找|搜一下|找|搜)(?:我)?(?:之前|以前|上次|最近)?买(?:过)?的(.+?)",
        r"(?:我)?(?:之前|以前|上次)买(?:过)?的(.+?)(?:是哪(?:一)?单|在哪(?:个订单|一单))",
        r"(?:你)?(?:还)?记得我(?:原先|之前|以前|上次|原来)买(?:过)?的(.+?)(?:吗|么|不)?",
    ):
        match = re.fullmatch(pattern, text)
        if match:
            return match.group(1).strip().removesuffix("的订单").strip()
    return None


def asks_purchase_history(question: str) -> bool:
    """离线识别常用的购买历史问法。"""
    return bool(re.fullmatch(
        r"(?:那)?(?:你)?(?:还)?(?:记得)?我?(?:以前|之前|原先|过去)?(?:都)?买过(?:些)?(?:什么|啥|哪些(?:东西|商品)?)(?:吗|么)?[？?!！。]*",
        question.strip(),
    ))


LISTED_ORDER = re.compile(
    r"^(?:(?:请问|我问的是|我想问|再问一下|再问|还有|另外|以及|顺便|刚才(?:搜到的|找到的)?)\s*)*"
    r"(?:那(?:笔|一笔|个)?订单|那(?:一)?单(?!号)|那件(?:商品)?|"
    r"那个(?:商品|款|呢|[？?]|$)|第[一二三四五六1-6](?:个|笔|件|单)|(?:上|下)一(?:个|笔|单|件))"
)
ORDER_ORDINAL = re.compile(r"^(?:(?:我(?:说|指)的是|就|选)\s*)?第([一二三四五六1-6])(?:个|笔|件|单)")
ORDER_MONTH = re.compile(r"(上个?月|这个?月|本月)(?:买的|下单的)?那(?:一)?(?:笔|单|件|个)")
ORDER_DAY = re.compile(r"^(?:(\d{4})年)?(?:(\d{1,2})月)?(\d{1,2})[日号](?:买的|下单的)?(?:那(?:笔|单|件))?[。！？?!]?$")
DROP_PENDING = re.compile(
    r"^(?:(?:改一下|说错了|算了)[，, ]*)?(?:只(?:看|查|问)(?:这单|本单|当前订单)|"
    r"[^，,。！？?]{1,24}(?:不用|先别|别)(?:再)?(?:查|问)(?:了)?[。！!？?]*$)"
)
CURRENT_ORDER = re.compile(
    r"^(?:(?:请问|另外|还有|顺便|再问|以及)\s*)*(?:当前(?:这笔|这单|的订单)?|本单|这(?:一)?单|这(?:一)?笔(?:订单)?|这件(?:商品)?)"
)


def split_order_questions(question: str) -> tuple[str, str] | None:
    """只拆分两个指代明确的范围，保留每个范围下的连续问句。"""
    parts = re.split(r"[？?！!。；;，,\n]+|(?=(?:另外|还有|以及|再问|顺便)(?:这|那|刚才|本单|当前))",
                     question.strip())
    current, listed = [], []
    target = None
    for part in (part.strip() for part in parts if part.strip()):
        if CURRENT_ORDER.match(part):
            target = current
        elif match := LISTED_ORDER.match(part):
            target = listed
            part = part[match.end():].lstrip(" 的")
        if target is None:
            return None
        if part:
            target.append(part)
    if current and listed:
        return "；".join(current) + "？", "这笔订单" + "；".join(listed) + "？"
    return None


def refers_to_listed_order(question: str) -> bool:
    """识别指向另一笔订单或列表位置的说法，显式问本单时沿用当前商品。"""
    text = question.strip()
    return bool(LISTED_ORDER.match(text) or ORDER_ORDINAL.match(text) or ORDER_MONTH.search(text)
                or ORDER_DAY.fullmatch(text))


def narrow_order_candidates(question: str, candidates: list[dict], today: date) -> list[dict] | None:
    """按显示顺序或购买月份缩小候选，空列表表示筛选后没有命中。"""
    if match := ORDER_ORDINAL.match(question.strip()):
        ordinal = match[1]
        index = int(ordinal) - 1 if ordinal.isascii() else "一二三四五六".index(ordinal)
        return candidates[index:index + 1]
    if match := ORDER_MONTH.search(question):
        first = today.replace(day=1)
        month = (first - timedelta(days=1) if match[1].startswith("上") else first).isoformat()[:7]
        return [row for row in candidates if str(row["purchased"])[:7] == month]
    if match := ORDER_DAY.fullmatch(question.strip()):
        year, month, day = match.groups()
        return [row for row in candidates if str(row["purchased"])[8:10] == day.zfill(2)
                and (not month or str(row["purchased"])[5:7] == month.zfill(2))
                and (not year or str(row["purchased"])[:4] == year)]
    return None


def drops_pending_order(question: str) -> bool:
    """识别买家明确停止另一单查询的表达。"""
    return bool(DROP_PENDING.match(question.strip()))


def is_direct_question(question: str) -> bool:
    """已知动作和明确的单一售后提问直接交给订单规则。"""
    question = question.strip()
    if (order_search_term(question) is not None or asks_purchase_history(question)
            or refers_to_listed_order(question) or drops_pending_order(question)):
        return True
    if needs_citation_check(question) and not other_query_parts(question):
        return True
    if empty_package_report(question) and not EXPLICIT_LOGISTICS.search(question):
        return True
    if (CONFIRM_REASON.fullmatch(question) or EVIDENCE_SUBMISSION.fullmatch(question)
            or (MISSING_EVIDENCE.search(question) and not question_topics(question))):
        return True
    if FOUND_WITH_FAULT.fullmatch(question) and reported_reason(question) == "quality":
        return True
    parts = [part.strip() for part in QUESTION_PARTS.split(question) if part.strip()]
    if len(parts) > 1 and not NONFACTUAL.search(question) and missing_description(question):
        # 已能逐句解释的常见复合问题也可直接查询。遇到未知句子仍走模型。
        if all(missing_description(part) or PACKAGING.fullmatch(part.removesuffix("了"))
               or PHOTO_REQUEST.fullmatch(part) or RESHIP_QUESTION.fullmatch(part) for part in parts):
            return True
    return bool(question in DIRECT_REPLIES or any(pattern.fullmatch(question) for pattern in
                (ELIGIBILITY_CONFIRMATION, WHOLE_RETURN, PARCEL_FOLLOWUP, BASIS_ONLY, MISSING_RESOLVED, QUICK_CHECKS))
                or (MISSING_REPORT.fullmatch(question) and identify_reason(question) == "missing"
                    and not re.search(r"如果|假如|假设|万一|要是|怎么办|能不能|还要|吗|么", question)))


def reported_reason(question: str) -> str | None:
    """单独记录外包装情况，避免把纸箱破损误认成商品质量问题。"""
    if empty_package_report(question):
        return "missing"
    if EMPTY_PACKAGE.search(question):
        question = "，".join(part for part in QUESTION_PARTS.split(question) if not EMPTY_PACKAGE.search(part))
    return identify_reason(PACKAGING.sub("", question))


def empty_package_report(question: str) -> bool:
    """只把买家陈述的空盒收货当作事实。"""
    if NONFACTUAL.search(question):
        return False
    return any(not re.search(r"不是|并非|没有收到空|别说", part)
               and (not ISSUE_QUESTION.search(part.strip()) or re.search(r"收到|打开|拆开", part))
               and EMPTY_PACKAGE.search(part)
               for part in QUESTION_PARTS.split(question))


def issue_description(question: str, reason: str | None) -> str | None:
    """只采集买家已经陈述的具体情况，用于可编辑的申请草稿。"""
    if reason not in EVIDENCE_CHECKLIST or NONFACTUAL.search(question):
        return None
    parts = [part.strip() for part in QUESTION_PARTS.split(question) if part.strip()]
    confirmed = [part for part in parts if not ISSUE_QUESTION.search(part)
                 and (reported_reason(part) == reason or PACKAGING.search(part))
                 and not re.search(r"^(?:不是|并非|没有坏|没坏)", part)]
    if not any(reported_reason(part) == reason for part in confirmed):
        return None
    return "，".join(confirmed)[:500]


def missing_description(question: str) -> str | None:
    """从买家陈述中带入缺件事实，省略后面的办理问题。"""
    for part in QUESTION_PARTS.split(question):
        part = part.strip()
        if MISSING_REPORT.fullmatch(part) and reported_reason(part) == "missing" and not re.search(
            r"如果|假如|假设|万一|要是|怎么办|能不能|还要|怎么|是否|吗|么", part
        ):
            return part
    return None


def scope_parcel_topics(question: str, topics: list[str]) -> list[str]:
    """明确在问替换件时，普通的「快递」不额外触发原单物流。"""
    if empty_package_report(question) and not EXPLICIT_LOGISTICS.search(question):
        return [topic for topic in topics if topic != "logistics"]
    if "outbound" in topics and "logistics" in topics and not ORIGINAL_PARCEL.search(question):
        logistics_clauses = [part for part in QUESTION_PARTS.split(question) if LOGISTICS_QUERY.search(part)]
        if logistics_clauses and all(REPLACEMENT_PARCEL.search(part) for part in logistics_clauses):
            return [topic for topic in topics if topic != "logistics"]
    return topics


def resolve_query_scope(question: str, topics: list[str]) -> list[str]:
    """单一事项中，快递、进度等泛词服从具体业务对象；多事项保持原计划。"""
    topics = scope_parcel_topics(question, topics)
    clauses = [p for p in re.split(r"[，,。；;？?！!]|顺便|另外|以及|同时|还有|并且", question) if p.strip()]
    if len(clauses) > 1 and {"freight", "original_shipping"} <= set(topics) and all(
            re.search(r"运费|邮费|配送费|快递费", part) and not EXPLICIT_LOGISTICS.search(part) for part in clauses):
        return [topic for topic in topics if topic != "logistics"]
    if len(clauses) != 1 or len(topics) < 1:
        return topics
    def prefer(topic, redundant):
        return list(dict.fromkeys([*(t for t in topics if t not in redundant), topic]))

    postage = re.search(r"运费|邮费|配送费|快递费|快递钱|快递.{0,5}(?:付费|掏.{0,3}钱)", question)
    returning = re.search(r"退件|退货|退回|寄回|寄给(?:店|你|商家|卖家)", question)
    original = re.search(r"原订单|原来|原配送|发货时|购买时|下单|付款时|第一次(?:寄|发)|寄给我|"
                         r"(?:当时|最初).{0,8}(?:送货|送到|配送)", question)
    if postage and not re.search(r"到哪|运单|签收|发出", question):
        if original and not returning:
            return prefer("original_shipping", {"logistics", "freight"})
        if returning and not original:
            return prefer("freight", {"logistics"})
    if "outbound" in topics and not ORIGINAL_PARCEL.search(question) and not re.search(r"售后进度|谁处理", question):
        redundant = {"logistics", "progress"}
        if "替换件" in question and not returning:
            redundant.add("return")
        return prefer("outbound", redundant)
    if re.search(r"退款|货款退", question) and not re.search(r"售后|谁处理|寄回", question):
        return prefer("refund", {"progress"}) if set(topics) <= {"progress", "refund"} else topics
    if "logistics" in topics and re.search(r"(?:物流|配送)进度", question):
        return prefer("logistics", {"progress"})
    if original and "运单" in question and not returning:
        return prefer("logistics", {"return"})
    if re.search(r"拍|取证|留证|举证|证据", question) and not re.search(r"条款|依据|店规|本店|规定", question):
        if ("photos" in topics and set(topics) <= {"photos", "logistics", "shop_guidance"}
                and not EXPLICIT_LOGISTICS.search(question)):
            return prefer("photos", {"logistics", "shop_guidance"})
        if set(topics) <= {"photos", "shop_guidance", "eligibility"} and not reported_reason(question):
            return prefer("photos", {"shop_guidance", "eligibility"})
    return topics


def reports_poor_fit(question: str) -> bool:
    """仅把商品的尺寸反馈当成合身问题。"""
    return any(FIT_REPORT.search(part) and not NON_SIZE_AMOUNT.search(part) for part in QUESTION_PARTS.split(question))


def question_topics(question: str) -> list[str]:
    """匹配明确的只读诉求；复合问题可以同时查询几个来源。"""
    topics = [name for name, pattern in (
        ("product", PRODUCT_QUERY), ("logistics", LOGISTICS_QUERY), ("freight", POSTAGE_FOLLOWUP),
        ("original_shipping", ORIGINAL_SHIPPING), ("refund", REFUND_FOLLOWUP), ("photos", PHOTO_QUERY),
        ("progress", PROGRESS), ("offer", OFFER_QUERY), ("refund", REFUND_QUERY),
        ("return", RETURN_EXECUTION), ("outbound", OUTBOUND_EXECUTION), ("platform", INTERVENTION_QUERY),
    ) if pattern.search(question)]
    if re.search(r"原包裹(?:到哪了|发货了吗)", question):
        topics.append("logistics")
    if REPLACEMENT_PARCEL.search(question) and LOGISTICS_QUERY.search(question):
        topics.append("outbound")
    if FREIGHT.search(question) and ("original_shipping" not in topics or RETURN_SHIPPING.search(question)):
        topics.append("freight")
    if RESHIP_REQUEST.search(question) or re.search(r"能.{0,3}退[货换吗么]|想退货|怎么办售后|申请售后", question):
        topics.append("eligibility")
    if EVIDENCE_SUBMISSION.fullmatch(question.strip()):
        topics.append("eligibility")
    if not NONFACTUAL.search(question) and not SIZE_MISMATCH.search(question) and not re.search(r"不想换|不要换|不用换", question):
        if SIZE_EXCHANGE.search(question) or (reports_poor_fit(question) and not RETURN_ELIGIBILITY.search(question)):
            topics.append("fit_exchange")
    return scope_parcel_topics(question, list(dict.fromkeys([*topics, *dialogue_topics(question)])))


def consultation_evidence(item: dict, topics: list[str], result: dict) -> list[dict]:
    """只返回与本轮判断有关的下单条款；物流和退款事实另从原记录读取。"""
    ids = [POLICY_TOPICS[topic] for topic in topics if topic in POLICY_TOPICS]
    if result["eligibility"] and not result["case_no"]:
        if item["stage"] == "delivered":
            ids.append("window")
        if ("repair" in result["kinds"] or item["stage"] == "preparing" and "refund_only" in result["kinds"]
                or item["stage"] == "in_transit" and result["reason"] == "not_received"):
            ids.append("service")
    if result["freight"] or "freight" in topics and item["stage"] == "delivered":
        ids.append("freight")
    if result["intervention"]:
        ids.append("response")
    clauses = policy_clauses(item) if ids else {}
    return [{"id": key, "text": clauses[key]} for key in dict.fromkeys(ids)]


def describe_outcome(case: dict, *, check_offer: bool, check_refund: bool) -> list[str]:
    """从原单的待确认方案、已接受快照和退款登记读取结果。"""
    replies = []
    pending = (case.get("prompt") or {}).get("offer")
    accepted = case.get("agreement")
    selected = accepted or pending
    if check_offer and selected:
        summary = f"{KINDS[selected['kind']]}，涉及 {selected['quantity']} 件"
        if selected["amount"] is not None:
            summary += f"，商品退款 ¥{Decimal(selected['amount']):.2f}"
        lead = "买家已接受" if accepted else "店铺提出"
        replies.append(f"{lead}「{summary}」。")
        if not accepted:
            next_step = ("接受后，先按方案寄回商品。" if selected["kind"] in RETURN_KINDS
                         else "接受后，由店铺按这版方案执行。")
            replies.append(next_step)
        if selected["return_instructions"]:
            replies.append(f"寄回说明：{selected['return_instructions']}")
    elif check_offer:
        replies.append("当前没有待确认或已接受的可执行方案，可打开售后单查看协商记录。")
    if check_refund:
        if summary := case.get("refund_summary"):
            product = summary["product"]
            replies.append(product["message"])
            if product.get("failure_reason"):
                replies.append("处理说明：" + product["failure_reason"])
            return replies
        amount = case.get("recorded_refund")
        attempt = case.get("refund_attempt")
        if attempt and attempt["state"] == "pending":
            replies.append(f"已发起商品退款 ¥{Decimal(attempt['amount']):.2f}，退款正在处理。")
        elif attempt and attempt["state"] == "failed":
            replies.append(f"商品退款 ¥{Decimal(attempt['amount']):.2f} 本次处理失败，等待店铺按原金额重试。"
                           f"处理说明：{attempt['result_note']}")
        elif attempt and attempt["state"] == "succeeded" and amount is not None:
            replies.append(f"退款成功，已登记商品退款 ¥{Decimal(amount):.2f}。")
        elif amount is not None:
            replies.append(f"这张单已在商城登记商品退款 ¥{Decimal(amount):.2f}。")
        elif accepted and accepted["amount"] is not None:
            replies.append(f"已确认商品退款 ¥{Decimal(accepted['amount']):.2f}，商家尚未登记退款。")
        elif pending and pending["amount"] is not None:
            replies.append("金额仍在等待买家确认。")
        else:
            replies.append("这张单暂无商品退款登记。")
    return replies


def describe_return(case: dict) -> str:
    """按当前原单的寄回信息、负责人和期限给买家说明。"""
    deadline = case.get("due_at")
    due = f"截止 {deadline.astimezone(ZoneInfo('Asia/Shanghai')):%Y-%m-%d %H:%M}。" if deadline else ""
    status = case["status"]
    if case.get("carrier_returned") and status == "return_sent":
        return "承运方已受理原包裹退回，等待店铺确认实际收件，无需填写寄回运单。" + due
    if case.get("transit_request") and status == "awaiting_outcome":
        if case.get("interception_failed"):
            return ("包裹拦截失败，仍在配送。可以联系快递确认拒收，受理后由店铺登记。"
                    "如果已经签收，请在原售后单选择「我已签收，改为自行寄回」。")
        return ("本次只退部分商品，请保留同包其他商品。实际签收后，可在原单确认签收并办理寄回。"
                if case["transit_request"] == "partial" else "店铺正在核对拦截或拒收结果。如已实际签收，可从原单改为自行寄回。")
    if status == "awaiting_return" and case.get("return_details_pending"):
        if case.get("transit_request"):
            return "已按实际签收情况转为寄回，当前等待店铺填写收件地址。" + due
        return "平台已支持退货，当前由店铺补充寄回信息。" + due + "地址提供后，原单会开放运单登记。"
    if status == "awaiting_return":
        details = case.get("return_details")
        if details:
            address = f"寄往 {details['address']}，收件人 {details['recipient']}，联系电话 {details['phone']}。"
        else:
            instructions = (case.get("agreement") or {}).get("return_instructions")
            address = f"寄回说明：{instructions}。" if instructions else "请在原单查看商家留的寄回说明。"
        return address + due + "寄出后请在原单登记运单。"
    if status == "return_sent":
        return "运单已登记，等待店铺确认收件。" + due
    return f"当前原单为「{case['status_label']}」。请在原单查看寄回和收件记录。"


def describe_outbound(case: dict) -> str:
    """分别说明店铺寄出的新包裹，避免与原订单运单混淆。"""
    parcel = case.get("outbound")
    if not parcel:
        if case.get("outbound_pending"):
            return "请在原售后单确认本次收货信息，确认后店铺可以发货。"
        kind = (case.get("agreement") or case).get("kind")
        if kind in ("exchange", "repair") and case.get("status") in ("awaiting_return", "return_sent"):
            return describe_return(case) + "店铺确认收到寄回的商品后，买家可在原单确认本次收货信息。"
        return "当前售后单没有独立的新包裹记录，可查看原单时间线。"
    review = case.get("delivery_review") or {}
    outcome = (DELIVERY_DECISION_MESSAGES.get(review.get("action"), "")
               if review.get("result_parcel_no") == parcel["parcel_no"] else "")
    message = f"店铺寄出的售后包裹 #{parcel['parcel_no']}。"
    if parcel["tracking"]:
        message += f"本次新运单为 {parcel['carrier']} {parcel['tracking']}。"
    if parcel["received_at"]:
        return message + "买家已确认收到。" + outcome
    if review.get("action") == "request_delivery_review":
        return message + "平台正在核对这次包裹，可在原单补充凭证。"
    if case.get("delivery_reply"):
        return message + "店铺核查回复：" + case["delivery_reply"]["note"] + " 如仍未收到，可在原单申请平台核对。"
    if parcel["stage"] == "ready" and not parcel["problem"]:
        return message + outcome + "收货信息已确认，等待店铺寄出。"
    if parcel["problem"]:
        return message + outcome + f"已反馈「{DELIVERY_PROBLEMS[parcel['problem']]}」，当前请按原单进度办理。"
    return message + outcome + "收到后可回原单确认收货，也可反馈没收到、发错或破损。"


def _answer_order(item: dict, question: str, explicit: str | None, previous: dict, latest: dict | None,
                  today: date, active_case: dict | None, topics: list[str], *,
                  confirmed_reason: str | None = None, trusted_reason: bool = False) -> tuple[dict, dict]:
    """从订单、售后和店铺约定生成办理结论，保留表单需要的完整细节。"""
    check_outbound = not explicit and "outbound" in topics
    reason = confirmed_reason if trusted_reason else explicit or confirmed_reason or (None if check_outbound and "eligibility" not in topics else reported_reason(question))
    if not trusted_reason and not explicit and item.get("stage") == "in_transit" and reason in (None,"not_received"):
        if any(identify_reason(clause) == "wrong_purchase" for clause in re.split(r"[，,。；;]|但是", question)):
            reason = "wrong_purchase"
    ask_original_shipping = "original_shipping" in topics
    asking_freight = "freight" in topics and (trusted_reason or not ask_original_shipping or bool(RETURN_SHIPPING.search(question)))
    correcting = any(word in question for word in ("不是", "并非", "说错", "弄错原因"))
    if not trusted_reason and not reason and (asking_freight or "eligibility" in topics) and previous.get("reason") in REASONS and not correcting:
        reason = previous["reason"]
    selected_case = active_case or latest
    use_case_freight = (not explicit and not check_outbound and (asking_freight or ask_original_shipping)
                        and (active_case or not reason or ask_original_shipping))
    case_freight = (selected_case or {}).get("refund_summary") if use_case_freight else None
    has_packaging = not trusted_reason and bool(PACKAGING.search(question) and not PACKAGING_INTACT.search(question))
    prior_packaging = not trusted_reason and previous.get("awaiting") == "damage"
    empty_package = empty_package_report(question)
    retract_damage = not trusted_reason and previous.get("reason") == "quality" and bool(UNDAMAGED.search(question))
    if retract_damage and not explicit:
        correcting = True
        reason = None
    resumes_damage = prior_packaging and bool(UNDAMAGED.search(question) or ITEM_DAMAGE.search(question))
    if resumes_damage and previous.get("pending_freight"):
        asking_freight = True
    description = (issue_description(question, reason) if not explicit
                   and (reason != "missing" or empty_package_report(question)) else None)
    if trusted_reason:
        description = previous.get("issue_description") if reason else None
    messages = []
    pending = None

    if not explicit and not check_outbound and (has_packaging or prior_packaging):
        if reason == "missing":
            if not empty_package:
                messages.append("拍一下外盒全貌和压坏的位置，再拍箱里实际收到的东西。包裹标签上的手机号和地址可以遮住。")
        elif UNDAMAGED.search(question) or (ONLY_PACKAGING.search(question) and not ITEM_DAMAGE.search(question)):
            reason = None
            pending = None
            messages.append("已了解，外包装破损，商品目前完好。可以先保留外箱和破损处照片。")
        elif ITEM_DAMAGE.search(question) or (reason == "quality" and description):
            reason = "quality"
            pending = None
            description = issue_description(question, reason) or "收到时外包装破损，商品也有损坏。"
            if prior_packaging and question.strip("。！!") == "里面也坏了":
                description = "收到时外包装破损，商品也有损坏。"
            elif prior_packaging and not has_packaging and not PACKAGING.search(description):
                description = "外包装破损，" + description
            messages.append(PHOTO_GUIDANCE)
        elif has_packaging:
            reason = None
            pending = "damage"
            messages.append("外包装破损，商品本身也有损坏吗？可以先拍外箱全貌和破损处近照。")
    elif retract_damage and not explicit:
        messages.append("明白了，商品本身完好。刚才说的质量问题先不算。")
    if empty_package and reason == "missing":
        photo = "可以拍外箱全貌、压坏处和箱内现状。" if has_packaging else "可以拍外箱全貌和箱内现状。"
        messages.insert(0, "按你描述的空盒情况，可以走少件漏发。" + photo + "面单上的手机号和地址可以遮住。")
    if "photos" in topics and (trusted_reason or not description and not has_packaging and not prior_packaging):
        if trusted_reason:
            checklist = EVIDENCE_CHECKLIST.get(reason) or ['商品和现有包装的实际状态照片，面单上的手机号和住址可以遮住']
            messages.append('建议准备的凭证：'+'；'.join(checklist)+'。这些是建议项目，是否需要补充由店铺按实际情况核对。')
        else:
            messages.append(item.get("guidance") if item.get("guidance_key") == "category" else PHOTO_GUIDANCE)
    if "shop_guidance" in topics and "photos" not in topics:
        messages.append(item.get("guidance") or "下单时这版条款没有单列打包或举证要求。寄回前可以向店铺确认。")

    if not case_freight and not check_outbound and (asking_freight or previous.get("awaiting") == "freight") and pending != "damage":
        if reason:
            asking_freight = True
        elif asking_freight:
            pending = "freight"
            messages.append("这件为什么想退呢？比如买错了、东西坏了，或者店铺发错了。我帮你看看运费谁出。")

    eligibility = assess_eligibility(item, reason, today) if reason else None
    if active_case and (eligibility or "eligibility" in topics):
        eligibility = Eligibility((), f"这件商品已有售后单 #{active_case['case_no']}，{active_case['status_label']}。",
                                  "请打开原售后单查看或继续办理。")
    kinds = eligibility.kinds if eligibility else ()
    quote = freight_quote(item, reason) if any(kind in kinds for kind in RETURN_KINDS) else None
    show_progress = "progress" in topics or (not topics and question.strip("？?") in ("到哪了", "办好了吗", "现在什么状态"))
    check_offer = "offer" in topics
    check_refund = "refund" in topics
    check_return = not explicit and not check_outbound and bool(
        "return" in topics or
        (selected_case and selected_case.get("transit_request") and TRANSIT_RETURN_QUERY.search(question)))
    logistics = "logistics" in topics
    show_outbound = check_outbound or bool(selected_case and selected_case.get("outbound") and (show_progress or logistics and not trusted_reason))
    intervention = None
    if not explicit and "platform" in topics:
        intervention = selected_case["intervention"] if selected_case else {
            "state": "no_case",
            "message": "这件商品还没有售后单。请先向店铺申请商品售后。协商未达成或店铺办理超时后，可从原单申请平台介入。",
        }
        messages.append(intervention["message"])
    elif show_outbound and selected_case and selected_case["intervention"]["state"] == "delivery_review_available":
        intervention = selected_case["intervention"]
        messages.append(intervention["message"])
    if show_progress or check_offer or check_refund or check_return or show_outbound or case_freight:
        if selected_case is None:
            if show_outbound and previous.get("reason") == "missing":
                messages.append("这件商品还没有补发申请。你可以先在右侧选「少件漏发」核对能否申请。")
            else:
                messages.append("这件商品暂时没有售后申请。")
        else:
            if show_progress and not (check_refund and not re.search(r"谁|截止|超时|谁处理|售后进度", question)):
                direction = f"当前由{selected_case['owner']}处理。" if selected_case.get("owner") else ""
                overdue = "该步骤已超时。" if selected_case.get("overdue") else ""
                messages.append(f"售后单 #{selected_case['case_no']} {selected_case['status_label']}。{direction}{overdue}")
                if (due := selected_case.get("due_at")) and not (check_return or selected_case.get("status") in ("awaiting_return", "return_sent")):
                    messages.append(f"本步截止 {due.astimezone(ZoneInfo('Asia/Shanghai')):%Y-%m-%d %H:%M}。")
            messages.extend(describe_outcome(selected_case, check_offer=check_offer, check_refund=check_refund))
            if case_freight:
                if ask_original_shipping:
                    messages.append(case_freight["original_shipping"]["message"])
                if not ask_original_shipping or RETURN_SHIPPING.search(question):
                    messages.append(case_freight["return_shipping"]["message"])
            if check_return or (show_progress and (selected_case.get("status") in ("awaiting_return", "return_sent")
                                                   or selected_case.get("transit_request") and selected_case.get("status") == "awaiting_outcome")):
                messages.append(describe_return(selected_case))
            if show_outbound:
                messages.append(describe_outbound(selected_case))
    if "product" in topics:
        parts = []
        if re.search(r"规格|型号|颜色|什么|啥|哪[个款]", question):
            parts.append(f"{item['title']}，{item['variant']}")
        if re.search(r"多少件|几[件个]|数量", question):
            parts.append(f"买了 {item['quantity']} 件")
        if re.search(r"实付|单价|多少钱|成交价", question) or "amount_followup" in topics:
            parts.append(f"每件实付 ¥{item['paid_unit']:.2f}")
        if trusted_reason:
            parts = [f"{item['title']}，{item['variant']}", f"买了 {item['quantity']} 件",
                     f"每件实付 ¥{item['paid_unit']:.2f}"]
        messages.append("，".join(parts or [f"{item['title']}，{item['variant']}"]) + "。")
    if ask_original_shipping and not case_freight:
        shipping = item.get("shipping")
        messages.append("这单暂无原配送费结算记录。" if shipping is None else
                        f"原订单配送费实付 ¥{shipping:.2f}。")
    if logistics and not check_outbound:
        stage = {"preparing": "尚未发货", "in_transit": "运输中", "delivered": "已有签收记录"}[item["stage"]]
        if item.get("remaining_quantity", item.get("quantity")) == 0:
            stage = "未发货，商品已全额登记退款" if item["stage"] == "preparing" else "商品已全额登记退款"
        messages.append(f"{item['title']}（包裹 {item['package_no']}）{stage}。"
                        + (f"运单号 {item['tracking']}。" if item["tracking"] else ""))
        if trusted_reason:
            if item.get('carrier'):
                messages.append('承运公司：'+item['carrier']+'。')
            if item.get('delivered_at'):
                messages.append('签收记录时间（北京时间）：'+item['delivered_at'].astimezone(ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d %H:%M')+'。')
            if item.get('expected_on'):
                messages.append('订单记录的预计送达日期：'+str(item['expected_on'])+'，不是新的送达承诺。')
    if asking_freight and kinds and not case_freight:
        if quote and "due" in quote:
            messages.append(f"这单能抵 ¥{quote['credit']:.2f} 运费，寄回时预计自付 ¥{quote['due']:.2f}。"
                            if quote["credit"] else f"这单寄回要自己付运费，预计自付 ¥{quote['due']:.2f}。")
        elif quote:
            messages.append(f"店铺核实后承担运费，你先垫付 ¥{quote['advance']:.2f}，之后由店铺返还。"
                            if quote["advance"] else "店铺核实后承担运费，按店铺提供的方式寄回即可。")
        else:
            messages.append("拦截或拒收费用需要店铺向承运方核实。" if eligibility.transit else "当前可申请的方式无需寄回。")
    if not selected_case and any(word in question for word in ("退多少", "退款金额")):
        messages.append(f"每件商品实付 ¥{item['paid_unit']:.2f}。")
        if "refund_balance" in item:
            messages.append(f"这件商品的退款余额为 ¥{item['refund_balance']:.2f}，本次金额以确认的方案为准。")
    for topic in topics:
        if topic in POLICY_TOPICS:
            messages.append(scoped_term(item, topic, reason or previous.get("reason")))
    if eligibility and ("eligibility" in topics or eligibility.transit or not kinds or not messages
                        or description and reason in {"quality", "wrong_item"}):
        messages.append(eligibility.message)
        if eligibility.next_step:
            if eligibility.transit and not explicit:
                messages.append("整包不要可申请拦截或向快递明确拒收；只退部分商品请先联系店铺。")
            elif not kinds or explicit:
                messages.append(eligibility.next_step)
    if "eligibility" in topics and not reason and not pending and not active_case:
        messages.append("你是少收到配件，还是商品本身有问题？我可以按实际情况查能否补发。"
                        if RESHIP_REQUEST.search(question) else "你是买错了、少收到东西，还是商品有问题？我帮你查能办哪种售后。")
        pending = "reason"
    if not messages:
        messages.append("我可以帮你查这件商品的退换条件、寄回运费或售后进度。你想先了解哪一项？")
    linked_case = (active_case if active_case and eligibility else
                   selected_case if show_progress or check_offer or check_refund or check_return or show_outbound or intervention or case_freight else None)
    result = {"answer": "\n".join(messages), "reason": reason, "kinds": eligibility.choices if eligibility else {},
              "freight": quote, "case_no": linked_case["case_no"] if linked_case else None,
              "intervention": intervention,
              "eligibility": {"message": eligibility.message, "next_step": eligibility.next_step,
                              "transit": eligibility.transit} if eligibility else None,
              "suggested_description": description if kinds else None,
              "evidence_checklist": EVIDENCE_CHECKLIST.get(reason, []) if kinds else [],
              "terms_version": item["terms_version"], "terms_title": item["terms_title"]}
    result["quick_replies"] = CHAT_REPLIES.get(pending, [])
    result["reset_suggestion"] = not explicit and not reason and (correcting or pending == "damage" or resumes_damage)
    remembered = reason or previous.get("reason")
    if not reason and (correcting or has_packaging or resumes_damage):
        remembered = None
    outstanding = pending
    if not outstanding and not reason and not resumes_damage and not correcting:
        outstanding = previous.get("awaiting")
    return result, {"awaiting": outstanding, "reason": remembered,
                    "pending_freight": outstanding == "damage" and (asking_freight or previous.get("pending_freight", False))}


def consultation_context(previous: dict) -> dict:
    """给模型保留一段问题描述，以及已经问过的事实和已排除的原因。"""
    return {name: previous[name] for name in
            ("issue_report", "proposed_reason", "rejected_reasons", "asked_facts", "arrival_damage", "requested_kind")
            if previous.get(name)}


def _model_clarification(item: dict, follow_up: dict | None, context: dict) -> dict | None:
    """只采纳尚未问过的具体追问，不接收业务操作或售后结论。"""
    if not follow_up or follow_up["fact"] in context.get("asked_facts", []):
        return None
    question = follow_up["question"].strip()
    if (not re.fullmatch(r"[^？?\r\n]{5,100}[？?]", question)
            or re.search(r"\d|[¥￥]|已退款|已提交|已申请|已补发|批准|保证|订单号|手机号|https?://", question)
            or any(label in question for label in REASONS.values())):
        return None
    context["asked_facts"] = [*context.get("asked_facts", []), follow_up["fact"]]
    result = _followup_reply(item, question)
    result.update(response_kind="clarification", answer_source="model")
    return result


def answer(item: dict, question: str, explicit: str | None, previous: dict, latest: dict | None,
           today: date, *, active_case: dict | None = None, topics: list[str] | None = None,
           last_result: dict | None = None, follow_up: dict | None = None) -> tuple[dict, dict]:
    """区分业务问题和对上一句的追问；只在买家要求时展示出处。"""
    topics = question_topics(question) if topics is None else list(topics)
    pending_reason = previous.get("proposed_reason")
    if not explicit and REJECT_REASON.fullmatch(question.strip()) and (
            question.strip(" 。！!") != "不是" or pending_reason or previous.get("issue_report")):
        context = consultation_context(previous)
        context.pop("proposed_reason", None)
        rejected = {"不是质量问题": "quality", "不是买错了": "wrong_purchase", "不是少件": "missing"}.get(
            question.strip(" 。！!"), pending_reason or previous.get("reason"))
        if rejected in REASONS:
            context["rejected_reasons"] = list(dict.fromkeys([*context.get("rejected_reasons", []), rejected]))
        prior = last_subjects(previous, last_result or {})
        context.update(focus=prior, awaiting="freight" if "freight" in prior else "reason", reason=None)
        result = _model_clarification(item, follow_up, context) or _followup_reply(
            item, "先把刚才的分类放一放。你可以补充当时看到的情况，或者说说你想怎么处理。")
        result["reset_suggestion"] = True
        return result, context
    confirmed_reason = None
    if pending_reason and not explicit:
        if pending_reason in REASONS and CONFIRM_REASON.fullmatch(question.strip()):
            confirmed_reason = pending_reason
            topics = ["eligibility", "freight"] if previous.get("awaiting") == "freight" else ["eligibility"]
        # “对”只确认紧邻的追问。已明确陈述的使用感受有自己的生命周期。
        previous = {key: value for key, value in previous.items() if key != "proposed_reason"}
    size_follow_up = previous.get("requested_kind") == "exchange" and SIZE_FOLLOW_UP.fullmatch(question.strip())
    if not explicit and not size_follow_up and topics == ["out_of_scope"]:
        result = _followup_reply(item, "我在这里主要帮你处理订单售后。想查物流、退换货或退款进度，都可以直接问我。")
        result["response_kind"] = "out_of_scope"
        return result, previous
    preference_report = previous.get("preference_report")
    identified = reported_reason(question)
    buying_arrival_damage = (not explicit and not NONFACTUAL.search(question) and ARRIVED_DAMAGED.search(question)
                             and not re.search(r"没(?:有)?(?:碎|裂|坏)|我.{0,8}(?:摔|碰|磕)(?:坏|碎|裂)了", question)
                             and (not PACKAGING.search(question) or ITEM_DAMAGE.search(question))
                             and identified in (None, "quality"))
    wants_return = bool(RETURN_ELIGIBILITY.search(question) or ELIGIBILITY_CONFIRMATION.fullmatch(question.strip()))
    fit_issue = (not explicit and not NONFACTUAL.search(question) and not SIZE_MISMATCH.search(question)
                 and not re.search(r"不(?:想|要|用)换|并不偏[大小]|不算偏[大小]|并不太[紧松]", question)
                 and (reports_poor_fit(question) or SIZE_EXCHANGE.search(question) or size_follow_up
                      or ("fit_exchange" in topics and not set(topics) & {"refund", "freight", "original_shipping"})))
    if (not explicit and not identified and not NONFACTUAL.search(question) and SIZE_MISMATCH.search(question)
            and set(topics) <= {"fit_exchange", "product", "eligibility", "return_window", "confirm_wrong_item", "confirm_quality",
                                "confirm_wrong_purchase"}):
        context = {**previous, "issue_report": question, "focus": ["eligibility"]}
        result = _model_clarification(item, follow_up, context) or _followup_reply(
            item, "你说的是实物和标注可能不一致。方便告诉我页面标的尺寸和你量到的尺寸吗？")
        return result, context
    suggest_exchange = bool(fit_issue and (not wants_return or SIZE_EXCHANGE.search(question)))
    if fit_issue:
        if identified is None:
            confirmed_reason = "wrong_purchase"
        incidental = {"out_of_scope", "fit_exchange", "return_window", "next_step"}
        if not PRODUCT_QUERY.search(question):
            incidental.add("product")
        topics = list(dict.fromkeys([*(topic for topic in topics if topic not in incidental), "eligibility"]))
        if size_follow_up and previous.get("issue_description"):
            preference_report = (previous["issue_description"] + "。" + question)[:500]
        else:
            preference_report = question
    arrival_report = question if buying_arrival_damage else previous.get("arrival_damage")
    if not buying_arrival_damage and re.search(r"说错|其实.{0,6}(?:摔|碰|磕)|后来.{0,6}(?:摔|碰|磕)|没(?:有)?(?:碎|裂|坏)", question):
        previous = {key: value for key, value in previous.items() if key != "arrival_damage"}
        arrival_report = None
    if (arrival_report and not explicit and not NONFACTUAL.search(question) and identified in (None, "quality")
            and (buying_arrival_damage or wants_return or "freight" in topics)):
        if buying_arrival_damage and not (
                wants_return or set(question_topics(question)) & {"freight", "photos", "logistics", "refund", "platform"}):
            context = {**consultation_context(previous), "arrival_damage": question, "issue_report": question,
                       "focus": ["eligibility"]}
            if active_case:
                result = _followup_reply(item, f"你描述的是拆包时就发现破损。可以到原售后单 #{active_case['case_no']} 补充情况。")
                result["case_no"] = active_case["case_no"]
            else:
                result = _followup_reply(item, "你描述的是拆包时就发现破损。可以先拍下破损处、商品全貌和外包装。"
                                         "想继续办理的话，我可以查这单支持哪些处理方式。", ["这件还可以退吗？"])
            result["reset_suggestion"] = bool(previous.get("reason"))
            return result, context
        confirmed_reason = "quality"
        topics = list(dict.fromkeys([*(topic for topic in topics if topic != "return_window"), "eligibility"]))
    corrects_issue = re.search(r"不是|并非|说错|搞错|弄错|改口|没(?:有)?觉得|谈不上|"
                               r"现在(?:坐|穿|戴|用)着(?:挺|很)?舒服", question)
    retracts_preference = False
    replaces_reason = False
    if not explicit and not NONFACTUAL.search(question) and not fit_issue:
        retracts_preference = bool(preference_report and corrects_issue and not identified)
        if corrects_issue or identified:
            preference_report = None
        experience = USE_PREFERENCE.search(question) if not corrects_issue and not identified else None
        if experience:
            preference_report = next(part.strip() for part in QUESTION_PARTS.split(question)
                                     if USE_PREFERENCE.search(part))[:500]
            replaces_reason = previous.get("reason") not in (None, "wrong_purchase")
            if replaces_reason:
                previous = {"preference_report": preference_report}
        consultation_topics = {"product", "eligibility", "return_window", "freight", "next_step",
                               "confirm_wrong_purchase", "confirm_quality"}
        # 保留另一个明确问题，如退款到账或平台介入。纯商品信息的误路由不能覆盖实际反馈。
        if preference_report and not identified and not corrects_issue and not confirmed_reason:
            if wants_return or "freight" in topics or confirmed_reason == "wrong_purchase" or (active_case and experience):
                confirmed_reason = "wrong_purchase"
                incidental = {"product", "return_window", "next_step"} if len(
                    [part for part in QUESTION_PARTS.split(question) if part.strip()]) == 1 else set()
                topics = list(dict.fromkeys([*(topic for topic in topics if topic not in incidental), "eligibility"]))
            elif experience and set(topics) <= consultation_topics:
                effect = experience.group()
                context = {**previous, "focus": ["eligibility"], "issue_report": preference_report,
                           "preference_report": preference_report}
                result = _model_clarification(item, follow_up, context)
                if result:
                    result["quick_replies"] = ["这件还可以退吗？"]
                elif "效果" in effect:
                    result = _followup_reply(item, "听起来效果没达到你的预期。是用起来不顺手，还是商品有哪里坏了？",
                                             ["这件还可以退吗？"])
                else:
                    result = _followup_reply(item, f"明白了，这件商品{effect}。你想查这单能不能退吗？",
                                             ["这件还可以退吗？"])
                    context["proposed_reason"] = "wrong_purchase"
                result["reset_suggestion"] = replaces_reason
                return result, context
    if preference_report != previous.get("preference_report"):
        previous = {key: value for key, value in previous.items() if key != "preference_report"}
        if preference_report:
            previous["preference_report"] = preference_report
    if retracts_preference and previous.get("reason") == "wrong_purchase":
        previous = {**previous, "reason": None, "issue_description": None}
    prior = last_subjects(previous, last_result or {})
    case = active_case or latest
    subjects = [key for key in topics if key in SUBJECTS]
    if (not explicit and MISSING_EVIDENCE.search(question) and not NONFACTUAL.search(question)
            and not reported_reason(question) and not set(subjects) - {"photos", "eligibility"}):
        reason = previous.get("reason") or (active_case or {}).get("reason")
        issue = "这件商品的" + REASONS[reason] if reason in REASONS else "这件商品的售后"
        checklist = EVIDENCE_CHECKLIST.get(reason)
        examples = "、".join(checklist[:2]) if checklist else "外包装和商品现状"
        if reason == "missing" and empty_package_report(previous.get("issue_description") or ""):
            examples = "空箱内部、面单和外包装"
        message = f"关于{issue}，可以先整理手头现有的材料，拍下{examples}。"
        if active_case:
            message += "在原售后单补充材料，或写明缺少哪些凭证。"
        else:
            message += "材料缺失的情况也可以写进申请说明，店铺需要补充时会在售后单上反馈。"
            if reason in REASONS and "eligibility" in topics:
                message += assess_eligibility(item, reason, today).message
        result = _followup_reply(item, message)
        result["response_kind"] = "evidence_guidance"
        if active_case:
            result["case_no"] = active_case["case_no"]
        return result, previous
    if not explicit and (citation_query := resolve_citation_query(question, topics, previous)):
        remaining = other_query_parts(question)
        if remaining:
            supported_question = "？".join(remaining)
            promise_question = "？".join(part for part in split_aspects(question) if needs_citation_check(part))
            result, context = answer(item, supported_question, explicit, previous, latest, today,
                                     active_case=active_case, last_result=last_result)
            message, evidence, _ = checked_mall_citations(item, promise_question)
            result["answer"] += "\n" + message
            if evidence:
                result.update(evidence=evidence, evidence_requested=True)
            return result, context
        message, evidence, choices = checked_mall_citations(item, citation_query)
        return _followup_reply(item, message, choices, evidence=evidence, reference=bool(evidence)), {
            **previous, "citation_query": citation_query}
    if "citation_query" in previous:
        previous = {key: value for key, value in previous.items() if key != "citation_query"}
    corrected_missing = (not explicit and previous.get("reason") == "missing"
                         and not NONFACTUAL.search(question) and (MISSING_RETRACTION.search(question)
                         or FOUND_WITH_FAULT.fullmatch(question) and reported_reason(question) == "quality"))
    if corrected_missing:
        previous = {**previous, "reason": None, "missing_description": None, "missing_guide": None}
    if not explicit and MISSING_RESOLVED.fullmatch(question.strip()) and previous.get("reason") == "missing":
        result = _followup_reply(item, "找到了就好。刚才填的缺件原因先撤掉。如果你已经提交了申请，可以从售后单查看进度。")
        result["reset_suggestion"] = True
        return result, {}
    if not explicit and PARCEL_FOLLOWUP.fullmatch(question.strip()):
        parcel_topics = [key for key in prior if key in PARCEL_SUBJECTS]
        if len(parcel_topics) == 1:
            topics = parcel_topics
            subjects = parcel_topics
        elif parcel_topics or question.strip("？?") != "到哪了":
            return _followup_reply(item, "你想查哪个包裹？",
                                   ["原包裹到哪了？", "补发到哪了？", "寄回运单"]), previous
    policy_scope = [key for key in ("platform_floor", "shop_current") if key in subjects]
    if "basis" in topics and not policy_scope and not [key for key in question_topics(question) if key in SUBJECTS]:
        policy_scope = [key for key in prior if key in ("platform_floor", "shop_current")]
    if "basis" in topics and previous.get("missing_guide") and not (policy_scope or [key for key in question_topics(question) if key in SUBJECTS]):
        guide = item.get("missing_supplement")
        current = next((row for row in item.get("supplements", []) if row["id"] == previous["missing_guide"]), None)
        if current and current["title"]:
            return _followup_reply(item, "这是你下单时适用的店铺补充条款。", evidence=[{
                "id": f"supplement-{current['id']}", "text": current["note"],
                "source": f"店铺已审核条款 #{current['id']}"}], reference=True), previous
        if guide and guide["id"] == previous["missing_guide"]:
            return _followup_reply(item, f"你下单时适用的是店铺第 {guide['id']} 号缺件补发说明。",
                                   evidence=[{"id":"missing_guide","text":guide["note"]}], reference=True), previous
    if not explicit and policy_scope:
        policy = item.get("current_policy")
        if not policy:
            return _followup_reply(item, "目前查不到这家店已发布的平台保障版本，可以在订单详情核对下单条款。"), previous
        facets = [key for key in subjects if key not in policy_scope]
        if "basis" in topics and not facets:
            facets = previous.get("policy_facets", [])
        message, excerpts = published_answer(policy, policy_scope, facets, item["terms_version"], "basis" in topics)
        return _followup_reply(item, message, evidence=excerpts, reference="basis" in topics), {
            **previous, "focus": policy_scope, "policy_facets": facets, "policy_reference": policy["version"]}
    # 对“那多少钱”“根据哪条”这类省略句，优先用已保存的话题，避免模型猜错所指。
    if not explicit and ("basis" in topics or "amount_followup" in topics or BARE_AMOUNT.fullmatch(question.strip())):
        named = [key for key in question_topics(question) if key in SUBJECTS]
        if not named and re.search(r"(?:我|自己)[出付]|自付", question):
            named = ["freight"]
        subjects = named or prior or subjects
        if "basis" in topics:
            reason = identify_reason(question) or previous.get("hypothetical_reason") or previous.get("reason")
            cited_case = case if (last_result or {}).get("case_no") or previous.get("focus_case") else active_case
            message, excerpts = explain_source(item, subjects, reason, cited_case)
            if reason and "freight" in subjects and not cited_case and item["stage"] == "delivered":
                eligible = assess_eligibility(item, reason, today)
                quote = freight_quote(item, reason) if any(kind in RETURN_KINDS for kind in eligible.kinds) else None
                if quote and "due" in quote:
                    message = f"寄回报价 ¥{quote['fee']:.2f} 减去本件抵扣 ¥{quote['credit']:.2f}，预计自付 ¥{quote['due']:.2f}。" + message
            return _followup_reply(item, message, evidence=excerpts, reference=True), {**previous, "focus": subjects}
        amounts = [key for key in subjects if key in AMOUNTS]
        if len(amounts) != 1:
            choices = {"freight": "寄回运费多少？", "original_shipping": "原配送费多少？",
                       "refund": "商品退款金额多少？", "product": "商品实付单价多少？", "offer": "店铺方案金额多少？"}
            options = [choices[key] for key in (amounts or ["freight", "refund", "product"])]
            return _followup_reply(item, "你想看哪笔钱？点下面一项，我帮你查。", options), {**previous, "focus": subjects}
        topics = [*amounts, "amount_followup"]
    if not explicit and "policy_menu" in topics and not set(subjects) - {"eligibility"}:
        sections = "退换期限、维修、寄回运费和店铺响应"
        if item.get("platform_version"):
            sections += "，还有运费权益和本店办理指引"
        return _followup_reply(item, f"你这单适用第 {item['terms_version']} 版，包含{sections}。你想看哪项？",
                               [*POLICY_MENU, "寄回运费谁出？"]), previous
    if not explicit and re.search(r"会员.{0,6}(?:包邮|免邮|免运费)|价保|保价|终身保修|假一赔十", question):
        return _followup_reply(item, "你下单时的店铺条款没有单列这项承诺。可以请店铺核对购买时的活动记录。"), {**previous, "focus": []}
    if not explicit and "next_step" in topics and not [key for key in question_topics(question) if key in SUBJECTS]:
        if case:
            topics = ["progress"]
        else:
            topics = ["eligibility"]
    asks_whole_return = any(WHOLE_RETURN.fullmatch(part.strip()) for part in re.split(r"[，,。？?！!；;]", question))
    missing_followup = (not explicit and not identified and previous.get("reason") == "missing" and
                        (asks_whole_return or re.search("单独补|只补|配件怎么补", question)))
    if missing_followup:
        topics = list(dict.fromkeys([*topics, "eligibility"]))
    elif not explicit and WHOLE_RETURN.fullmatch(question.strip()):
        topics = list(dict.fromkeys([*topics, "eligibility"]))
    hypothetical = not explicit and bool(NONFACTUAL.search(question))
    if not explicit and not confirmed_reason and not identified and not hypothetical:
        hint = next((key.removeprefix("confirm_") for key in topics if key.startswith("confirm_")), None)
        rejected = previous.get("rejected_reasons", [])
        open_question = set(subjects) <= {"eligibility", "freight", "product", "return_window"}
        if open_question and (hint or follow_up and "product" not in question_topics(question)):
            context = {**previous, "issue_report": question, "focus": ["freight"] if "freight" in subjects else ["eligibility"]}
            result = _model_clarification(item, follow_up, context)
            if result:
                return result, context
            if hint in rejected or (follow_up and previous.get("asked_facts")):
                return _followup_reply(item, "我记得你前面说的情况。你可以补充商品是什么时候、哪里出现问题的，"
                                       "或者直接说你希望怎么处理，我再帮你核对。"), context
        if (hint in REASON_REPLIES and open_question and not previous.get("reason") and not PACKAGING.search(question)
                and not re.search(r"不是|并非|没有|没坏", question)):
            clarification = ("我可以帮你核对这单的退换条件。你是觉得商品不合适，想退掉这件吗？"
                             if hint == "wrong_purchase" else f"你说的情况可能属于「{REASONS[hint]}」，我这样理解对吗？")
            result = _followup_reply(item, clarification,
                                     ["对" if hint == "wrong_purchase" else REASON_REPLIES[hint], "不是这个原因"])
            focus = ["freight"] if "freight" in subjects or previous.get("awaiting") == "freight" else ["eligibility"]
            return result, {**previous, "focus": focus, "proposed_reason": hint, "issue_report": question,
                            "awaiting": focus[0] if focus == ["freight"] else "reason"}
    if identified and "freight" in prior and re.search(r"说错|说反|弄错|其实|而是|不是", question) and "freight" not in topics:
        topics.append("freight")
    result, context = _answer_order(item, question, explicit, {} if hypothetical and identified else previous,
                                   latest, today, active_case, topics, confirmed_reason=confirmed_reason)
    if not explicit and result["reason"] is None:
        context.update(consultation_context(previous))
    if arrival_report and confirmed_reason == "quality" and result["kinds"]:
        result["suggested_description"] = arrival_report
    if suggest_exchange:
        if "exchange" in result["kinds"]:
            result["recommended_kind"] = "exchange"
            size_request = SIZE_EXCHANGE.search(question)
            target_given = size_follow_up or (size_request and not size_request.group().endswith(("尺码", "尺寸")))
            detail = ("想换的规格已带入申请说明，店铺会确认能提供的规格。" if target_given
                      else "想换的尺码或尺寸可以写进申请说明，店铺会确认能提供的规格。")
            result["answer"] = "这单可以申请换货。" + detail + "\n" + result["answer"]
            context["requested_kind"] = "exchange"
            if result["reason"] != "wrong_purchase":
                result["suggested_description"] = preference_report
        elif result["case_no"] is None:
            result["answer"] = "我看了这单，当前没有可选的换货方式。\n" + result["answer"]
    if retracts_preference or replaces_reason:
        result["reset_suggestion"] = True
    if preference_report and not explicit and context.get("reason") in (None, "wrong_purchase"):
        context["preference_report"] = preference_report
        if result["reason"] == "wrong_purchase" and result["kinds"]:
            result["suggested_description"] = preference_report
    if corrected_missing:
        result["answer"] = "找到了就好，缺件建议已经撤掉。\n" + result["answer"]
        result["reset_suggestion"] = True
    focus = [key for key in topics if key in SUBJECTS]
    if result["reason"] == "missing" and "reship" in result["kinds"]:
        guide = item.get("missing_supplement")
        description = missing_description(question) if identified == "missing" else None
        missing_question = description or previous.get("missing_description") or question
        _, related = ranked_citation(missing_question, supplement_sources(item, "missing"))
        confirming = ELIGIBILITY_CONFIRMATION.fullmatch(question.strip()) or EVIDENCE_SUBMISSION.fullmatch(question.strip())
        if missing_followup and not confirming:
            guidance = "已收到的商品可以留着，不用为申请补发把整套寄回。"
            result["answer"] = guidance + ("\n" + result["answer"] if len(focus) > 1 else "")
        elif identified == "missing" and not confirming and (not focus or focus == ["eligibility"]) and not PACKAGING.search(question) and not empty_package_report(question):
            result["answer"] = "可以申请补发缺少的配件。先留着已经收到的商品，不用为申请补发把整套寄回。"
        elif not confirming and identified == "missing" and "eligibility" not in topics:
            result["answer"] += "\n" + result["eligibility"]["message"]
        if description:
            context["missing_description"] = description
        if related and not confirming:
            result["answer"] += "\n" + related.text
            context["missing_guide"] = int(related.article_id.removeprefix("supplement-"))
        elif guide and not confirming:
            result["answer"] += "\n" + guide["note"]
            context["missing_guide"] = guide["id"]
    if context.get("reason") == "missing" and not hypothetical:
        description = context.get("missing_description") or previous.get("missing_description")
        if description:
            context["missing_description"] = description
            if result["reason"] == "missing" and "reship" in result["kinds"]:
                result["suggested_description"] = result["suggested_description"] or description
    if not explicit and not hypothetical and context.get("reason"):
        captured = result.get("suggested_description")
        if not captured and context["reason"] == previous.get("reason"):
            captured = previous.get("issue_description")
        if captured:
            context["issue_description"] = captured
            if result["reason"] and result["kinds"]:
                result["suggested_description"] = captured
    if previous.get("awaiting") == "freight" and result["reason"] and "freight" not in focus:
        focus.append("freight")
    if not focus and result["eligibility"]:
        focus.append("eligibility")
    if not focus and context.get("awaiting") == "damage":
        focus.append("photos")
    context["focus"] = focus
    context["focus_case"] = result["case_no"]
    if hypothetical and identified:
        # 假设只用于试算，不向表单或下一轮写入用户尚未确认的实际原因。
        result["answer"] = "如果是「" + REASONS[identified] + "」：" + result["answer"]
        result.update(reason=None, kinds={}, suggested_description=None, evidence_checklist=[], reset_suggestion=False)
        context = {**previous, "focus": focus, "hypothetical_reason": identified}
    return result, context


def _followup_reply(item: dict, message: str, choices: list[str] | None = None, *,
                    evidence: list[dict] | None = None, reference: bool = False) -> dict:
    """只解释或澄清，不改写买家已经填好的售后表单。"""
    return {"answer": message, "quick_replies": choices or [], "evidence": evidence or [],
            "evidence_requested": reference, "reason": None, "eligibility": None, "kinds": {},
            "case_no": None, "freight": None, "intervention": None, "suggested_description": None,
            "terms_title": item["terms_title"], "terms_version": item["terms_version"]}

