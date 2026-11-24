"""在已启动的工作台容器内只读验收依赖、三角色登录与页面。"""

import json
from pathlib import Path
from urllib.request import urlopen

import httpx
import psycopg

from app.access import ACCOUNT_FILE
from app.mall_accounts import DIRECTORY_ACCOUNT_FILE
from app.policies.semantic_terms import E5Encoder
from app.settings import local_setting


def main() -> None:
    accounts = json.loads(DIRECTORY_ACCOUNT_FILE.read_text(encoding="utf-8"))
    staff = json.loads(ACCOUNT_FILE.read_text(encoding="utf-8"))
    assert len(accounts) == 150 and "staff" in staff
    paths = {"buyer-001": "/mall/orders", "shop-001": "/merchant/mall", "staff": "/staff/disputes"}
    roles = {"buyer-001": "buyer", "shop-001": "merchant", "staff": "staff"}
    with httpx.Client(base_url="http://127.0.0.1:8765", timeout=10) as client:
        for asset in ("/login", "/commerce-build/workbench.js", "/commerce-build/workbench.css", "/parcel-studio.jpg"):
            assert client.get(asset).status_code == 200, asset
        for account, home in paths.items():
            password = staff[account] if account == "staff" else accounts[account]
            with httpx.Client(base_url="http://127.0.0.1:8765", timeout=10, follow_redirects=False) as session:
                reply = session.post("/api/session/login", json={"account_id": account, "secret": password})
                assert reply.status_code == 200 and reply.json()["role"] == roles[account], account
                assert session.get(home).status_code == 200, home
                assert session.get("/api/session").json()["account_id"] == account
                other = "/merchant/mall" if account != "shop-001" else "/mall/orders"
                assert session.get(other).status_code == 303, (account, other)
    with psycopg.connect(local_setting("ORDER_DATABASE_URL")) as connection:
        connection.execute("SET TRANSACTION READ ONLY")
        counts = {name: connection.execute(f"SELECT count(*) FROM public.{name}").fetchone()[0]
                  for name in ("mall_orders", "mall_items", "mall_buyer_profiles", "mall_shop_profiles")}
    assert counts["mall_orders"] >= 304 and counts["mall_items"] >= 604
    assert counts["mall_buyer_profiles"] >= 50 and counts["mall_shop_profiles"] >= 100
    with urlopen("http://milvus:9091/healthz", timeout=5) as response:
        assert response.status == 200
    model_dir = Path(local_setting("MALL_E5_MODEL_DIR"))
    assert model_dir.is_dir()
    vector = E5Encoder(model_dir).encode(["退货运费谁承担"], query=True)
    assert vector.shape == (1, 384)
    print(json.dumps({"passed": True, "roles": list(roles.values()), "counts": counts,
                      "milvus": "healthy", "e5_dimensions": vector.shape[1]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
