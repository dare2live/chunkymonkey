import math

from services.ml_lifecycle.drift import compute_psi, severity_for_psi


def test_compute_psi_filters_non_finite_values():
    train = [1, 2, 3, 4, 5, math.nan, math.inf, None]
    recent = [1, 2, 3, 4, 5, None, -math.inf]

    psi, n_train, n_recent = compute_psi(train, recent, n_bins=5)

    assert n_train == 5
    assert n_recent == 5
    assert psi < 1e-8


def test_compute_psi_detects_shifted_distribution():
    train = list(range(1, 101))
    recent = list(range(51, 151))

    psi, n_train, n_recent = compute_psi(train, recent, n_bins=10)

    assert n_train == 100
    assert n_recent == 100
    assert severity_for_psi(psi) == "critical"
