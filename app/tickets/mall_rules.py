"""兼容早期导入路径，商城实现在 app.mall.rules。"""

import sys

from app.mall import rules as implementation

sys.modules[__name__] = implementation
