"""检查公开源码清单与可选 ZIP；仅报告问题，不修改或清理文件。"""

import argparse
import hashlib
import re
from pathlib import Path
from zipfile import ZipFile

from scripts.package_release import ROOT, release_files


TOKEN = re.compile(r"\b(?:sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16})\b")
PRIVATE_KEY = re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")
PRIVATE_PATH = re.compile(r"(?:C:[\\/]Users[\\/][^\\/\s]+|D:[\\/]AAA找工作)", re.IGNORECASE)
DSN = re.compile(r"postgres(?:ql)?://[^:/\s]+:([^@\s]+)@", re.IGNORECASE)
FORBIDDEN_PARTS = {".local", "node_modules", "__pycache__", ".git", "commerce-build"}


def inspect_text(path: Path, content: bytes) -> list[str]:
    if path.suffix.lower() in {".jpg", ".png", ".webp", ".onnx"}:
        return []
    value = content.decode("utf-8-sig")
    issues = []
    if TOKEN.search(value) or PRIVATE_KEY.search(value):
        issues.append("疑似令牌或私钥")
    if PRIVATE_PATH.search(value):
        issues.append("本机绝对路径")
    if any("{" not in match.group(1) and "}" not in match.group(1)
           for match in DSN.finditer(value)):
        issues.append("疑似写死的数据库口令")
    return issues


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, help="额外核对 ZIP 的文件名与源码字节")
    options = parser.parse_args()
    paths = release_files()
    issues = []
    for path in paths:
        relative = path.relative_to(ROOT)
        if any(part in FORBIDDEN_PARTS or part.startswith(".env.") and part != ".env.example"
               for part in relative.parts):
            issues.append((str(relative), "运行产物或配置进入源码清单"))
        try:
            for issue in inspect_text(path, path.read_bytes()):
                issues.append((str(relative), issue))
        except UnicodeError:
            issues.append((str(relative), "文本编码无法检查"))
    if options.archive:
        prefix = "after-sales-agent-v" + __import__("json").loads((ROOT / "package.json").read_text("utf-8"))["version"] + "/"
        expected = {prefix + path.relative_to(ROOT).as_posix(): path for path in paths}
        with ZipFile(options.archive) as archive:
            names = archive.namelist()
            if len(names) != len(set(names)) or set(names) != set(expected):
                issues.append((options.archive.name, "ZIP 文件清单与源码不一致或含重复项"))
            for name in set(names) & set(expected):
                if hashlib.sha256(archive.read(name)).digest() != hashlib.sha256(expected[name].read_bytes()).digest():
                    issues.append((name, "ZIP 内容与当前源码不一致"))
            if archive.testzip() is not None:
                issues.append((options.archive.name, "ZIP 校验失败"))
    for filename, issue in issues:
        print(f"{filename}: {issue}")
    if issues:
        raise SystemExit(f"公开检查未通过：{len(issues)} 项。")
    print(f"公开检查通过：{len(paths)} 个文件；配置、运行目录和高置信度密钥检查无异常。")
    if not (ROOT / "LICENSE").exists():
        print("授权提示：尚无项目 LICENSE；公开可见不等于授权第三方复用。")


if __name__ == "__main__":
    main()
