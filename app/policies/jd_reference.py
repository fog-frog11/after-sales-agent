"""从已验证的京东历史订单确定中文条款的适用范围。"""

from app.orders.logistics import LogisticsOrder


JD_REFERENCE_COLLECTION = "after_sales_jd_cn_reference"
JD_REFERENCE_SCOPE = "jd-cn-public"
JD_SELF_URL = "https://help.jd.com/user/issue/131-496.html"
JD_MARKET_URL = "https://help.jd.com/user/issue/464-1501.html"
SAMR_URL = "https://sjfg.samr.gov.cn/law/file/pdf/3235243/1663578568290.pdf"
JD_REFERENCE_SOURCES = {"jd-self": (JD_SELF_URL, SAMR_URL), "jd-market": (JD_MARKET_URL, SAMR_URL),
                        "jd-general": (SAMR_URL,)}


def jd_policy_scope(order: LogisticsOrder) -> str:
    """自营须有对应配送证据，混合和来源不明订单只使用通用依据。"""
    if order.channel == "self" and order.delivered_on is not None:
        return "jd-self"
    if order.channel == "market":
        return "jd-market"
    return "jd-general"


def label_jd_reference(result: dict) -> dict:
    """明确当前公开规则与历史订单的适用边界。"""
    heading = "中国电商公开规则 · 历史订单仅用于演示，具体售后资格需人工核对。"
    if result["status"] == "no_matches" and not result.get("items"):
        return {**result, "answer": f"{heading}\n\n暂无可核对的依据，请申请人工处理。"}
    return {**result, "answer": f"{heading}\n\n{result['answer']}"}
