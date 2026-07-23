#!/usr/bin/env python3
"""
kmeans.py — Minimal weighted K-Means with k-means++ initialization, in
plain numpy (no scikit-learn dependency).

Written for palette_generator.py's use case (a few thousand to tens of
thousands of points in a 3D perceptual color space, K in the tens), where a
straightforward vectorized implementation is more than fast enough.
"""

import numpy as np


def _kmeans_pp_init(points, k, rng, weights):
    n = points.shape[0]
    centers = np.empty((k, points.shape[1]))

    first = rng.choice(n, p=weights / weights.sum())
    centers[0] = points[first]
    closest_dist_sq = ((points - centers[0]) ** 2).sum(axis=1)

    for i in range(1, k):
        probs = closest_dist_sq * weights
        total = probs.sum()
        idx = rng.choice(n) if total <= 0 else rng.choice(n, p=probs / total)
        centers[i] = points[idx]
        new_dist_sq = ((points - centers[i]) ** 2).sum(axis=1)
        closest_dist_sq = np.minimum(closest_dist_sq, new_dist_sq)

    return centers


def kmeans(points, k, weights=None, max_iter=50, tol=1e-4, seed=42):
    """
    Args:
        points: array(n, d)
        k: number of clusters (must be <= n)
        weights: optional array(n,) of sample weights (defaults to uniform —
            for randomly-sampled pixels, sampling frequency already reflects
            pixel frequency, so uniform weights are usually correct)
        max_iter, tol: convergence controls
        seed: RNG seed, for reproducible results

    Returns:
        (centers: array(k, d), labels: array(n,) of cluster index per point)
    """
    points = np.asarray(points, dtype=np.float64)
    n = points.shape[0]
    if k > n:
        raise ValueError(f"k ({k}) cannot exceed the number of points ({n})")

    rng = np.random.default_rng(seed)
    weights = np.ones(n) if weights is None else np.asarray(weights, dtype=np.float64)

    centers = _kmeans_pp_init(points, k, rng, weights)

    labels = None
    for _ in range(max_iter):
        dists = ((points[:, None, :] - centers[None, :, :]) ** 2).sum(axis=2)
        labels = dists.argmin(axis=1)

        new_centers = centers.copy()
        shifted = 0.0
        for i in range(k):
            mask = labels == i
            if not mask.any():
                continue  # keep an empty cluster's previous center rather than reseed
            w = weights[mask]
            new_centers[i] = (points[mask] * w[:, None]).sum(axis=0) / w.sum()

        shifted = float(np.linalg.norm(new_centers - centers))
        centers = new_centers
        if shifted < tol:
            break

    dists = ((points[:, None, :] - centers[None, :, :]) ** 2).sum(axis=2)
    labels = dists.argmin(axis=1)
    return centers, labels
