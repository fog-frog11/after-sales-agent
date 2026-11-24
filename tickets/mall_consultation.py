"""兼容早期导入路径，商城实现在 app.mall.consultation。"""

import sys

from app.mall import consultation as implementation

sys.modules[__name__] = implementation
