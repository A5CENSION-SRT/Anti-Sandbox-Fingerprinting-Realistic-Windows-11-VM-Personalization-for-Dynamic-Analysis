"""Artifact expansion pipeline (ADR-014: renamed from services/generators/).

Transforms AI-generated seeds into full artifact bundles that downstream
services consume via ServiceContext.expansion.  Each expander converts a
seed list + per-day rate + timeline_days into a deterministic set of
artifact descriptors.

Phases:
    Phase 2 (current): ExpansionBundle + per-day rate config wired in.
    Phase 4a: ExpansionOrchestrator consumes scheduler events instead of
              rolling its own timeline.
"""

__all__ = ["ExpansionBundle", "ExpansionOrchestrator"]


def __getattr__(name):
    if name == "ExpansionBundle":
        from services.expansion.bundle import ExpansionBundle
        return ExpansionBundle
    if name == "ExpansionOrchestrator":
        from services.expansion.orchestrator import ExpansionOrchestrator
        return ExpansionOrchestrator
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
