"""短条款全文与窗口相同时复用编码，长条款仍补编码独立窗口。"""

from types import SimpleNamespace
import unittest

from app.policies.evidence import PolicySource
from app.policies.semantic_terms import SemanticTerms


class RecordedEncoder:
    def __init__(self):
        self.calls=[]
        self.np=SimpleNamespace(stack=lambda rows:list(rows))
    def encode(self,texts):
        self.calls.append(list(texts))
        return [[len(text),sum(map(ord,text))] for text in texts]


class SemanticEncodingTests(unittest.TestCase):
    def test_identical_short_window_does_not_trigger_second_model_batch(self):
        encoder=RecordedEncoder()
        terms=SemanticTerms(encoder,[PolicySource('a','补发','店铺','配件可单独补寄。')])
        self.assertEqual(len(encoder.calls),1)
        self.assertEqual(terms.window_vectors[0],terms.article_vectors[0])

    def test_long_clause_keeps_all_window_ranges_and_vectors(self):
        encoder=RecordedEncoder()
        text='条款正文：'+('仍需核对商品状态。'*10)+'末尾例外'
        terms=SemanticTerms(encoder,[PolicySource('a','条款','店铺',text)],window_size=20,window_overlap=5)
        self.assertEqual(len(encoder.calls),2)
        self.assertGreater(len(terms.window_vectors),1)
        last=encoder.calls[-1][-1]
        self.assertTrue(last.endswith('末尾例外'))
        self.assertEqual(terms.window_vectors[-1],[len(last),sum(map(ord,last))])
