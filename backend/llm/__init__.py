"""Reasoning layer: a ``Reasoner`` interface with two backends.

* ``GroqClient``    — real LLM via Groq's OpenAI-compatible chat/completions API,
                      with tool (function) calling. Default model openai/gpt-oss-120b.
* ``LocalReasoner`` — deterministic, no-network fallback that synthesizes a brief
                      from recalled memories so the agent loop runs offline.
"""
from __future__ import annotations

from .client import GroqClient, LocalReasoner, Reasoner, build_reasoner

__all__ = ["Reasoner", "GroqClient", "LocalReasoner", "build_reasoner"]
