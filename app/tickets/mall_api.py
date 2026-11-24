"""兼容早期导入路径，商城实现在 app.mall.api。"""

import sys

from app.mall import api as implementation

sys.modules[__name__] = implementation
