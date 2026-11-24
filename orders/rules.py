"""定义订单、店铺政策和退货期限判断，包含供本地演示使用的样本。"""

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class Order:
    """保存订单号、签收日期和所属店铺。未签收时签收日期为空。"""

    order_id: str
    signed_on: date | None
    shop_id: str | None = None


OrderLookup = Callable[[str], Order | None]


@dataclass(frozen=True)
class ReturnPolicy:
    """描述店铺适用的政策名称和允许退货的天数。"""

    shop_name: str
    policy_name: str
    return_window_days: int


PolicyLookup = Callable[[str], ReturnPolicy | None]


POLICIES = {
    "demo-standard": ReturnPolicy("青禾生活馆", "青禾签收后 7 天退货政策", 7),
    "demo-extended": ReturnPolicy("栖木家居店", "栖木签收后 15 天退货政策", 15),
}

ORDERS = {
    "A101": Order("A101", date(2026, 11, 6), "demo-standard"),
    "A102": Order("A102", date(2026, 11, 5), "demo-standard"),
    "A103": Order("A103", None, "demo-extended"),
    "A104": Order("A104", date(2026, 11, 5), "demo-extended"),
}
DEMO_DATE = date(2026, 11, 13)


def get_order(order_id: str) -> Order | None:
    """规范化订单号，从内存样本中读取订单。"""
    return ORDERS.get(order_id.strip().upper())


def get_policy(shop_id: str) -> ReturnPolicy | None:
    """按店铺编号读取对应的本地演示政策。"""
    return POLICIES.get(shop_id)


def check_return(
    order_id: str,
    as_of: date,
    return_window_days: int = 7,
    *,
    order_lookup: OrderLookup = get_order,
) -> dict[str, object]:
    """按照模拟店铺的签收后退货期限判断。"""
    if not order_id.strip():
        return {"status": "missing_order_id", "message": "请提供订单号。"}

    return evaluate_return(order_lookup(order_id.strip().upper()), as_of, return_window_days)


def evaluate_return(
    order: Order | None, as_of: date, return_window_days: int,
) -> dict[str, object]:
    """根据已查到的订单、查询日期和适用期限，返回判断及依据。"""
    if order is None:
        return {"status": "order_not_found", "message": "查不到这笔订单。"}

    if order.signed_on is None:
        return {
            "status": "not_signed",
            "order_id": order.order_id,
            "message": "订单尚未签收，退货期限还未开始计算。",
        }

    days_since_delivery = (as_of - order.signed_on).days
    if days_since_delivery < 0:
        return {
            "status": "invalid_delivery_date",
            "order_id": order.order_id,
            "message": "签收日期晚于查询日期，请核对订单记录。",
        }

    eligible = days_since_delivery <= return_window_days
    return {
        "status": "eligible" if eligible else "outside_window",
        "order_id": order.order_id,
        "signed_on": order.signed_on.isoformat(),
        "days_since_delivery": days_since_delivery,
        "return_window_days": return_window_days,
        "message": "在模拟退货期限内。" if eligible else "已超过模拟退货期限。",
    }


if __name__ == "__main__":
    order_id = input("请输入模拟订单号（A101、A102、A103）：")
    result = check_return(order_id, as_of=DEMO_DATE)
    print(json.dumps(result, ensure_ascii=False, indent=2))
