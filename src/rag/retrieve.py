"""Question -> the most relevant passages (meaning + exact words, fused)."""

import math
import re
from collections import Counter

import numpy as np

_TOKEN = re.compile(r"[a-z0-9]+(?:[.\-/][a-z0-9]+)*")
STOP = set("""
a an and are as at be by for from has have how i in is it of on or that the
this to was what when where which who why will with do does did can you your
about into than then there these those their they we our not no
""".split())


def tokens(text):
    return [t for t in _TOKEN.findall(text.lower()) if t not in STOP]


class BM25:
    def __init__(self, texts, k1=1.5, b=0.75):
        self.k1, self.b = k1, b
        self.docs = [Counter(tokens(t)) for t in texts]
        self.lengths = [sum(d.values()) for d in self.docs]
        self.avg = (sum(self.lengths) / len(self.lengths)) if self.docs else 0
        df = Counter()
        for doc in self.docs:
            df.update(doc.keys())
        n = len(self.docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def scores(self, query):
        terms = [t for t in set(tokens(query)) if t in self.idf]
        out = np.zeros(len(self.docs), dtype=np.float32)
        if not terms or not self.docs:
            return out
        for i, doc in enumerate(self.docs):
            score = 0.0
            for t in terms:
                f = doc.get(t)
                if f:
                    norm = f + self.k1 * (1 - self.b + self.b * self.lengths[i] / (self.avg or 1))
                    score += self.idf[t] * f * (self.k1 + 1) / norm
            out[i] = score
        return out


class Retriever:
    def __init__(self, store, embedder):
        self.store = store
        self.embedder = embedder
        self.bm25 = BM25([c["text"] for c in store.chunks])

    def search(self, question, top_k=6, min_score=0.30, files=None, candidates=20):
        """Return ``(hits, best_vector_score)``. Each hit is the chunk record
        plus ``score`` (vector) and ``rank``. ``hits`` is empty when nothing
        is relevant enough."""

        chunks = self.store.chunks
        if not chunks:
            return [], 0.0
        allowed = np.ones(len(chunks), dtype=bool)
        if files:
            wanted = set(files)
            allowed = np.array([c["file_key"] in wanted for c in chunks])

        vector_scores = self.store.vectors @ self.embedder.embed_query(question)
        vector_scores = np.where(allowed, vector_scores, -1.0)
        keyword_scores = np.where(allowed, self.bm25.scores(question), 0.0)

        by_vector = [i for i in np.argsort(-vector_scores)[:candidates] if allowed[i]]
        by_keyword = [i for i in np.argsort(-keyword_scores)[:candidates]
                      if keyword_scores[i] > 0]
        best = float(vector_scores[by_vector[0]]) if by_vector else 0.0
        if best < min_score and not by_keyword:
            return [], best

        fused = Counter()
        for ranking in (by_vector, by_keyword):
            for rank, index in enumerate(ranking):
                fused[int(index)] += 1.0 / (60 + rank + 1)
        hits = []
        for rank, (index, _) in enumerate(fused.most_common(top_k), start=1):
            hit = dict(chunks[index])
            hit["score"] = round(float(vector_scores[index]), 4)
            hit["rank"] = rank
            hits.append(hit)
        return hits, best
