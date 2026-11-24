"""兼容早期导入路径，商城实现在 app.mall.offers。"""

import sys

from app.mall import offers as implementation

sys.modules[__name__] = implementation
