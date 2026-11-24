"""兼容早期导入路径，商城实现在 app.mall.refund_summary。"""

import sys

from app.mall import refund_summary as implementation

sys.modules[__name__] = implementation
