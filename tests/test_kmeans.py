import numpy as np
import pytest

from color_switcher.backend.kmeans import kmeans


def test_separates_well_separated_blobs():
    rng = np.random.default_rng(0)
    blob_a = rng.normal(loc=[0, 0, 0], scale=0.5, size=(50, 3))
    blob_b = rng.normal(loc=[50, 50, 50], scale=0.5, size=(50, 3))
    points = np.vstack([blob_a, blob_b])

    centers, labels = kmeans(points, k=2, seed=1)

    # every point in blob_a (first 50) should share one label, blob_b another
    assert len(set(labels[:50])) == 1
    assert len(set(labels[50:])) == 1
    assert labels[0] != labels[50]

    center_dists = np.linalg.norm(centers, axis=1)
    assert center_dists.min() < 5  # one center near the origin blob
    assert center_dists.max() > 40  # the other near the (50,50,50) blob


def test_three_blobs_recovered():
    rng = np.random.default_rng(2)
    blobs = [rng.normal(loc=c, scale=0.3, size=(30, 3)) for c in ([0, 0, 0], [30, 0, 0], [0, 30, 0])]
    points = np.vstack(blobs)

    centers, labels = kmeans(points, k=3, seed=3)

    # each original blob's 30 points should all land in the same cluster
    assert len(set(labels[0:30])) == 1
    assert len(set(labels[30:60])) == 1
    assert len(set(labels[60:90])) == 1


def test_deterministic_with_fixed_seed():
    rng = np.random.default_rng(5)
    points = rng.normal(size=(60, 3))
    centers1, labels1 = kmeans(points, k=4, seed=7)
    centers2, labels2 = kmeans(points, k=4, seed=7)
    assert np.array_equal(centers1, centers2)
    assert np.array_equal(labels1, labels2)


def test_k_greater_than_points_raises():
    points = np.zeros((3, 3))
    with pytest.raises(ValueError):
        kmeans(points, k=5)


def test_single_cluster_returns_mean():
    points = np.array([[0.0, 0.0, 0.0], [10.0, 0.0, 0.0], [5.0, 0.0, 0.0]])
    centers, labels = kmeans(points, k=1, seed=1)
    assert np.allclose(centers[0], [5.0, 0.0, 0.0])
    assert set(labels) == {0}
