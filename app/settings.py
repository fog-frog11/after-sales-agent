"""从环境变量或项目 .env 读取本机配置。"""

import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def local_setting(setting: str) -> str:
    """优先读环境变量，缺少时从项目的 .env 读取指定配置。"""
    value = os.environ.get(setting, "").strip()
    if value:
        return value
    env_file = ROOT / ".env"
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8-sig").splitlines():
            name, _, value = line.partition("=")
            if name.strip() == setting:
                return value.strip().strip('"\'')
    return ""
