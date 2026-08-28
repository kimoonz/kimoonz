"""HMM 국면 필터 검증."""

import numpy as np
import pytest

from bayesfutures.regime import GaussianHMM


def _two_state_series(n=3000, stay=0.97, seed=1):
    rng = np.random.default_rng(seed)
    xs, states, s = [], [], 0
    for _ in range(n):
        if rng.random() > stay:
            s = 1 - s
        states.append(s)
        xs.append(rng.normal(-1.0 if s == 0 else 1.0, 0.8))
    return np.array(xs), np.array(states)


def test_recovers_state_means():
    x, _ = _two_state_series()
    h = GaussianHMM(n_states=2, seed=0).fit(x)
    assert h.means[0] == pytest.approx(-1.0, abs=0.2)
    assert h.means[1] == pytest.approx(1.0, abs=0.2)


def test_states_sorted_ascending():
    x, _ = _two_state_series()
    h = GaussianHMM(n_states=3, seed=0).fit(x)
    assert list(h.means) == sorted(h.means)


def test_transition_matrix_is_persistent():
    x, _ = _two_state_series(stay=0.97)
    h = GaussianHMM(n_states=2, seed=0).fit(x)
    assert np.diag(h.trans).min() > 0.9
    assert np.allclose(h.trans.sum(axis=1), 1.0)


def test_filter_identifies_true_state():
    x, states = _two_state_series()
    h = GaussianHMM(n_states=2, seed=0).fit(x)
    gamma = h.filter(x)
    assert np.allclose(gamma.sum(axis=1), 1.0)
    assert (gamma.argmax(axis=1) == states).mean() > 0.85


def test_filter_is_causal():
    """필터링은 미래를 안 본다 — 뒤쪽 데이터를 바꿔도 앞쪽 결과가 그대로여야 한다."""
    x, _ = _two_state_series()
    h = GaussianHMM(n_states=2, seed=0).fit(x)
    g_full = h.filter(x)
    x_mod = x.copy()
    x_mod[2000:] = 99.0
    g_mod = h.filter(x_mod)
    assert np.allclose(g_full[:2000], g_mod[:2000])


def test_handles_nan_input():
    x, _ = _two_state_series(n=1000)
    h = GaussianHMM(n_states=2, seed=0).fit(x)
    x_nan = x.copy()
    x_nan[:60] = np.nan
    gamma = h.filter(x_nan)
    assert np.isfinite(gamma).all()


def test_rejects_tiny_sample():
    with pytest.raises(ValueError):
        GaussianHMM(n_states=3).fit(np.random.default_rng(0).normal(size=20))
