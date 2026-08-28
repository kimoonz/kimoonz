"""베이지안 확률 엔진.

사후 로그오즈 = 사전 로그오즈 + Σ_i (신뢰도_i × 로그우도비_i)

  * 각 피처를 학습구간 분위수로 구간화하고, 구간별 우도비
    LR = P(구간|상승) / P(구간|하락) 을 라플라스 평활로 추정한다.
  * 표본이 적은 구간은 경험적 베이즈로 0 쪽으로 축소한다
    (shrink = n / (n + m) → 근거가 얇으면 확률을 안 움직임).
  * 나이브 베이즈는 피처 독립을 가정해 확률이 과신(overconfident)되므로,
    검증 구간에서 p = sigmoid(a·S + b) 로지스틱 보정을 학습해 되돌린다.
    보통 a < 1 이 나오고, 이게 '확률을 믿을 수 있게' 만드는 핵심이다.
  * 국면(HMM) 같은 소프트 증거는 구간 가중치가 원핫 대신 확률벡터가 될 뿐,
    같은 식으로 처리된다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

_EPS = 1e-12


def sigmoid(x: np.ndarray | float) -> np.ndarray | float:
    return 1.0 / (1.0 + np.exp(-np.clip(x, -40.0, 40.0)))


def logit(p: float) -> float:
    p = float(np.clip(p, 1e-6, 1 - 1e-6))
    return float(np.log(p / (1.0 - p)))


@dataclass
class EvidenceTable:
    """피처 하나의 구간별 우도비 표."""

    name: str
    edges: np.ndarray                 # 내부 경계 (categorical이면 카테고리 값)
    log_lr: np.ndarray                # 구간별 로그우도비
    shrink: np.ndarray                # 구간별 신뢰도 (0~1)
    support: np.ndarray               # 구간별 표본 수
    categorical: bool = False

    @property
    def n_bins(self) -> int:
        return len(self.log_lr)

    def weights(self, values: np.ndarray) -> np.ndarray:
        """값 -> (n, n_bins) 원핫 가중치. 결측은 전부 0 (증거 없음)."""
        n = len(values)
        W = np.zeros((n, self.n_bins))
        finite = np.isfinite(values)
        if not finite.any():
            return W
        if self.categorical:
            idx = np.searchsorted(self.edges, values[finite])
            idx = np.clip(idx, 0, self.n_bins - 1)
            exact = self.edges[idx] == values[finite]
            rows = np.flatnonzero(finite)[exact]
            W[rows, idx[exact]] = 1.0
        else:
            idx = np.searchsorted(self.edges, values[finite], side="right")
            W[np.flatnonzero(finite), np.clip(idx, 0, self.n_bins - 1)] = 1.0
        return W

    def bin_index(self, value: float) -> int | None:
        if not np.isfinite(value):
            return None
        if self.categorical:
            hits = np.flatnonzero(self.edges == value)
            return int(hits[0]) if len(hits) else None
        return int(np.clip(np.searchsorted(self.edges, value, side="right"), 0, self.n_bins - 1))

    def describe_bin(self, index: int) -> str:
        """구간을 사람이 읽을 수 있게."""
        if self.categorical:
            return f"= {self.edges[index]:g}"
        lo = self.edges[index - 1] if index > 0 else None
        hi = self.edges[index] if index < len(self.edges) else None
        if lo is None:
            return f"< {hi:.2f}"
        if hi is None:
            return f"> {lo:.2f}"
        return f"{lo:.2f} ~ {hi:.2f}"


@dataclass
class BayesModel:
    n_bins: int = 5
    laplace_alpha: float = 1.0
    shrink_m: float = 40.0
    prior_strength: float = 20.0
    prior_up_rate: float = 0.5
    validation_frac: float = 0.25

    prior_logodds: float = 0.0
    tables: dict[str, EvidenceTable] = field(default_factory=dict)
    soft_tables: dict[str, EvidenceTable] = field(default_factory=dict)
    calib_a: float = 1.0
    calib_b: float = 0.0
    n_train: int = 0
    base_rate: float = 0.5
    fitted: bool = False

    # ------------------------------------------------------------------ 학습
    def fit(
        self,
        X: pd.DataFrame,
        y: pd.Series,
        soft: dict[str, np.ndarray] | None = None,
        sample_weight: np.ndarray | None = None,
        embargo: int = 0,
        n_folds: int = 5,
    ) -> "BayesModel":
        """우도비 표를 학습하고, 퍼지드 K-폴드로 보정계수를 구한다.

        embargo: 라벨이 미래 h봉을 보므로 폴드 경계 양쪽 h봉을 학습에서 제외한다.
                 이걸 빼먹으면 보정계수가 낙관적으로 부풀려진다.
        """
        y_arr = np.asarray(y, dtype=float)
        mask = np.isfinite(y_arr)
        X = X.loc[mask]
        y_arr = y_arr[mask]
        soft = {k: np.asarray(v)[mask] for k, v in (soft or {}).items()}
        n = len(y_arr)
        if n < 50:
            raise ValueError(f"학습 표본 부족: {n}")
        w = np.ones(n) if sample_weight is None else np.asarray(sample_weight, float)[mask]

        # 1) 전체 데이터로 최종 표 학습
        self.prior_logodds, self.tables, self.soft_tables = self._fit_tables(X, y_arr, w, soft)
        self.base_rate = float(sigmoid(self.prior_logodds))

        # 2) 폴드 밖 점수(OOF)를 모아 보정 (a, b) 학습
        oof = self._oof_scores(X, y_arr, w, soft, embargo=embargo, n_folds=n_folds)
        if oof is not None:
            scores, targets, weights = oof
            self.calib_a, self.calib_b = _fit_platt(scores, targets, weights,
                                                    self.prior_logodds)
        else:
            self.calib_a, self.calib_b = 0.3, 0.0

        self.n_train = n
        self.fitted = True
        return self

    def _fit_tables(self, X: pd.DataFrame, y: np.ndarray, w: np.ndarray,
                    soft: dict[str, np.ndarray]):
        """사전 로그오즈 + 피처별 우도비 표를 만든다."""
        up = float(np.average(y, weights=w)) if w.sum() > 0 else 0.5
        a0 = self.prior_strength * self.prior_up_rate
        b0 = self.prior_strength * (1.0 - self.prior_up_rate)
        post_up = (a0 + up * w.sum()) / (a0 + b0 + w.sum())
        prior_logodds = logit(post_up)

        tables: dict[str, EvidenceTable] = {}
        for col in X.columns:
            table = self._fit_table(col, X[col].to_numpy(dtype=float), y, w)
            if table is not None:
                tables[col] = table
        soft_tables = {name: self._fit_soft_table(name, W, y, w)
                       for name, W in soft.items()}
        return prior_logodds, tables, soft_tables

    def _oof_scores(self, X: pd.DataFrame, y: np.ndarray, w: np.ndarray,
                    soft: dict[str, np.ndarray], embargo: int, n_folds: int):
        """시간순 K-폴드. 각 폴드는 나머지 구간(경계 embargo 제외)으로 학습."""
        n = len(y)
        n_folds = int(np.clip(n_folds, 2, 10))
        if n < 200:
            return None
        bounds = np.linspace(0, n, n_folds + 1).astype(int)
        scores, targets, weights = [], [], []
        for k in range(n_folds):
            lo, hi = bounds[k], bounds[k + 1]
            if hi - lo < 20:
                continue
            train_idx = np.concatenate([
                np.arange(0, max(lo - embargo, 0)),
                np.arange(min(hi + embargo, n), n),
            ])
            if len(train_idx) < 100:
                continue
            Xt, yt, wt = X.iloc[train_idx], y[train_idx], w[train_idx]
            soft_t = {kk: vv[train_idx] for kk, vv in soft.items()}
            try:
                prior, tables, soft_tables = self._fit_tables(Xt, yt, wt, soft_t)
            except Exception:
                continue
            fold = slice(lo, hi)
            s = self._score_with(X.iloc[fold], {kk: vv[fold] for kk, vv in soft.items()},
                                 prior, tables, soft_tables)
            scores.append(s)
            targets.append(y[fold])
            weights.append(w[fold])
        if not scores:
            return None
        return (np.concatenate(scores), np.concatenate(targets), np.concatenate(weights))

    def _fit_table(self, name: str, values: np.ndarray, y: np.ndarray,
                   w: np.ndarray) -> EvidenceTable | None:
        finite = np.isfinite(values)
        if finite.sum() < 40:
            return None
        vals = values[finite]
        uniq = np.unique(vals)
        categorical = len(uniq) <= 12 and np.allclose(uniq, np.round(uniq))

        if categorical:
            edges = uniq
            n_bins = len(uniq)
        else:
            qs = np.linspace(0, 100, self.n_bins + 1)[1:-1]
            edges = np.unique(np.percentile(vals, qs))
            if len(edges) == 0:
                return None
            n_bins = len(edges) + 1

        table = EvidenceTable(name, edges, np.zeros(n_bins), np.zeros(n_bins),
                              np.zeros(n_bins), categorical)
        W = table.weights(values) * w[:, None]
        return self._counts_to_table(table, W, y)

    def _fit_soft_table(self, name: str, W_raw: np.ndarray, y: np.ndarray,
                        w: np.ndarray) -> EvidenceTable:
        n_bins = W_raw.shape[1]
        table = EvidenceTable(name, np.arange(n_bins), np.zeros(n_bins),
                              np.zeros(n_bins), np.zeros(n_bins), categorical=True)
        return self._counts_to_table(table, W_raw * w[:, None], y)

    def _counts_to_table(self, table: EvidenceTable, W: np.ndarray,
                         y: np.ndarray) -> EvidenceTable:
        """가중 카운트 -> 로그우도비 + 축소계수."""
        n1 = W.T @ y                      # 구간별 상승 표본 (가중)
        n0 = W.T @ (1.0 - y)
        alpha = self.laplace_alpha
        B = table.n_bins
        p1 = (n1 + alpha) / (n1.sum() + alpha * B)
        p0 = (n0 + alpha) / (n0.sum() + alpha * B)
        table.log_lr = np.log(p1 + _EPS) - np.log(p0 + _EPS)
        support = n1 + n0
        table.support = support
        table.shrink = support / (support + self.shrink_m)
        return table

    # ------------------------------------------------------------------ 예측
    @staticmethod
    def _score_with(X: pd.DataFrame, soft: dict[str, np.ndarray] | None,
                    prior_logodds: float, tables: dict[str, EvidenceTable],
                    soft_tables: dict[str, EvidenceTable]) -> np.ndarray:
        """주어진 표로 원시 로그오즈를 계산 (사전 + 증거 기여 합)."""
        score = np.full(len(X), prior_logodds)
        for name, table in tables.items():
            if name not in X.columns:
                continue
            W = table.weights(X[name].to_numpy(dtype=float))
            score += W @ (table.log_lr * table.shrink)
        for name, table in (soft_tables or {}).items():
            W = (soft or {}).get(name)
            if W is None:
                continue
            score += np.asarray(W, dtype=float) @ (table.log_lr * table.shrink)
        return score

    def _raw_score(self, X: pd.DataFrame, soft: dict[str, np.ndarray] | None = None) -> np.ndarray:
        return self._score_with(X, soft, self.prior_logodds, self.tables, self.soft_tables)

    def predict_proba(self, X: pd.DataFrame,
                      soft: dict[str, np.ndarray] | None = None) -> np.ndarray:
        """보정된 사후 상승확률."""
        if not self.fitted:
            raise RuntimeError("모델이 학습되지 않음")
        s = self._raw_score(X, soft)
        return sigmoid(self.calib_a * (s - self.prior_logodds) + self.calib_b + self.prior_logodds)

    def explain(self, row: pd.Series, soft_row: dict[str, np.ndarray] | None = None,
                top: int = 6) -> list[dict]:
        """이번 판단에 각 증거가 로그오즈를 얼마나 밀었는지 (보정 반영)."""
        out: list[dict] = []
        for name, table in self.tables.items():
            if name not in row.index:
                continue
            idx = table.bin_index(float(row[name]))
            if idx is None:
                continue
            contrib = float(table.log_lr[idx] * table.shrink[idx]) * self.calib_a
            out.append({
                "name": name, "value": float(row[name]), "bin": table.describe_bin(idx),
                "contribution": contrib, "support": float(table.support[idx]),
            })
        for name, table in (self.soft_tables or {}).items():
            W = (soft_row or {}).get(name)
            if W is None:
                continue
            W = np.asarray(W, dtype=float).ravel()
            contrib = float(W @ (table.log_lr * table.shrink)) * self.calib_a
            best = int(np.argmax(W))
            out.append({
                "name": name, "value": float(best), "bin": f"상태{best} ({W[best]:.0%})",
                "contribution": contrib, "support": float(table.support.sum()),
            })
        out.sort(key=lambda d: abs(d["contribution"]), reverse=True)
        return out[:top]


def _fit_platt(scores: np.ndarray, y: np.ndarray, w: np.ndarray,
               offset: float, max_iter: int = 100) -> tuple[float, float]:
    """p = sigmoid(a·(S-offset) + b + offset) 의 (a, b)를 뉴턴법으로 학습.

    a 는 나이브 베이즈의 과신을 깎는 상관 보정 계수. a<1 이 정상.
    """
    s = np.asarray(scores, float) - offset
    y = np.asarray(y, float)
    w = np.asarray(w, float)
    good = np.isfinite(s) & np.isfinite(y)
    s, y, w = s[good], y[good], w[good]
    if len(s) < 20 or np.std(s) < 1e-9:
        return 0.5, 0.0

    a, b = 0.5, 0.0
    for _ in range(max_iter):
        p = sigmoid(a * s + b + offset)
        resid = w * (y - p)
        g = np.array([resid @ s, resid.sum()])
        v = w * p * (1.0 - p)
        H = np.array([[-(v * s * s).sum(), -(v * s).sum()],
                      [-(v * s).sum(), -v.sum()]])
        H -= np.eye(2) * 1e-6            # 정규화 (역행렬 안정)
        try:
            step = np.linalg.solve(H, g)
        except np.linalg.LinAlgError:
            break
        a_new, b_new = a - step[0], b - step[1]
        if not np.isfinite(a_new) or not np.isfinite(b_new):
            break
        moved = abs(a_new - a) + abs(b_new - b)
        a, b = a_new, b_new
        if moved < 1e-8:
            break

    a = float(np.clip(a, 0.02, 1.5))     # 음수/폭주 방지
    b = float(np.clip(b, -2.0, 2.0))
    return a, b


# ---------------------------------------------------------------------- 평가
def brier_score(p: np.ndarray, y: np.ndarray) -> float:
    return float(np.mean((p - y) ** 2))


def log_loss(p: np.ndarray, y: np.ndarray) -> float:
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def auc(p: np.ndarray, y: np.ndarray) -> float:
    """Mann-Whitney U 기반 ROC AUC."""
    pos, neg = p[y == 1], p[y == 0]
    if len(pos) == 0 or len(neg) == 0:
        return float("nan")
    order = np.argsort(np.concatenate([pos, neg]))
    ranks = np.empty(len(order), dtype=float)
    ranks[order] = np.arange(1, len(order) + 1)
    # 동점 처리
    allv = np.concatenate([pos, neg])[order]
    i = 0
    sorted_ranks = ranks[order]
    while i < len(allv):
        j = i
        while j + 1 < len(allv) and allv[j + 1] == allv[i]:
            j += 1
        if j > i:
            sorted_ranks[i:j + 1] = sorted_ranks[i:j + 1].mean()
        i = j + 1
    ranks[order] = sorted_ranks
    r_pos = ranks[:len(pos)].sum()
    return float((r_pos - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg)))


def reliability(p: np.ndarray, y: np.ndarray, bins: int = 10) -> pd.DataFrame:
    """신뢰도 곡선 표: 예측확률 구간별 실제 적중률."""
    edges = np.linspace(0, 1, bins + 1)
    idx = np.clip(np.searchsorted(edges, p, side="right") - 1, 0, bins - 1)
    rows = []
    for b in range(bins):
        m = idx == b
        if m.sum() == 0:
            continue
        rows.append({
            "구간": f"{edges[b]:.0%}~{edges[b+1]:.0%}",
            "건수": int(m.sum()),
            "예측평균": float(p[m].mean()),
            "실제적중": float(y[m].mean()),
        })
    return pd.DataFrame(rows)
