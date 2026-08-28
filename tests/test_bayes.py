"""확률 엔진 검증."""

import numpy as np
import pandas as pd
import pytest

from bayesfutures.bayes import (BayesModel, auc, brier_score, log_loss,
                                logit, reliability, sigmoid)


@pytest.fixture
def signal_data():
    rng = np.random.default_rng(0)
    n = 3000
    x1, x2, noise = rng.normal(size=n), rng.normal(size=n), rng.normal(size=n)
    p = sigmoid(0.9 * x1 + 0.5 * x2)
    y = (rng.random(n) < p).astype(float)
    return pd.DataFrame({"a": x1, "b": x2, "noise": noise}), pd.Series(y), p


def test_recovers_signal(signal_data):
    X, y, true_p = signal_data
    m = BayesModel(n_bins=6).fit(X, y)
    pred = m.predict_proba(X)
    assert auc(pred, y.to_numpy()) > 0.68
    assert np.corrcoef(true_p, pred)[0, 1] > 0.9


def test_is_calibrated(signal_data):
    """예측확률 구간별 실제 적중률이 예측값에 가까워야 한다."""
    X, y, _ = signal_data
    m = BayesModel(n_bins=6).fit(X, y)
    rel = reliability(m.predict_proba(X), y.to_numpy(), bins=5)
    err = (rel["예측평균"] - rel["실제적중"]).abs()
    assert err.max() < 0.08


def test_noise_feature_is_shrunk(signal_data):
    """정보가 없는 피처는 로그오즈를 거의 못 움직여야 한다."""
    X, y, _ = signal_data
    m = BayesModel(n_bins=6).fit(X, y)
    weight = lambda name: np.abs(m.tables[name].log_lr * m.tables[name].shrink).max()
    assert weight("noise") < weight("a") / 3


def test_pure_noise_gives_flat_probabilities():
    """아무 정보도 없으면 보정계수가 확률을 기준선으로 눌러야 한다."""
    rng = np.random.default_rng(3)
    n = 2000
    X = pd.DataFrame({f"f{i}": rng.normal(size=n) for i in range(6)})
    y = pd.Series((rng.random(n) < 0.5).astype(float))
    m = BayesModel(n_bins=5).fit(X, y)
    pred = m.predict_proba(X)
    assert m.calib_a < 0.35
    assert pred.std() < 0.06
    assert abs(pred.mean() - 0.5) < 0.05


def test_missing_values_contribute_nothing():
    """결측 피처는 '증거 없음'으로 처리되어야 한다."""
    rng = np.random.default_rng(5)
    n = 1200
    x = rng.normal(size=n)
    y = pd.Series((rng.random(n) < sigmoid(x)).astype(float))
    X = pd.DataFrame({"x": x})
    m = BayesModel(n_bins=5).fit(X, y)
    nan_row = pd.DataFrame({"x": [np.nan]})
    assert m.predict_proba(nan_row)[0] == pytest.approx(m.base_rate, abs=0.02)


def test_soft_evidence_is_used():
    """국면 같은 소프트 증거(확률 벡터)도 확률을 움직여야 한다."""
    rng = np.random.default_rng(11)
    n = 2000
    state = rng.integers(0, 2, n)
    soft = np.zeros((n, 2))
    soft[np.arange(n), state] = 0.9
    soft[np.arange(n), 1 - state] = 0.1
    y = pd.Series((rng.random(n) < np.where(state == 1, 0.7, 0.3)).astype(float))
    X = pd.DataFrame({"dummy": rng.normal(size=n)})
    m = BayesModel(n_bins=4).fit(X, y, soft={"regime": soft})
    up = m.predict_proba(X.iloc[[0]], soft={"regime": np.array([[0.1, 0.9]])})[0]
    down = m.predict_proba(X.iloc[[0]], soft={"regime": np.array([[0.9, 0.1]])})[0]
    assert up > down + 0.15


def test_embargo_lowers_optimism():
    """라벨이 겹치는 데이터에서 embargo를 주면 보정계수가 더 보수적이어야 한다."""
    rng = np.random.default_rng(7)
    n = 2000
    x = rng.normal(size=n)
    # 라벨을 앞뒤로 겹치게 만들어 폴드 경계 누수를 유도
    y_raw = pd.Series(x).rolling(20).mean().shift(-10)
    y = pd.Series((y_raw > 0).astype(float)).fillna(0.0)
    X = pd.DataFrame({"x": x})
    a_leaky = BayesModel(n_bins=5).fit(X, y, embargo=0).calib_a
    a_clean = BayesModel(n_bins=5).fit(X, y, embargo=40).calib_a
    assert a_clean <= a_leaky + 1e-9


def test_explain_contributions_sum_toward_score():
    rng = np.random.default_rng(2)
    n = 1500
    X = pd.DataFrame({"a": rng.normal(size=n), "b": rng.normal(size=n)})
    y = pd.Series((rng.random(n) < sigmoid(X["a"])).astype(float))
    m = BayesModel(n_bins=5).fit(X, y)
    row = X.iloc[10]
    contribs = m.explain(row)
    total = sum(c["contribution"] for c in contribs)
    expected = (m._raw_score(X.iloc[[10]])[0] - m.prior_logodds) * m.calib_a
    assert total == pytest.approx(expected, abs=1e-6)


def test_metrics_edge_cases():
    y = np.array([0.0, 1.0, 0.0, 1.0])
    assert brier_score(np.array([0.0, 1.0, 0.0, 1.0]), y) == 0.0
    assert auc(np.array([0.1, 0.9, 0.2, 0.8]), y) == 1.0
    assert np.isnan(auc(np.array([0.5, 0.5]), np.array([1.0, 1.0])))
    assert log_loss(np.array([0.5] * 4), y) == pytest.approx(np.log(2))


def test_logit_sigmoid_roundtrip():
    for p in (0.01, 0.3, 0.5, 0.87, 0.999):
        assert sigmoid(logit(p)) == pytest.approx(p, abs=1e-6)
