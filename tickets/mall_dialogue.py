"""兼容早期导入路径，商城实现在 app.mall.dialogue。"""

import sys

from app.mall import dialogue as implementation

sys.modules[__name__] = implementation
