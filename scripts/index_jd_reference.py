"""把已下载的中文官方零售条款加载到国内订单的独立 Milvus 集合。"""

import argparse
import sys

from app.policies.evidence import PolicyIndexError
from app.policies.jd_reference import JD_REFERENCE_COLLECTION, JD_REFERENCE_SCOPE
from app.settings import ROOT, local_setting
from app.storage.milvus_policies import MilvusPolicies
from scripts.native_terms_sources import load_articles


def main() -> None:
    """复用公开中文原文，先预览，收到 --apply 后写入。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    sys.path.append(str(ROOT / ".local/retrieval-deps"))
    try:
        raw, _ = load_articles(ROOT / ".local/datasets/after-sales-terms/native-zh")
        articles = [{**row, "shop_id": JD_REFERENCE_SCOPE} for row in raw]
        print(f"已核对 {len(articles)} 段中文官方条款。", flush=True)
        if args.apply:
            url = local_setting("MILVUS_URL")
            if not url:
                raise ValueError("请先配置本机 MILVUS_URL。")
            index = MilvusPolicies(url, collection_name=JD_REFERENCE_COLLECTION)
            index.index_articles(articles)
            print(f"Milvus 已加载 {index.count_articles(JD_REFERENCE_SCOPE)} 段中文条款。")
    except (OSError, ImportError, ValueError, PolicyIndexError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
