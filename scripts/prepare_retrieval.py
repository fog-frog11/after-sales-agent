"""检查或下载主聊天使用的 E5 ONNX 文件，不改动已有文件。"""

import argparse
import hashlib
from pathlib import Path
from urllib.request import urlopen

from app.settings import ROOT


MODEL_DIR = ROOT / ".local/models/multilingual-e5-small"
FILES = {
    "model_int8.onnx": (
        "https://huggingface.co/Xenova/multilingual-e5-small/resolve/main/onnx/model_int8.onnx",
        "4d24e2bc01a447951524466ef533e52944bf48509e6552810bcee1a2711cb02c",
    ),
    "tokenizer.json": (
        "https://huggingface.co/Xenova/multilingual-e5-small/resolve/main/tokenizer.json",
        "0b44a9d7b51c3c62626640cda0e2c2f70fdacdc25bbbd68038369d14ebdf4c39",
    ),
}


def digest(path: Path) -> str:
    checksum = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            checksum.update(chunk)
    return checksum.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download", action="store_true", help="仅下载缺少的官方模型文件；已有文件只校验")
    parser.add_argument("--model-dir", type=Path, default=MODEL_DIR,
                        help="模型存放目录；容器镜像可使用独立于运行数据卷的目录")
    options = parser.parse_args()
    model_dir = options.model_dir
    model_dir.mkdir(parents=True, exist_ok=True)
    for name, (url, expected) in FILES.items():
        path = model_dir / name
        if not path.exists() and options.download:
            # 独占创建；下载中断时保留文件供人工检查，不自动清理或覆盖。
            with urlopen(url, timeout=60) as response, path.open("xb") as output:
                for chunk in iter(lambda: response.read(1024 * 1024), b""):
                    output.write(chunk)
        if not path.is_file():
            raise SystemExit(f"缺少 {name}。运行 --download 或从模型页下载后放入 {model_dir}。")
        if digest(path) != expected:
            raise SystemExit(f"{name} 的 SHA-256 与已验收版本不符；请检查文件，脚本不会覆盖它。")
        print(f"{name}: SHA-256 验证通过")
    print("E5 模型文件齐全；CPU 依赖另按 README 安装。")


if __name__ == "__main__":
    main()
