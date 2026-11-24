"""商城批量开设账号的编号、角色和口令文件。"""

from app.settings import ROOT


DIRECTORY_ACCOUNT_FILE = ROOT / ".local" / "mall-directory-accounts.json"
ACCOUNT_BOOK_FILE = ROOT / ".local" / "mall-logins.json"
BUYER_CODES = tuple(f"buyer-{number:03d}" for number in range(1, 51))
SHOP_CODES = tuple(f"shop-{number:03d}" for number in range(1, 101))
DIRECTORY_ROLES = {**dict.fromkeys(BUYER_CODES, "buyer"), **dict.fromkeys(SHOP_CODES, "merchant")}
