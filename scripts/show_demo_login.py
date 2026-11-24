"""显示指定虚构角色的本机随机登录口令，不读取其他账号。"""

import argparse
import json

from app.access import ACCOUNT_FILE
from app.mall_accounts import DIRECTORY_ACCOUNT_FILE, DIRECTORY_ROLES


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("account", choices=["staff", *DIRECTORY_ROLES], help="例如 buyer-001、shop-001、staff")
    account = parser.parse_args().account
    path = ACCOUNT_FILE if account == "staff" else DIRECTORY_ACCOUNT_FILE
    credentials = json.loads(path.read_text(encoding="utf-8"))
    print(f"账号：{account}\n口令：{credentials[account]}")


if __name__ == "__main__":
    main()
