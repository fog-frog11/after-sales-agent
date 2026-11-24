"""将内置模拟售后手册导入已启动的本机 Milvus。"""

from app.storage.milvus_policies import MilvusPolicies
from app.policies.articles import ARTICLES
from app.settings import local_setting


def main() -> None:
    """读取配置并按固定条款编号导入两家店铺的手册。"""
    url = local_setting("MILVUS_URL")
    if not url:
        raise SystemExit("请在 .env 配置 MILVUS_URL 后再导入。")
    MilvusPolicies(url).index_articles(ARTICLES)
    print(f"已导入 {len(ARTICLES)} 条模拟店铺政策。")


if __name__ == "__main__":
    main()
