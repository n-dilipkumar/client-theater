"""Open-source Digital Sales Room - automation and validation toolchain.

This package holds the shared tooling that the Orchestrator and every sub-agent
use. The most important module is :mod:`tools.jev`, which is the single entry
point for validation decisions. Every validation in this project is a typed
Jev judgment with a recorded probability, never a language-model opinion.
"""

__all__ = ["jev"]
