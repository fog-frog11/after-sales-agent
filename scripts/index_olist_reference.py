"""将有出处的巴西电商公开资料写入独立的本机 Milvus 集合。"""

import argparse
import json

from app.policies.olist_reference import OLIST_REFERENCE_COLLECTION, OLIST_REFERENCE_SCOPE
from app.policies.search import PolicyIndexError
from app.settings import ROOT, local_setting
from app.storage.milvus_policies import MilvusPolicies


def main() -> None:
    """默认显示条款数量，收到 --apply 后按稳定编号写入。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    path = ROOT / "scripts/fixtures/olist-pt-br-public-reference.json"
    clauses = [{**row, "shop_id": OLIST_REFERENCE_SCOPE, "text": f"{row['title']}。{row['text']}"}
               for row in json.loads(path.read_text(encoding="utf-8"))]
    if len({row["id"] for row in clauses}) != len(clauses):
        parser.error("公开规则编号有重复。")
    print(f"已准备 {len(clauses)} 条巴西公开电商规则。", flush=True)
    if args.apply:
        url = local_setting("MILVUS_URL")
        if not url:
            parser.error("请先配置本机 MILVUS_URL。")
        index = MilvusPolicies(url, collection_name=OLIST_REFERENCE_COLLECTION)
        try:
            index.index_articles(clauses)
            print(f"Milvus 已加载 {index.count_articles(OLIST_REFERENCE_SCOPE)} 条公开规则。")
        except PolicyIndexError as error:
            parser.error(str(error))


if __name__ == "__main__":
    main()
