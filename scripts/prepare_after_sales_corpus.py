"""下载并核对 QingshanAI 的售后合成语料，保留原始文件和来源信息。"""

import argparse
import hashlib
import json
import re
import unicodedata
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from app.settings import ROOT


DATASET = "QingshanAI/ecom-after-sale-synthetic"
DIRECTORY = ROOT / ".local/datasets/ecom-after-sale-synthetic"
API = f"https://www.modelscope.cn/api/v1/datasets/{DATASET}"
FILES = ("README.md", "LICENSE.txt", "dataset_infos.json", "data.jsonl")


def get_bytes(url: str) -> bytes:
    """读取官方站点文件，不执行下载内容。"""
    with urllib.request.urlopen(url, timeout=45) as response:
        return response.read()


def download(directory: Path) -> None:
    """按仓库当前提交下载四份文件，并核对官方 SHA256。"""
    directory.mkdir(parents=True, exist_ok=True)
    tree = json.loads(get_bytes(API + "/repo/tree?Revision=master&Root="))
    if tree.get("Code") != 200:
        raise ValueError("无法读取数据集文件清单")
    latest = tree["Data"].get("LatestCommitter") or {}
    revision = latest.get("Id") or latest.get("ShortId") or "master"
    metadata = json.loads(get_bytes(API))
    entries = {entry["Path"]: entry for entry in tree["Data"]["Files"]}
    manifest = {"dataset": DATASET, "revision": revision, "source": f"https://www.modelscope.cn/datasets/{DATASET}",
                "downloaded_at": datetime.now(timezone.utc).isoformat(), "files": []}
    for name in FILES:
        entry = entries[name]
        path = directory / name
        url = f"https://www.modelscope.cn/datasets/{DATASET}/resolve/master/{name}"
        content = path.read_bytes() if path.exists() else get_bytes(url)
        digest = hashlib.sha256(content).hexdigest()
        if digest != entry["Sha256"] or len(content) != entry["Size"]:
            raise ValueError(f"{name} 与官方文件校验不符")
        if not path.exists():
            with path.open("xb") as output:
                output.write(content)
        manifest["files"].append({"name": name, "bytes": len(content), "sha256": digest, "url": url})
    for name, content in (("source.json", metadata), ("manifest.json", manifest)):
        target = directory / name
        if not target.exists():
            with target.open("x", encoding="utf-8") as output:
                json.dump(content, output, ensure_ascii=False, indent=2)
    print(json.dumps({"revision": revision, "files": manifest["files"]}, ensure_ascii=False))


def normalize(text: str) -> str:
    """将全角标点和空白统一，便于识别重复问法。"""
    text = unicodedata.normalize("NFKC", text).casefold()
    return "".join(char for char in text if char.isalnum())


def inspect(directory: Path) -> dict:
    """统计结构、重复和需人工复核的回复模式，保存原行号。"""
    rows = []
    invalid = []
    for number, line in enumerate((directory / "data.jsonl").read_text("utf-8-sig").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            if not isinstance(row, dict) or any(not isinstance(row.get(key), str) or not row[key].strip()
                                                 for key in ("scene", "user", "bot", "tone")):
                raise ValueError("缺少非空文本字段")
            rows.append({"line": number, **row})
        except (ValueError, TypeError) as error:
            invalid.append({"line": number, "error": str(error)})
    groups = {}
    flagged = []
    patterns = {
        "unverified_action_or_promise": r"(?:立即|直接|马上|免费|无条件|保证|一定|承诺).{0,12}(?:退|补发|赔|换)"
                                       r"|(?:为您|给您|帮您|为你|帮你).{0,6}(?:办理退款|退款|补发|赔偿|换货)",
        "fixed_time": r"\d+\s*(?:个)?(?:小时|天|工作日)|七天|三天|一周",
        "rating_or_compensation": r"好评|差评|删评|修改评价|撤销投诉|优惠券|赔偿|补偿",
    }
    for row in rows:
        groups.setdefault(normalize(row["user"]), []).append(row["line"])
        reasons = [name for name, pattern in patterns.items() if re.search(pattern, row["bot"])]
        if reasons:
            flagged.append({"line": row["line"], "reasons": reasons, "user": row["user"], "bot": row["bot"]})
    return {"dataset": DATASET, "valid_rows": len(rows), "invalid_rows": invalid,
            "scenes": dict(Counter(row["scene"] for row in rows)), "tones": dict(Counter(row["tone"] for row in rows)),
            "unique_user_messages": len(groups), "duplicate_user_groups": [value for value in groups.values() if len(value) > 1],
            "flag_counts": dict(Counter(reason for row in flagged for reason in row["reasons"])),
            "flagged_rows": flagged}


def main() -> None:
    """下载和审计分开执行，审计输出文件使用新名称。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--data-dir", type=Path, default=DIRECTORY)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    if args.download:
        download(args.data_dir)
    report = inspect(args.data_dir)
    if args.out:
        with args.out.open("x", encoding="utf-8") as output:
            json.dump(report, output, ensure_ascii=False, indent=2)
    print(json.dumps({key: value for key, value in report.items() if key != "flagged_rows"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
