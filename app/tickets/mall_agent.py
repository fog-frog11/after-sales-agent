"""兼容早期导入路径，商城实现在 app.mall.agent。"""

import sys

from app.mall import agent as implementation

sys.modules[__name__] = implementation
