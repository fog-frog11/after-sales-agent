"""从本机保存的中文官方发布页提取带稳定编号的评测条款。"""

import re
from html.parser import HTMLParser
from pathlib import Path

from app.policies.public_benchmark import BENCHMARK_SHOP
from app.policies.jd_reference import JD_MARKET_URL as JD_MARKET, JD_SELF_URL as JD_SELF, SAMR_URL as LAW_PDF


JD_SELF_SECTIONS = (
    ("JDS-SCOPE", "京东自营售后运费适用范围", "适用范围："),
    ("JDS-1A", "京东自营退换货运费承担", "①退换货"),
    ("JDS-1B", "京东自营维修运费承担", "②"),
    ("JDS-2.1", "京东自营上门取件及扣费", "（1）上门取件"),
    ("JDS-2.2", "京东自营快递寄回及运费返还", "（2）快递至京东"),
)
JD_MARKET_SECTIONS = (
    ("JDM-SCOPE", "京东第三方纠纷规则适用范围", "适用范围："),
    ("JDM-1", "京东第三方纠纷处理总则", "一、总则"),
    ("JDM-2.1", "京东第三方真实交易", "1.买卖双方"),
    ("JDM-2.2A", "京东第三方签收与收货信息", "【签收规范】"),
    ("JDM-2.2B", "京东第三方拒收与签收纠纷", "【签收问题纠纷处理】"),
    ("JDM-2.3A", "京东第三方退换货规范", "【退换货规范】"),
    ("JDM-2.3B", "京东第三方退换货丢失破损", "【退换货问题纠纷处理】"),
    ("JDM-2.4A", "京东第三方运费及包邮规则", "【运费规范】"),
    ("JDM-2.4B", "京东第三方运费纠纷", "【运费问题纠纷处理】"),
    ("JDM-3", "京东第三方纠纷举证责任", "三、举证责任"),
    ("JDM-4.1", "京东第三方纠纷申请与受理", "1.申请和受理"),
    ("JDM-4.2", "京东第三方纠纷处理程序", "2.纠纷的处理"),
)
HEADING_ONLY = re.compile(r"^[一二三四]、|^[12]、(?:京东自营商品|商品返回京东)|^[234]\.(?:签收|退换货|运费).*原则")
LAW_ARTICLE = re.compile(r"^(第[一二三四五六七八九十]+条)\s*(.*)")
LAW_CHAPTER = re.compile(r"^第[一二三四五六七八九十]+章")
PAGE_MARKER = re.compile(r"^—\s*\d+\s*—$")
CLAUSE_NUMERALS = "一二三四五六七八九"
LAW_TOPICS = {
    6: "不适用七日无理由退货的商品",
    7: "付款前确认的退货例外",
    8: "商品完好与拆封查验",
    9: "不完好商品的具体标准",
    10: "退货通知与期限起算",
    11: "退货地址与凭证",
    12: "配件和赠品返还",
    13: "退回货款的期限",
    14: "原路退款与多种支付方式",
    15: "积分代金券与优惠券返还",
    16: "信用卡手续费",
    17: "部分退货与优惠重算",
    18: "退货运费承担",
    19: "退货方式和有偿上门取件",
    20: "排除无理由退货的展示确认",
    24: "平台协助处理退货争议",
    25: "退回商品再次出售的标示",
    35: "商品质量问题的退货依据",
}


class PublicationText(HTMLParser):
    """提取页面可见段落及表格，同时跳过脚本与样式。"""

    def __init__(self, *, root_class: str | None = None):
        """可限制在内容容器中读取，避免京东导航混入条款。"""
        super().__init__()
        self.root_class = root_class
        self.depth = 0
        self.ignored = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """在段落或表格边界插入分隔符。"""
        classes = (dict(attrs).get("class") or "").split()
        if tag == "div" and (self.depth or self.root_class in classes):
            self.depth += 1
        if tag in {"script", "style"}:
            self.ignored += 1
        if self.ignored or (self.root_class and not self.depth):
            return
        if tag in {"p", "tr", "br", "h1", "h2", "h3", "div", "li"}:
            self.parts.append("\n")
        if tag in {"td", "th"}:
            self.parts.append(" | ")

    def handle_endtag(self, tag: str) -> None:
        """退出网页内容容器与需要忽略的标记。"""
        if tag == "div" and self.depth:
            self.depth -= 1
        if tag in {"script", "style"} and self.ignored:
            self.ignored -= 1

    def handle_data(self, data: str) -> None:
        """收集正文，并保留表格单元格内原文字句。"""
        if not self.ignored and (self.root_class is None or self.depth):
            self.parts.append(data)

    def lines(self) -> list[str]:
        """将连续空白标准化，忽略纯格式空行。"""
        return [" ".join(line.split()) for line in "".join(self.parts).splitlines() if line.strip()]


def html_lines(path: Path, *, jd: bool = False) -> list[str]:
    """按发布页声明的常用编码读取正文。"""
    parser = PublicationText(root_class="contxt" if jd else None)
    parser.feed(path.read_text(encoding="gb18030" if jd else "utf-8-sig"))
    return parser.lines()


def clause(article_id: str, title: str, source: str, lines: list[str]) -> dict:
    """为 Milvus 保留来源及编号，并检查完整条款能放进索引。"""
    text = "\n".join([title, *lines])
    if not any(line.strip() for line in lines) or len(text.encode("utf-8")) > 4096:
        raise ValueError(f"条款缺少正文或过长：{article_id}")
    return {"id": article_id, "shop_id": BENCHMARK_SHOP, "title": title, "source": source, "text": text}


def jd_clauses(lines: list[str], source: str, sections: tuple[tuple[str, str, str], ...]) -> list[dict]:
    """按京东页面的小节锚点取原文，版式变动时停止导入。"""
    boundaries = []
    for article_id, title, anchor in sections:
        matches = [i for i, line in enumerate(lines) if line.startswith(anchor)]
        if len(matches) != 1 or (boundaries and matches[0] <= boundaries[-1]):
            raise ValueError(f"无法确认官方页面的条款边界：{article_id}")
        boundaries.append(matches[0])
    boundaries.append(len(lines))
    return [
        clause(article_id, title, source, [line for line in lines[start:end] if not HEADING_ONLY.match(line)])
        for (article_id, title, _), start, end in zip(sections, boundaries[:-1], boundaries[1:], strict=True)
    ]


def law_clauses(lines: list[str], source: str) -> list[dict]:
    """确认 2020 修订标记后，按法条起始编号切分官方正文。"""
    if not any("2020年10月23日" in line.replace(" ", "") and "第31号修订" in line.replace(" ", "") for line in lines):
        raise ValueError("法规正文缺少预期的 2020 修订标记。")
    articles: list[tuple[str, list[str]]] = []
    for line in lines:
        match = LAW_ARTICLE.match(line)
        if match:
            heading, text = match.groups()
            if articles and heading == articles[-1][0]:
                raise ValueError(f"法规正文中重复出现 {heading}。")
            articles.append((heading, [text] if text else []))
            continue
        if articles and not LAW_CHAPTER.match(line) and not PAGE_MARKER.fullmatch(line):
            articles[-1][1].append(line)
    if [heading for heading, _ in articles] != [law_heading(number) for number in range(1, 39)]:
        raise ValueError(f"预期 38 条法规及连续编号，实际解析到 {len(articles)} 条。")
    return [
        clause(f"SAMR-{number:02d}",
               heading if number not in LAW_TOPICS else f"{heading} {LAW_TOPICS[number]}", source,
               ["".join(body)])
        for number, (heading, body) in enumerate(articles, start=1)
    ]


def law_heading(number: int) -> str:
    """生成已发布法规中的原编号，供导入时验证条款顺序。"""
    tens, units = divmod(number, 10)
    prefix = CLAUSE_NUMERALS[tens - 1] if tens >= 2 else ""
    digits = prefix + ("十" if tens else "") + (CLAUSE_NUMERALS[units - 1] if units else "")
    return f"第{digits}条"


def load_articles(root: Path) -> tuple[list[dict], tuple[tuple[Path, str, str | None], ...]]:
    """读取三份官方快照，返回条款与已知的来源版本信息。"""
    from pypdf import PdfReader

    self_path = root / "jd-self-operated.html"
    market_path = root / "jd-marketplace.html"
    law_path = root / "samr-return-rules-2020.pdf"
    market_lines = html_lines(market_path, jd=True)
    if "生效时间：2023年9月19日" not in market_lines:
        raise ValueError("京东第三方快照的生效日期与评测版本不符。")
    pages = PdfReader(law_path).pages
    law_lines = [line.strip() for page in pages for line in page.extract_text().splitlines() if line.strip()]
    sources = (
        (self_path, JD_SELF, None),
        (market_path, JD_MARKET, "2023-09-19"),
        (law_path, LAW_PDF, "2020-10-23"),
    )
    articles = [
        *jd_clauses(html_lines(self_path, jd=True), JD_SELF, JD_SELF_SECTIONS),
        *jd_clauses(market_lines, JD_MARKET, JD_MARKET_SECTIONS),
        *law_clauses(law_lines, LAW_PDF),
    ]
    return articles, sources
