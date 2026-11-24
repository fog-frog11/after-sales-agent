"""判定商城候选条款能否支持买家具体追问。"""

import re
from dataclasses import dataclass

from app.policies.evidence import PolicySource
from app.policies.retrieval_ranking import article_text


@dataclass(frozen=True)
class CitationNeed:
    """买家问点的触发表达及原文必须覆盖的概念。"""

    label: str
    asks: str
    requires: tuple[str, ...]


# 先识别具体承诺，再识别一般事项。相似候选必须同时满足该问点的条件。
EXPLICIT_PROMISES = (
    CitationNeed("终身换新", r"(?:终身|永久).{0,8}(换新|换货)", (r"终身|永久", r"换新|换货")),
    CitationNeed("终身保修", r"(?:终身|永久).{0,8}(维修|保修|质保)", (r"终身|永久", r"维修|保修|质保")),
    CitationNeed("分期付款", r"分期|花呗|白条", (r"分期|花呗|白条",)),
    CitationNeed("会员运费权益", r"会员.{0,14}(包邮|免邮|免运费|免寄回运费)|(?:免邮|免运费).{0,12}会员", (r"会员", r"包邮|免邮|免运费|免寄回运费")),
    CitationNeed("保价", r"保价|价保|补差价", (r"保价|价保|补差价",)),
    CitationNeed("上门拆旧", r"(?:上门|到家).{0,8}拆旧", (r"上门|到家", r"拆旧")),
    CitationNeed("上门维修", r"(?:上门|到家).{0,8}(?:修理|维修|检修)", (r"上门|到家", r"维修|检修")),
    CitationNeed("限时加急配送", r"跑腿|即时配送|加急.{0,5}送|(?:两|二|2)小时.{0,5}送", (r"跑腿|即时配送|加急|(?:两|二|2)\s*小时", r"送|配送")),
)
NEEDS = (
    *EXPLICIT_PROMISES,
    CitationNeed("接收器收纳位", r"(?:接收器|接收头|插电脑的.{0,6}(?:小头|接头)).{0,22}(仓|收纳|藏|拆)|(?:电池仓|收纳位).{0,15}接收", (r"接收器", r"电池仓|收纳位")),
    CitationNeed("滑轨的左右方向", r"(?:滑轨|滑道|导轨).{0,18}(左右|左边|右边|哪边)|(?:左右|左边|右边).{0,14}(滑轨|滑道|导轨)", (r"滑轨|导轨", r"左|右")),
    CitationNeed("到付", r"到付|收件人付快递费", (r"到付",)),
    CitationNeed("撤销售后", r"撤销|撤回|取消.{0,7}(申请|售后)|(?:把|将).{0,8}(?:申请|售后).{0,5}(?:关掉|取消|撤掉)",
                 (r"可.{0,12}(撤销|撤回)|允许.{0,6}(撤销|撤回)", r"申请|售后")),
    CitationNeed("售后运单", r"(?:补发|换货|维修).{0,20}(?:运单|单号)|(?:新|原来).{0,5}(?:运单|单号)", (r"换货|补发|维修返还|售后包裹", r"新运单|单号")),
    CitationNeed("包装留证", r"(?:外盒|纸箱|外箱|包装|鞋盒).{0,12}(?:拍|取证|证明|压|破)|(?:拍|取证).{0,12}(?:外盒|纸箱|外箱|包装|鞋盒)", (r"外盒|纸箱|外箱|包装|鞋盒", r"拍|照片|取证")),
    CitationNeed("退款到账渠道", r"原路退|退款.{0,8}(哪里|到哪|退到|渠道)|钱退到", (r"原付款渠道|原支付|原路", r"退款|退回")),
    CitationNeed("购买时配送费", r"原配送费|当初.{0,7}(?:配送费|运费)|下单.{0,10}配送费", (r"原配送费|下单.{0,10}(?:发货|配送)费",)),
    CitationNeed("寄回运费", r"运费|邮钱|邮费|寄回.{0,7}费|退回.{0,7}快递费|垫付", (r"寄回运费|寄回报价|寄件费用|退货运费", r"承担|垫付|抵扣|自付|返还")),
    CitationNeed("平台介入", r"平台介入|平台处理|转人工|找人工", (r"平台介入|平台处理", r"超时|拒绝|协商")),
    CitationNeed("退换或申请期限", r"期限|截止|(?:几|多少)天.{0,7}(?:退|申请)|多久.{0,5}(?:退|申请)|超期", (r"签收|截止|期限", r"退换|申请|售后")),
    CitationNeed("补发需要的信息", r"(?:补发|补寄|缺件).{0,12}(?:信息|材料|照片|证明|凭证|怎么填)",
                 (r"配件|零件|部件|缺件|五金|型号|鞋带|线材", r"请说|提供|填写|拍|安装步骤|核对|确认")),
    CitationNeed("补发配件", r"缺件|少件|少发|少了|补配件|补寄|补发|补给|没附|不给齐|没给齐|漏发|没装进去",
                 (r"配件|零件|线材|部件|纽扣|扣子|鞋带|五金|套装|少件|缺件|说明书", r"补发|补寄|单独补|备件|安排.{0,5}零件|可.{0,5}提供|可.{0,12}申报")),
)
ENTITY_TERMS = (
    r"接收器|接收头|插电脑的.{0,6}(?:小头|接头)",
    r"滑轨|滑道|导轨",
    r"纽扣|备用扣|同色小扣|扣子",
    r"鞋带|系鞋的.{0,3}带",
    r"镜头盖|镜头.{0,6}(?:盖|圆盖)",
    r"螺丝|螺钉|垫片|螺母|螺帽|五金",
)
LOOKUP_PREFIX = re.compile(r"^查(?:一下)?(?:本单|该订单|这单)?(?:的)?(?:售后)?条款(?:\s*[：:]|$)")


def needs_citation_check(question: str) -> bool:
    """明确查本单原文或询问可验证的额外承诺时进入检查。"""
    return bool(LOOKUP_PREFIX.search(question.strip()) or any(re.search(need.asks, question) for need in EXPLICIT_PROMISES))


def find_citation(question: str, candidates: list[PolicySource]) -> tuple[str, PolicySource | None]:
    """按排序依次验证来源文本，无法判断问点时要求澄清。"""
    need = next((rule for rule in NEEDS if re.search(rule.asks, question)), None)
    if need is None:
        return "clarify", None
    related = [entity for entity in ENTITY_TERMS if re.search(entity, question)]
    for article in candidates:
        text = article_text(article)
        supporting_text = article.text if need in EXPLICIT_PROMISES else text
        if all(re.search(requirement, supporting_text) for requirement in need.requires) and all(
            re.search(entity, text) for entity in related
        ):
            return need.label, article
    return need.label, None
