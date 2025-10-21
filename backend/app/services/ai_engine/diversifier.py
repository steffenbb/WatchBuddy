"""
diversifier.py (AI Engine)
- Maximal Marginal Relevance for diversity.
- KMeans clustering for genre/theme grouping.
"""
from typing import List, Dict, Any
import numpy as np
from sklearn.cluster import KMeans


def maximal_marginal_relevance(
    candidates: List[Dict[str, Any]],
    candidate_vectors: np.ndarray,
    top_k: int = 20,
    lambda_param: float = 0.8,
) -> List[Dict[str, Any]]:
    if not candidates:
        return []
    selected = [0]
    remaining = list(range(1, len(candidates)))
    while len(selected) < min(top_k, len(candidates)) and remaining:
        best_idx = None
        best_score = -1e9
        for i in remaining:
            relevance = candidates[i].get("final_score", 0.0)
            diversity = max(
                float(np.dot(candidate_vectors[i], candidate_vectors[j])) for j in selected
            )
            mmr = lambda_param * relevance - (1 - lambda_param) * diversity
            if mmr > best_score:
                best_score = mmr
                best_idx = i
        selected.append(best_idx)
        remaining.remove(best_idx)
    return [candidates[i] for i in selected]


def cluster_based_sampling(
    candidates: List[Dict[str, Any]],
    candidate_vectors: np.ndarray,
    top_k: int = 20,
    n_clusters: int = 5,
) -> List[Dict[str, Any]]:
    """
    Use KMeans clustering to group candidates by semantic similarity,
    then sample top items from each cluster for diversity.
    
    Args:
        candidates: List of candidate dicts with scores
        candidate_vectors: np.ndarray of embeddings (N x D)
        top_k: Total number of items to return
        n_clusters: Number of clusters to create (default 5)
    
    Returns:
        Diverse list of candidates sampled from different clusters
    """
    if not candidates or len(candidates) < n_clusters:
        # Not enough candidates for clustering, return as-is
        return sorted(candidates, key=lambda x: x.get("final_score", 0.0), reverse=True)[:top_k]
    
    # Ensure n_clusters doesn't exceed number of candidates
    n_clusters = min(n_clusters, len(candidates))
    
    # Perform KMeans clustering on embeddings
    kmeans = KMeans(n_clusters=n_clusters, random_state=42, n_init=10)
    cluster_labels = kmeans.fit_predict(candidate_vectors)
    
    # Group candidates by cluster
    clusters = [[] for _ in range(n_clusters)]
    for i, label in enumerate(cluster_labels):
        clusters[label].append(i)
    
    # Sample from each cluster proportionally
    selected_indices = []
    items_per_cluster = top_k // n_clusters
    remaining = top_k % n_clusters
    
    for cluster_idx, cluster_members in enumerate(clusters):
        if not cluster_members:
            continue
        
        # Sort cluster members by score
        cluster_members_sorted = sorted(
            cluster_members,
            key=lambda i: candidates[i].get("final_score", 0.0),
            reverse=True
        )
        
        # Take top items from this cluster
        take_count = items_per_cluster + (1 if cluster_idx < remaining else 0)
        selected_indices.extend(cluster_members_sorted[:take_count])
    
    # If we still need more items (some clusters were empty), fill from remaining high-scored items
    if len(selected_indices) < top_k:
        all_indices = set(range(len(candidates)))
        remaining_indices = list(all_indices - set(selected_indices))
        remaining_sorted = sorted(
            remaining_indices,
            key=lambda i: candidates[i].get("final_score", 0.0),
            reverse=True
        )
        selected_indices.extend(remaining_sorted[: top_k - len(selected_indices)])
    
    # Sort final selection by score
    selected_indices = sorted(
        selected_indices[:top_k],
        key=lambda i: candidates[i].get("final_score", 0.0),
        reverse=True
    )
    
    return [candidates[i] for i in selected_indices]
