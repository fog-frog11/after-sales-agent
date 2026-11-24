"""保留从项目根目录启动售后工作台的入口。"""

from app.api import configured_app, create_app, main


__all__ = ["configured_app", "create_app", "main"]


if __name__ == "__main__":
    main()
