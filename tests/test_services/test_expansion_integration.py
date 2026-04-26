"""Integration tests for the expansion pipeline (Phase 2).

Validates that ExpansionOrchestrator produces artifact counts proportional
to per_day × timeline_days × (1 ± jitter), meeting the A11/A13/A14/A15 thresholds.
"""

import random
from datetime import datetime, timezone

import pytest

from services.expansion.bundle import ExpansionBundle
from services.expansion.orchestrator import ExpansionOrchestrator


# ---------------------------------------------------------------------------
# Minimal persona stub
# ---------------------------------------------------------------------------

class _Persona:
    def __init__(self, archetype: str = "home_user", timeline_days: int = 360):
        self.profile_archetype = archetype
        self.timeline_days = timeline_days
        self.username = "jane.doe"
        self.browsing_categories = ["news", "shopping", "social"]
        self.project_names = ["Project Alpha"]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestExpansionOrchestrator:
    def _run(self, archetype: str = "home_user", timeline_days: int = 360,
             scale_config: dict | None = None) -> ExpansionBundle:
        persona = _Persona(archetype=archetype, timeline_days=timeline_days)
        rng = random.Random(42)
        orch = ExpansionOrchestrator(scale_config=scale_config or {})
        return orch.expand(persona=persona, rng=rng, scale_config=scale_config or {})

    def test_document_count_proportional_to_timeline(self):
        bundle = self._run(timeline_days=360)
        # 12.5 docs/day × 360 days × (1 ± 0.3) → 3150–5850; minimum 1
        assert len(bundle.documents) >= 3000

    def test_download_count_proportional_to_timeline(self):
        bundle = self._run(timeline_days=360)
        # 4.2 dl/day × 360 × (1 ± 0.3) → 1058–1965; minimum 1
        assert len(bundle.downloads) >= 900

    def test_browsing_count_proportional_to_timeline(self):
        bundle = self._run(timeline_days=360)
        # 83 url/day × 360 × (1 ± 0.5) → ~14940–44820
        assert len(bundle.browsing) >= 10000

    def test_media_count_proportional_to_timeline(self):
        bundle = self._run(timeline_days=360)
        # 2.1 media/day × 360 × (1 ± 0.4) → 453–1058
        assert len(bundle.media) >= 400

    def test_scale_config_overrides_defaults(self):
        scale_config = {
            "documents": {"per_day": 50.0, "jitter": 0.1},
            "downloads": {"per_day": 0.5, "jitter": 0.1},
        }
        bundle = self._run(timeline_days=100, scale_config=scale_config)
        # 50 docs/day × 100 days × (1 ± 0.1) → 4500–5500
        assert len(bundle.documents) >= 4000
        # 0.5 dl/day × 100 days → ~45–55 but bounded at 1
        assert len(bundle.downloads) >= 1

    def test_determinism_same_seed(self):
        persona = _Persona(timeline_days=90)
        orch = ExpansionOrchestrator()
        b1 = orch.expand(persona, random.Random(99), {})
        b2 = orch.expand(persona, random.Random(99), {})
        assert len(b1.documents) == len(b2.documents)
        assert len(b1.downloads) == len(b2.downloads)
        assert len(b1.browsing) == len(b2.browsing)

    def test_different_seeds_differ(self):
        persona = _Persona(timeline_days=360)
        orch = ExpansionOrchestrator()
        b1 = orch.expand(persona, random.Random(1), {})
        b2 = orch.expand(persona, random.Random(999), {})
        # Count difference expected due to jitter
        assert abs(len(b1.documents) - len(b2.documents)) > 0

    def test_developer_archetype_doc_extensions(self):
        bundle = self._run(archetype="developer", timeline_days=90)
        exts = {d.relative_path.rsplit(".", 1)[-1] for d in bundle.documents}
        dev_exts = {"md", "txt", "py", "json"}
        assert exts & dev_exts, "Developer should produce code/markdown files"

    def test_office_user_archetype_doc_extensions(self):
        bundle = self._run(archetype="office_user", timeline_days=90)
        exts = {d.relative_path.rsplit(".", 1)[-1] for d in bundle.documents}
        office_exts = {"docx", "xlsx", "pdf"}
        assert exts & office_exts, "Office user should produce Office documents"

    def test_bundle_total_artifacts(self):
        bundle = self._run(timeline_days=360)
        assert bundle.total_artifacts > 10000

    def test_short_timeline_still_produces_artifacts(self):
        bundle = self._run(timeline_days=30)
        assert len(bundle.documents) >= 1
        assert len(bundle.downloads) >= 1
        assert len(bundle.browsing) >= 1

    def test_browsing_descriptors_have_valid_urls(self):
        bundle = self._run(timeline_days=30)
        for bd in bundle.browsing[:20]:
            assert bd.url.startswith("http")
            assert bd.visit_count >= 1
            assert 0.0 <= bd.visit_time_offset_days <= 30.0

    def test_media_descriptors_have_valid_types(self):
        bundle = self._run(timeline_days=90)
        valid_types = {"image", "video", "audio"}
        for md in bundle.media[:20]:
            assert md.media_type in valid_types

    def test_document_paths_under_users(self):
        bundle = self._run(timeline_days=30)
        for doc in bundle.documents[:10]:
            assert doc.relative_path.startswith("Users/")

    def test_timestamp_offset_days_assigned_to_documents(self):
        bundle = self._run(timeline_days=360)
        offsets = [d.timestamp_offset_days for d in bundle.documents[:50]]
        assert all(0.0 <= o <= 360.0 for o in offsets), "offset out of [0, timeline] range"
        assert len(set(round(o, 0) for o in offsets)) > 10, "timestamps not spread across timeline"

    def test_timestamp_offset_days_assigned_to_downloads(self):
        bundle = self._run(timeline_days=360)
        offsets = [d.timestamp_offset_days for d in bundle.downloads[:20]]
        assert all(0.0 <= o <= 360.0 for o in offsets)

    def test_timestamp_offset_days_assigned_to_media(self):
        bundle = self._run(timeline_days=360)
        offsets = [m.timestamp_offset_days for m in bundle.media[:20]]
        assert all(0.0 <= o <= 360.0 for o in offsets)

    def test_timestamp_spread_covers_full_timeline(self):
        bundle = self._run(timeline_days=360)
        all_offsets = (
            [d.timestamp_offset_days for d in bundle.documents]
            + [d.timestamp_offset_days for d in bundle.downloads]
        )
        assert max(all_offsets) >= 300.0, "timestamps don't reach the end of the 360-day window"
        assert min(all_offsets) <= 60.0, "timestamps don't reach the start of the timeline"
