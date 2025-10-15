# backend/app/services/semantic.py
"""
SemanticEngine: produces semantic embeddings (best-effort) and does keyword clustering.
- If sentence-transformers is installed, uses 'all-MiniLM-L6-v2' for compact embeddings.
- If not installed, falls back to TF-IDF vectors (fast, lightweight).
- Provides:
  - get_embeddings(texts): returns numpy array (n_texts, dim)
  - similarity_scores(query_vec, candidate_vecs): cosine similarities
  - extract_keywords(text): light-weight keyword extraction (no heavy deps)
  - cluster_keywords(texts): cluster keywords to identify themes
  - expand_keywords(keywords): small synonym/fuzzy expansion map
"""

from typing import List, Tuple, Dict, Optional
import logging
import math
import re
from collections import Counter, defaultdict

import numpy as np

# sklearn utilities (already in project)
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from sklearn.cluster import KMeans

# fuzzy matching for small synonym expansion
from difflib import get_close_matches

logger = logging.getLogger(__name__)


# Always use TF-IDF/keyword logic (no heavy dependencies)
_TFIDF_MAX_FEATURES = 2000

# Basic synonym map for common movie/show keywords (extend as needed)
_SIMPLE_SYNONYMS = {
    "sci-fi": ["science fiction", "scifi", "sf"],
    "romcom": ["romantic comedy", "rom-com"],
    "crime": ["gangster", "detective", "police"],
    "thriller": ["suspense"],
    "romance": ["love", "relationship"],
    "family": ["kids", "children"],
    "doc": ["documentary", "nonfiction"],
    "biopic": ["biography", "based on a true story"]
}

def _clean_text(text: str) -> str:
    if not text:
        return ""
    t = text.lower()
    t = re.sub(r"\s+", " ", t)
    t = re.sub(r"[^a-z0-9\s]", " ", t)
    return t.strip()

class SemanticEngine:
    def __init__(self):
        self._tfidf = None

    # ---------------------
    # Embedding API
    # ---------------------
    def get_embeddings(self, texts: List[str]) -> np.ndarray:
        """
        Return dense vectors for each text using TF-IDF (normalized).
        """
        cleaned = [ _clean_text(t) for t in texts ]
        if self._tfidf is None:
            self._tfidf = TfidfVectorizer(max_features=_TFIDF_MAX_FEATURES, stop_words='english')
            self._tfidf.fit(cleaned)
        mat = self._tfidf.transform(cleaned).astype(np.float32).toarray()
        # normalize
        norms = np.linalg.norm(mat, axis=1, keepdims=True)
        norms[norms==0] = 1.0
        return mat / norms

    def similarity_scores(self, query_text: str, candidate_texts: List[str]) -> List[float]:
        """
        Compute cosine similarity scores between query_text and each candidate_text.
        """
        if not candidate_texts:
            return []
        queries = [query_text] + candidate_texts
        emb = self.get_embeddings(queries)
        q = emb[0:1]
        cand = emb[1:]
        sims = cosine_similarity(q, cand).flatten().tolist()
        return sims

    # ---------------------
    # Keyword extraction & clustering
    # ---------------------
    def extract_keywords(self, text: str, top_n: int = 20) -> List[str]:
        """
        Lightweight keyword extraction:
        - Clean text, split, count n-grams (1-2), exclude small common words.
        - Return top_n keywords (phrases are kept).
        """
        if not text:
            return []
        txt = _clean_text(text)
        tokens = [t for t in txt.split() if len(t) > 2]
        # build unigrams and bigrams frequencies
        unigrams = tokens
        bigrams = [" ".join(tokens[i:i+2]) for i in range(len(tokens)-1)]
        combined = unigrams + bigrams
        c = Counter(combined)
        # remove overly generic words
        stopset = {"with","from","have","this","that","there","which","would","about","their","could"}
        candidates = [(k,v) for k,v in c.items() if k not in stopset]
        candidates.sort(key=lambda x: x[1], reverse=True)
        return [k for k,_ in candidates[:top_n]]

    def expand_keywords(self, keywords: List[str], max_extra: int = 10) -> List[str]:
        """
        Expand keywords using a small synonym map and fuzzy matching.
        This is intentionally conservative (no huge external lists).
        """
        expanded = set(keywords)
        for k in keywords:
            # direct synonyms map
            for syn_k, syn_list in _SIMPLE_SYNONYMS.items():
                if k == syn_k or k in syn_list:
                    expanded.update([syn_k] + syn_list)
            # fuzzy matches against synonyms (close matches)
            for syn_k in _SIMPLE_SYNONYMS.keys():
                matches = get_close_matches(k, [syn_k], n=1, cutoff=0.8)
                if matches:
                    expanded.update(_SIMPLE_SYNONYMS.get(syn_k, []))
        # limit
        extras = list(expanded - set(keywords))
        if len(extras) > max_extra:
            extras = extras[:max_extra]
        return list(keywords) + extras

    def cluster_keywords(self, texts: List[str], n_clusters: int = 5) -> Dict[int, List[str]]:
        """
        Cluster keywords extracted from a list of texts. Returns mapping cluster_id -> keywords.
        - This helps group user interests into themes.
        """
        # gather keywords from each text
        all_keywords = []
        mapping_text_to_kw = []
        for t in texts:
            kws = self.extract_keywords(t, top_n=12)
            mapping_text_to_kw.append(kws)
            all_keywords.extend(kws)

        if not all_keywords:
            return {}

        unique_kw = list(dict.fromkeys(all_keywords))  # preserve order
        # vectorize unique keywords using TF-IDF (small vocabulary)
        tf = TfidfVectorizer(max_features=1000, stop_words='english').fit(unique_kw)
        kw_vecs = tf.transform(unique_kw).toarray()
        k = min(n_clusters, len(unique_kw))
        if k <= 1:
            return {0: unique_kw}

        km = KMeans(n_clusters=k, random_state=42, n_init=8)
        labels = km.fit_predict(kw_vecs)
        clusters = defaultdict(list)
        for idx, lab in enumerate(labels):
            clusters[lab].append(unique_kw[idx])
        return dict(clusters)

    # ---------------------
    # Helper: semantic matching with clusters
    # ---------------------
    def score_by_clusters(self, user_texts: List[str], candidate_texts: List[str], cluster_weight: float = 0.5) -> List[float]:
        """
        Score candidate_texts by combining semantic similarity and cluster keyword overlap.
        Returns list of scores (0..1).
        """
        # Profile text = concatenation of user_texts
        profile_text = " ".join(user_texts)
        sims = self.similarity_scores(profile_text, candidate_texts)  # semantic sims

        # cluster user keywords
        clusters = self.cluster_keywords(user_texts, n_clusters=5)
        # create cluster sets for quick overlap
        cluster_sets = [set(v) for v in clusters.values()] if clusters else []

        kw_scores = []
        for ctext in candidate_texts:
            ckw = set(self.extract_keywords(ctext, top_n=12))
            # compute best cluster overlap
            best = 0.0
            for cs in cluster_sets:
                if not cs: continue
                overlap = len(ckw & cs) / max(len(cs),1)
                best = max(best, overlap)
            kw_scores.append(best)

        # combine semantic sims and kw overlap (weights)
        final = []
        for s, k in zip(sims, kw_scores):
            # normalize s (could already be in 0..1)
            s_norm = float((s + 1) / 2) if isinstance(s, (float,)) else float(s)
            score = (1 - cluster_weight) * s_norm + cluster_weight * k
            final.append(min(1.0, max(0.0, score)))
        return final
