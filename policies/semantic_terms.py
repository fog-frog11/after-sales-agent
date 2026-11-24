"""使用本机 E5 ONNX 做售后条款的语义召回和证据窗口排序。"""

from pathlib import Path

from app.policies.retrieval_ranking import article_text, evidence_windows
from app.policies.search import PolicySource


class E5Encoder:
    """加载本机 INT8 模型，按 E5 的 query/passage 约定生成单位向量。"""

    def __init__(self, model_dir: Path):
        """加载固定的模型和分词器，并限制办公电脑的 CPU 线程数。"""
        import numpy as np
        import onnxruntime as ort
        from tokenizers import Tokenizer

        self.np = np
        self.tokenizer = Tokenizer.from_file(str(model_dir / "tokenizer.json"))
        self.tokenizer.enable_truncation(max_length=512)
        self.tokenizer.enable_padding(pad_id=self.tokenizer.token_to_id("<pad>"), pad_token="<pad>")
        options = ort.SessionOptions()
        options.intra_op_num_threads = 4
        options.inter_op_num_threads = 1
        self.session = ort.InferenceSession(
            str(model_dir / "model_int8.onnx"), sess_options=options, providers=["CPUExecutionProvider"],
        )
        self.input_names = {field.name for field in self.session.get_inputs()}

    def encode(self, texts: list[str], *, query: bool = False):
        """分批编码，屏蔽填充 token 后做均值池化和 L2 归一化。"""
        np = self.np
        prefix = "query: " if query else "passage: "
        batches = []
        for start in range(0, len(texts), 8):
            tokens = self.tokenizer.encode_batch([prefix + text for text in texts[start:start + 8]])
            mask = np.array([item.attention_mask for item in tokens], dtype=np.int64)
            inputs = {
                "input_ids": np.array([item.ids for item in tokens], dtype=np.int64),
                "attention_mask": mask,
                "token_type_ids": np.array([item.type_ids for item in tokens], dtype=np.int64),
            }
            hidden, = self.session.run(["last_hidden_state"], {
                key: value for key, value in inputs.items() if key in self.input_names
            })
            pooled = (hidden * mask[:, :, None].astype(hidden.dtype)).sum(axis=1) / mask.sum(axis=1)[:, None]
            normalized = pooled / np.linalg.norm(pooled, axis=1, keepdims=True)
            batches.append(normalized.astype(np.float32))
        return np.concatenate(batches)


class SemanticTerms:
    """为同一个已确定范围预编码条款及其证据窗口。"""

    def __init__(self, encoder: E5Encoder, articles: list[PolicySource], *, window_size: int = 400,
                 window_overlap: int = 100, include_windows: bool = True):
        """建立小范围向量矩阵，并记录每个条款对应的窗口下标。"""
        self.encoder = encoder
        self.article_ids = [article.article_id for article in articles]
        article_texts=[article_text(article) for article in articles]
        self.article_vectors = encoder.encode(article_texts)
        if not include_windows:
            return
        self.window_ranges: dict[str, slice] = {}
        windows = []
        for article in articles:
            start = len(windows)
            windows.extend(evidence_windows(article, size=window_size, overlap=window_overlap))
            self.window_ranges[article.article_id] = slice(start, len(windows))
        by_text=dict(zip(article_texts,self.article_vectors,strict=True))
        missing=list(dict.fromkeys(text for text in windows if text not in by_text))
        if missing:
            by_text.update(zip(missing,encoder.encode(missing),strict=True))
        self.window_vectors = encoder.np.stack([by_text[text] for text in windows])

    def query_vector(self, question: str):
        """生成可被整条语义召回和证据排序复用的同一个查询向量。"""
        return self.encoder.encode([question], query=True)[0]

    def dense_ranking(self, query_vector, limit: int) -> list[str]:
        """用余弦相似度为范围内的完整条款排序。"""
        scores = self.article_vectors @ query_vector
        ranked = self.encoder.np.argsort(-scores, kind="stable")
        return [self.article_ids[int(index)] for index in ranked[:limit]]

    def window_ranking(self, query_vector, candidates: list[str]) -> list[str]:
        """给候选条款按最相关证据窗口打分，同分时保留原顺序。"""
        scores = {}
        for article_id in candidates:
            vectors = self.window_vectors[self.window_ranges[article_id]]
            scores[article_id] = float((vectors @ query_vector).max())
        return sorted(candidates, key=scores.__getitem__, reverse=True)
