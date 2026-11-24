"""检查官方资料导入时的表格结构及法规连续性。"""

import unittest

from scripts.native_terms_sources import PublicationText, jd_clauses, law_clauses, law_heading
from scripts.evaluate_native_terms import matches_answer_details


class NativeTermsSourceTests(unittest.TestCase):
    """覆盖影响适用范围和引用准确性的解析边界。"""

    def test_table_columns_are_preserved_in_the_official_section(self):
        """会员运费表保留每列关系，导航和脚本保持在正文之外。"""
        parser = PublicationText(root_class="contxt")
        parser.feed(
            '<div class="menu">导航</div><div class="contxt"><p>适用范围：</p>'
            '<p>仅示例</p><p>①退换货</p><table><tr><td>原因</td><td>非会员</td><td>会员</td></tr>'
            '<tr><td>误买</td><td>客户承担</td><td>平台承担</td></tr></table>'
            '<script>无关内容</script></div>'
        )
        sections = (("scope", "适用范围", "适用范围"), ("fee", "运费", "①退换货"))
        scope, fee = jd_clauses(parser.lines(), "https://example.test", sections)
        self.assertEqual(scope["text"], "适用范围\n适用范围：\n仅示例")
        self.assertIn("| 误买 | 客户承担 | 平台承担", fee["text"])
        self.assertNotIn("无关内容", fee["text"])

    def test_law_sections_fail_if_article_numbers_shift(self):
        """法规漏条或编号顺序错乱时，拒绝生成可能误引的索引。"""
        heading = "根据2020年10月23日国家市场监督管理总局令第31号修订"
        lines = [heading, *(f"{law_heading(i)} 测试文本" for i in range(1, 39))]
        self.assertEqual(law_clauses(lines, "https://example.test")[9]["id"], "SAMR-10")
        lines[10], lines[11] = lines[11], lines[10]
        with self.assertRaisesRegex(ValueError, "连续编号"):
            law_clauses(lines, "https://example.test")

    def test_answer_details_accept_formatting_and_require_each_statement(self):
        """括号和空白不影响判分，缺少第二个诉求仍判为未通过。"""
        checks = [["消费者承担", "买家仅承担"], ["不能到付", "除非双方同意"]]
        self.assertTrue(matches_answer_details("运费由你（消 费者）承担。且不能到付。", checks))
        self.assertFalse(matches_answer_details("买家仅承担退货运费。可以直接到付。", checks))
