"""Scorer-only labels and anchored deterministic scoring; no inference client."""

from .deterministic import CandidateAnswer, ScoreRecord, ScoringLabel, score, summarize_scores

__all__ = ["CandidateAnswer", "ScoreRecord", "ScoringLabel", "score", "summarize_scores"]
