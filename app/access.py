"""管理本机演示账号、会话和订单归属。"""

import json
from collections import Counter
from dataclasses import dataclass
from hashlib import sha256
from hmac import compare_digest
from pathlib import Path
from secrets import token_urlsafe
from threading import Lock
from time import monotonic
from typing import Literal

from app.orders.logistics import HISTORICAL_ORDER_PATTERN
from app.mall_accounts import DIRECTORY_ACCOUNT_FILE, DIRECTORY_ROLES
from app.settings import ROOT


ACCOUNT_FILE = ROOT / ".local" / "demo-accounts.json"
MERCHANT_ACCOUNT_FILE = ROOT / ".local" / "merchant-accounts.json"
SESSION_COOKIE = "after_sales_session"
SESSION_SECONDS = 8 * 60 * 60
Role = Literal["buyer", "staff", "merchant"]
ACCOUNT_ROLES: dict[str, Role] = {"buyer-a": "buyer", "buyer-b": "buyer", "staff": "staff"}
MERCHANT_SHOPS = {"shop-qh": "demo-standard", "shop-qm": "demo-extended"}
MALL_LOGIN_ROLES: dict[str, Role] = {**ACCOUNT_ROLES, **dict.fromkeys(MERCHANT_SHOPS, "merchant"), **DIRECTORY_ROLES}
ACCOUNT_GROUPS = {"buyer": "买家", "merchant": "店铺", "staff": "平台客服"}
ORDER_OWNERS = {"A101": "buyer-a", "A102": "buyer-a", "A103": "buyer-b", "A104": "buyer-b"}
OLIST_EXAMPLES = ("OL-00010242FE", "OL-000E906B78", "OL-002F19A65A", "OL-00310B0C75")
OLIST_OWNER_SUFFIXES = {"buyer-a": "02468ACE", "buyer-b": "13579BDF"}


@dataclass(frozen=True)
class Account:
    """服务器确认的账号身份和操作角色。"""

    account_id: str
    role: Role
    display_name: str | None = None


def order_owner(order_id: str) -> str | None:
    """查询演示归属。样本按末位奇偶分给两名买家。"""
    if HISTORICAL_ORDER_PATTERN.fullmatch(order_id):
        return "buyer-a" if order_id[-1].upper() in OLIST_OWNER_SUFFIXES["buyer-a"] else "buyer-b"
    return ORDER_OWNERS.get(order_id)


def load_credentials(path: Path = ACCOUNT_FILE, *, accounts=ACCOUNT_ROLES) -> dict[str, str]:
    """读入本机生成的登录口令，校验账号齐全和配置格式。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if (isinstance(data, dict) and data.keys() == accounts.keys()
                and all(isinstance(value, str) and len(value) >= 20 for value in data.values())
                and len(set(data.values())) == len(accounts)):
            return data
    except (OSError, ValueError):
        pass
    raise RuntimeError(f"本机演示账号未初始化或配置有误，请检查 {path.name} 的配置。")


def build_account_book(names: dict[str, str]) -> dict:
    """按角色整理中文名称和原口令文件位置。"""
    groups = {group: {} for group in ACCOUNT_GROUPS.values()}
    for account, role in {**DIRECTORY_ROLES, "staff": "staff"}.items():
        name = "平台客服" if role == "staff" else names[account]
        source = ACCOUNT_FILE if role == "staff" else DIRECTORY_ACCOUNT_FILE
        groups[ACCOUNT_GROUPS[role]][account] = {"名称": name, "密码文件": "./" + source.name, "密码键": account}
    return groups


class DemoAccess:
    """校验随机口令并保存临时会话，角色只能由服务端分配。"""

    def __init__(self, credentials: dict[str, str], *, names: dict[str, str] | None = None):
        """为具备口令的账号登记显示名，只有唯一且不与编号冲突的名称可用于登录。"""
        self.credentials = {name: sha256(secret.encode()).digest() for name, secret in credentials.items()}
        self.names = {account_id: name for account_id, name in (names or {}).items()
                      if account_id in credentials and name}
        name_counts = Counter(self.names.values())
        self.aliases = {name: account_id for account_id, name in self.names.items()
                        if name_counts[name] == 1 and name not in credentials}
        self.sessions: dict[str, tuple[Account, float]] = {}
        self.lock = Lock()

    def login(self, account_id: str, secret: str, previous_token: str | None = None) -> tuple[str, Account] | None:
        """验证口令并发新会话，成功时撤销此浏览器的旧令牌。"""
        account_id = self.aliases.get(account_id, account_id)
        expected = self.credentials.get(account_id)
        matches = compare_digest(sha256(secret.encode()).digest(), expected or bytes(32))
        if expected is None or not matches:
            return None
        role = MALL_LOGIN_ROLES.get(account_id)
        if role is None:
            return None
        account = Account(account_id, role, self.names.get(account_id))
        token = token_urlsafe(32)
        now = monotonic()
        with self.lock:
            self.sessions = {key: session for key, session in self.sessions.items() if session[1] > now}
            self.sessions.pop(previous_token, None)
            self.sessions[token] = (account, now + SESSION_SECONDS)
        return token, account

    def account(self, token: str | None) -> Account | None:
        """按浏览器提供的不透明令牌读取尚未到期的身份。"""
        with self.lock:
            session = self.sessions.get(token)
            if session is not None and session[1] > monotonic():
                return session[0]
        return None

    def logout(self, token: str | None) -> None:
        """撤销指定会话令牌。"""
        with self.lock:
            self.sessions.pop(token, None)
