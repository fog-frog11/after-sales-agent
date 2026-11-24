"""兼容早期导入路径，商城实现在 app.mall.photos。"""

import sys

from app.mall import photos as implementation

sys.modules[__name__] = implementation
