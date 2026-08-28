"""가우시안 HMM 국면 필터 (numpy 구현, 외부 의존성 없음).

수익률 계열을 K개 은닉 상태로 모델링한다. 학습은 Baum-Welch(EM),
추론은 forward 필터링만 사용해 시점 t 의 국면 사후확률 gamma_t 를 얻는다.
필터링만 쓰기 때문에 미래 정보가 새지 않는다 (smoothing은 학습에만 사용).

보통 3개 상태는 [하락추세 / 횡보저변동 / 상승추세] 처럼 해석된다.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

_EPS = 1e-12


@dataclass
class GaussianHMM:
    n_states: int = 3
    n_iter: int = 60
    tol: float = 1e-5
    seed: int = 0

    # 학습 결과
    start_prob: np.ndarray | None = None
    trans: np.ndarray | None = None
    means: np.ndarray | None = None
    stds: np.ndarray | None = None
    fitted: bool = False

    # ------------------------------------------------------------------
    def _emission(self, x: np.ndarray) -> np.ndarray:
        """B[t, s] = N(x_t | mu_s, sigma_s)"""
        z = (x[:, None] - self.means[None, :]) / self.stds[None, :]
        return np.exp(-0.5 * z * z) / (self.stds[None, :] * np.sqrt(2.0 * np.pi)) + _EPS

    def _init_params(self, x: np.ndarray) -> None:
        rng = np.random.default_rng(self.seed)
        qs = np.linspace(0, 100, self.n_states + 2)[1:-1]
        self.means = np.percentile(x, qs).astype(float)
        self.means += rng.normal(0, 1e-6, self.n_states)
        spread = float(np.std(x)) or 1.0
        self.stds = np.full(self.n_states, spread, dtype=float)
        self.start_prob = np.full(self.n_states, 1.0 / self.n_states)
        # 국면은 잘 안 바뀐다는 사전지식: 대각 성분을 크게
        stay = 0.9
        off = (1.0 - stay) / max(self.n_states - 1, 1)
        self.trans = np.full((self.n_states, self.n_states), off)
        np.fill_diagonal(self.trans, stay)

    # ------------------------------------------------------------------
    def _forward(self, B: np.ndarray) -> tuple[np.ndarray, np.ndarray, float]:
        n = len(B)
        alpha = np.zeros((n, self.n_states))
        scale = np.zeros(n)
        a = self.start_prob * B[0]
        scale[0] = a.sum() + _EPS
        alpha[0] = a / scale[0]
        for t in range(1, n):
            a = (alpha[t - 1] @ self.trans) * B[t]
            scale[t] = a.sum() + _EPS
            alpha[t] = a / scale[t]
        return alpha, scale, float(np.log(scale).sum())

    def _backward(self, B: np.ndarray, scale: np.ndarray) -> np.ndarray:
        n = len(B)
        beta = np.zeros((n, self.n_states))
        beta[-1] = 1.0 / scale[-1]
        for t in range(n - 2, -1, -1):
            beta[t] = (self.trans @ (B[t + 1] * beta[t + 1])) / scale[t]
        return beta

    # ------------------------------------------------------------------
    def fit(self, x: np.ndarray) -> "GaussianHMM":
        x = np.asarray(x, dtype=float)
        x = x[np.isfinite(x)]
        if len(x) < 50 * self.n_states:
            raise ValueError("HMM 학습 표본 부족")
        self._init_params(x)

        prev_ll = -np.inf
        for _ in range(self.n_iter):
            B = self._emission(x)
            alpha, scale, ll = self._forward(B)
            beta = self._backward(B, scale)

            gamma = alpha * beta
            gamma /= gamma.sum(axis=1, keepdims=True) + _EPS

            # xi 합계 (전이 기대 횟수) — 벡터화
            fwd = alpha[:-1]                            # (T-1, K)
            bwd = B[1:] * beta[1:]                      # (T-1, K)
            num = fwd[:, :, None] * self.trans[None, :, :] * bwd[:, None, :]
            num /= num.sum(axis=(1, 2), keepdims=True) + _EPS
            xi_sum = num.sum(axis=0)

            self.start_prob = gamma[0] / (gamma[0].sum() + _EPS)
            self.trans = xi_sum / (xi_sum.sum(axis=1, keepdims=True) + _EPS)
            w = gamma.sum(axis=0) + _EPS
            self.means = (gamma * x[:, None]).sum(axis=0) / w
            var = (gamma * (x[:, None] - self.means[None, :]) ** 2).sum(axis=0) / w
            self.stds = np.sqrt(np.maximum(var, 1e-10))

            if abs(ll - prev_ll) < self.tol * max(abs(prev_ll), 1.0):
                break
            prev_ll = ll

        order = np.argsort(self.means)          # 평균 수익률 오름차순으로 상태 정렬
        self.means = self.means[order]
        self.stds = self.stds[order]
        self.start_prob = self.start_prob[order]
        self.trans = self.trans[np.ix_(order, order)]
        self.fitted = True
        return self

    def filter(self, x: np.ndarray) -> np.ndarray:
        """각 시점의 국면 사후확률 (필터링, 미래 정보 미사용)."""
        if not self.fitted:
            raise RuntimeError("HMM이 학습되지 않음")
        x = np.asarray(x, dtype=float)
        finite = np.isfinite(x)
        filled = np.where(finite, x, float(np.nanmedian(x[finite])) if finite.any() else 0.0)
        B = self._emission(filled)
        alpha, _, _ = self._forward(B)
        return alpha

    def describe(self) -> list[str]:
        """상태별 한글 설명."""
        if not self.fitted:
            return []
        names = []
        for mu, sd in zip(self.means, self.stds):
            direction = "상승" if mu > 0.15 else ("하락" if mu < -0.15 else "횡보")
            vol = "고변동" if sd > np.median(self.stds) * 1.3 else "저변동"
            names.append(f"{direction}·{vol}")
        return names
