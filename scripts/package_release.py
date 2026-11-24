"""根据公开源码清单打包当前主版本。传入一个新的 ZIP 路径。"""

import argparse
import json
import re
from hashlib import sha256
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


ROOT = Path(__file__).resolve().parents[1]
TOP_LEVEL = (
    ".env.example", ".gitignore", ".dockerignore", "Dockerfile", "README.md", "RELEASE_GUIDE.md", "DEVELOPMENT_HISTORY.md", "THIRD_PARTY_NOTICES.md", "compose.yaml",
    "package.json", "package-lock.json", "requirements.txt", "requirements-dev.txt",
    "requirements-mcp.txt", "requirements-retrieval.txt", "server.py", ".github/workflows/verify.yml",
    ".github/workflows/docker-smoke.yml",
)
SOURCE_SUFFIXES = {
    "app": {".py", ".json"}, "scripts": {".py", ".ps1"}, "sql": {".sql"},
    "tests": {".py", ".mjs", ".json"}, "web": {".js", ".jsx", ".html", ".css", ".jpg"},
    "openspec": {".md", ".yaml"}, "milvus": {".yaml"}, "third_party": {".txt"},
}


def release_files() -> list[Path]:
    """加入源码及自编试题，运行目录和下载数据不进入归档。"""
    paths = {ROOT / name for name in TOP_LEVEL}
    for folder, suffixes in SOURCE_SUFFIXES.items():
        for path in (ROOT / folder).rglob("*"):
            names = path.relative_to(ROOT).parts
            if any((name.startswith(".") and name != ".openspec.yaml")
                   or name in {"__pycache__", "commerce-build"} for name in names):
                continue
            if path.is_file() and path.suffix in suffixes:
                paths.add(path)
    paths.update((ROOT / "scripts/fixtures").glob("mall_*.json"))
    paths.update((ROOT / "scripts/fixtures").glob("native-zh-*.json"))
    paths.add(ROOT / "scripts/fixtures/tau-retail-zh.json")
    for path in paths:
        if not path.resolve().is_relative_to(ROOT) or not path.is_file():
            raise ValueError(f"无法归档 {path.relative_to(ROOT)}")
    return sorted(paths)


def main() -> None:
    """创建源码归档并输出校验摘要。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    output = parser.parse_args().output
    version = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))["version"]
    if not re.fullmatch(r"[1-9][0-9]*\.0\.0", version):
        parser.error("发布包使用主版本号，例如 2.0.0。")
    paths = release_files()
    output.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(output, "x", compression=ZIP_DEFLATED) as archive:
        for path in paths:
            archive.write(path, Path(f"after-sales-agent-v{version}") / path.relative_to(ROOT))
    with output.open("rb") as archive:
        digest = sha256(archive.read()).hexdigest()
    print(f"{len(paths)} 个文件，SHA-256 {digest}")


if __name__ == "__main__":
    main()
