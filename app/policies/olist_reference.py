"""定义 Olist 订单共用的巴西公开规则范围和显示说明。"""

OLIST_REFERENCE_SCOPE = "olist-br-public"
OLIST_REFERENCE_COLLECTION = "after_sales_olist_br_reference"
OLIST_REFERENCE_NAME = "巴西电商公开规则"


def label_olist_reference(result: dict) -> dict:
    """将公共依据明确标在答复上，方便买家与客服确认适用边界。"""
    heading = "巴西电商公开规则（中文整理）。店铺具体约定需人工确认。"
    if result["status"] == "no_matches" and not result.get("items"):
        return {**result, "answer": f"{heading}\n\n当前公开规则没有足够依据回答此问题，请人工核对。"}
    return {**result, "answer": f"{heading}\n\n{result['answer']}"}
