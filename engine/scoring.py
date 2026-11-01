"""Calibration and open-set scorers computed offline from saved logits/embeddings.

Convention for every scorer: higher = more confident the item is a KNOWN class.
"""

from __future__ import annotations

import numpy as np
from scipy.optimize import minimize_scalar
from scipy.special import logsumexp, softmax


def fit_temperature(logits: np.ndarray, y: np.ndarray) -> float:
    """Temperature scaling on validation logits only (spec section 2)."""

    def nll(t: float) -> float:
        lp = logits / t - logsumexp(logits / t, axis=1, keepdims=True)
        return float(-lp[np.arange(len(y)), y].mean())

    return float(minimize_scalar(nll, bounds=(0.05, 20.0), method="bounded").x)


def msp(logits: np.ndarray, t: float = 1.0) -> np.ndarray:
    return softmax(logits / t, axis=1).max(axis=1)


def energy(logits: np.ndarray, t: float = 1.0) -> np.ndarray:
    return t * logsumexp(logits / t, axis=1)  # = -E(x); higher = more known


class Mahalanobis:
    """Class-conditional Gaussians with a shared covariance on pooled embeddings."""

    def __init__(self, emb: np.ndarray, y: np.ndarray, n_classes: int, eps: float = 1e-3) -> None:
        self.mu = np.stack([emb[y == c].mean(0) for c in range(n_classes)])
        centred = emb - self.mu[y]
        cov = centred.T @ centred / len(emb) + eps * np.eye(emb.shape[1])
        self.prec = np.linalg.inv(cov)

    def score(self, emb: np.ndarray) -> np.ndarray:
        # (x-mu_c)^T P (x-mu_c) for every class, without materialising n x C x D.
        xp = emb @ self.prec
        mp = self.mu @ self.prec
        m = (xp * emb).sum(1)[:, None] - 2 * xp @ self.mu.T + (mp * self.mu).sum(1)[None, :]
        return -m.min(axis=1)  # negative squared distance to the nearest class


def threshold_for_retention(val_known_scores: np.ndarray, retain: float = 0.95) -> float:
    """Pre-registered operating point: keep `retain` of known validation items."""
    return float(np.percentile(val_known_scores, 100 * (1 - retain)))
