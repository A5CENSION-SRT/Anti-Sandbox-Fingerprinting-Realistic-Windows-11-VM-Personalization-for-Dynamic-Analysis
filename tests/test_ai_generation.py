"""Tests for AI-powered profile generation and permutation engines.

Tests cover:
1. Schema validation
2. Filename permutation
3. Content variation
4. Fallback behavior when API unavailable
"""

import pytest
from datetime import date
from pathlib import Path

from services.ai.schemas import (
    PersonaContext,
    PersonaInterests,
    PersonaWorkStyle,
    TechProficiency,
    DownloadSeed,
    DocumentSeed,
    ExpansionRule,
    FileCategory,
)
from services.generators.filename_permutator import FilenamePermutator, expand_filename_pattern
from services.generators.content_variator import ContentVariator, vary_content


# ---------------------------------------------------------------------------
# Schema Tests
# ---------------------------------------------------------------------------

class TestPersonaContext:
    """Test PersonaContext schema validation."""
    
    def test_valid_persona(self):
        """Test creating a valid persona."""
        persona = PersonaContext(
            full_name="John Smith",
            username="john.smith",
            email="john.smith@acme.com",
            organization="Acme Corp",
            occupation="Software Engineer",
            age_range="28-35",
            tech_proficiency=TechProficiency.HIGH,
            interests=PersonaInterests(
                hobbies=["coding", "gaming", "reading"],
                professional_topics=["cloud", "devops"],
            ),
            work_style=PersonaWorkStyle(
                description="Focused developer",
                typical_tools=["VS Code", "Docker", "Git"],
            ),
            project_names=["API Gateway", "Auth Service", "Data Pipeline"],
            colleague_names=["Jane Doe", "Bob Wilson"],
        )
        
        assert persona.full_name == "John Smith"
        assert persona.tech_proficiency == TechProficiency.HIGH
    
    def test_username_validation(self):
        """Test username pattern validation."""
        with pytest.raises(ValueError):
            PersonaContext(
                full_name="Test User",
                username="Invalid User Name",  # Spaces not allowed
                email="test@test.com",
                organization="Test",
                occupation="Test",
                age_range="20-30",
                interests=PersonaInterests(
                    hobbies=["a", "b", "c"],
                    professional_topics=["x", "y"],
                ),
                work_style=PersonaWorkStyle(
                    description="Test",
                    typical_tools=["Tool"],
                ),
                project_names=["P1", "P2", "P3"],
                colleague_names=["C1", "C2"],
            )


class TestDownloadSeed:
    """Test DownloadSeed schema."""
    
    def test_valid_download_seed(self):
        """Test creating a valid download seed."""
        seed = DownloadSeed(
            seed_id="dl_001",
            filename_pattern="Report_{quarter}_{year}.pdf",
            url_template="https://example.com/reports/{quarter}_{year}.pdf",
            referrer_template="https://example.com/dashboard",
            mime_type="application/pdf",
            size_range_bytes=(100000, 5000000),
            context="Quarterly reports",
            variables={
                "quarter": ["Q1", "Q2", "Q3", "Q4"],
                "year": ["2023", "2024"],
            },
            expansion=ExpansionRule(target_count=100, date_range_days=90),
        )
        
        assert seed.seed_id == "dl_001"
        assert len(seed.variables["quarter"]) == 4


# ---------------------------------------------------------------------------
# Filename Permutation Tests
# ---------------------------------------------------------------------------

class TestFilenamePermutator:
    """Test filename permutation engine."""
    
    def test_simple_pattern_expansion(self):
        """Test expanding a simple pattern."""
        filenames = expand_filename_pattern(
            pattern="{project}_Report.docx",
            variables={"project": ["Alpha", "Beta", "Gamma"]},
            target_count=10,
            seed=42,
        )
        
        assert len(filenames) <= 10
        assert any("Alpha" in f for f in filenames)
        assert any("Beta" in f for f in filenames)
    
    def test_date_expansion(self):
        """Test expanding patterns with dates."""
        permutator = FilenamePermutator(seed=42)
        filenames = permutator.expand_pattern(
            pattern="Report_{date}.pdf",
            variables={},
            target_count=20,
            include_dates=True,
        )
        
        assert len(filenames) <= 20
        # All should have date-like patterns
        assert all(".pdf" in f for f in filenames)
    
    def test_counter_expansion(self):
        """Test expanding patterns with counters."""
        permutator = FilenamePermutator(seed=42)
        filenames = permutator.expand_pattern(
            pattern="IMG_{date}_{counter}.jpg",
            variables={},
            target_count=50,
        )
        
        assert len(filenames) <= 50
        # Should have sequential counters
        assert any("0001" in f for f in filenames)
    
    def test_version_variations(self):
        """Test adding version variations."""
        permutator = FilenamePermutator(seed=42)
        filenames = permutator.expand_pattern(
            pattern="Document.docx",
            variables={},
            target_count=20,
            include_versions=True,
        )
        
        # Should have some versioned files
        assert any("v2" in f or "_v2" in f or "v3" in f for f in filenames)
    
    def test_reproducibility(self):
        """Test that same seed produces same results."""
        filenames1 = expand_filename_pattern(
            pattern="{project}_Report.docx",
            variables={"project": ["A", "B", "C"]},
            target_count=10,
            seed=42,
        )
        
        filenames2 = expand_filename_pattern(
            pattern="{project}_Report.docx",
            variables={"project": ["A", "B", "C"]},
            target_count=10,
            seed=42,
        )
        
        assert filenames1 == filenames2


# ---------------------------------------------------------------------------
# Content Variation Tests
# ---------------------------------------------------------------------------

class TestContentVariator:
    """Test content variation engine."""
    
    def test_simple_template_expansion(self):
        """Test expanding a simple template."""
        contents = vary_content(
            template="Meeting notes for {project}",
            variables={"project": ["Alpha", "Beta"]},
            count=5,
            seed=42,
        )
        
        assert len(contents) <= 5
        assert any("Alpha" in c for c in contents)
    
    def test_auto_date_variable(self):
        """Test automatic date variable expansion."""
        variator = ContentVariator(seed=42)
        contents = variator.expand_template(
            template="Report dated {date}",
            variables={},
            target_count=5,
        )
        
        assert len(contents) <= 5
        # Dates should be filled in
        assert all("{date}" not in c for c in contents)
    
    def test_multiple_variables(self):
        """Test expanding multiple variables."""
        variator = ContentVariator(seed=42)
        contents = variator.expand_template(
            template="{project} by {author} on {date}",
            variables={
                "project": ["Alpha", "Beta"],
                "author": ["John", "Jane"],
            },
            target_count=10,
        )
        
        assert len(contents) <= 10
        # Should have different combinations
        unique_contents = set(contents)
        assert len(unique_contents) >= min(4, len(contents))


# ---------------------------------------------------------------------------
# Bulk Generator Tests (without API)
# ---------------------------------------------------------------------------

class TestBulkGeneratorsWithFallback:
    """Test bulk generators using fallback data (no API required)."""
    
    def test_bulk_downloads_generator(self):
        """Test bulk downloads expansion with fallback seeds."""
        from services.generators.bulk_downloads import BulkDownloadsGenerator
        from services.ai.persona_generator import create_fallback_persona
        from services.ai.seed_generators.downloads import DownloadSeedGenerator
        from services.ai.gemini_client import GeminiClient
        
        # Create fallback persona
        persona = create_fallback_persona(
            occupation="Software Engineer",
            profile_type="developer",
        )
        
        # Get fallback seeds (no API call)
        seed_gen = DownloadSeedGenerator(
            client=GeminiClient(),  # Won't actually call API
            seed_count=10,
            total_target=100,
        )
        seeds = seed_gen._generate_fallback_seeds(persona)
        
        # Expand seeds
        generator = BulkDownloadsGenerator(
            seed=42,
            timeline_days=90,
            target_total=100,
        )
        downloads = generator.expand_seeds(seeds, persona)
        
        assert len(downloads) <= 100
        assert all(d.filename for d in downloads)
        assert all(d.url for d in downloads)
    
    def test_bulk_documents_generator(self):
        """Test bulk documents expansion with fallback seeds."""
        from services.generators.bulk_documents import BulkDocumentsGenerator
        from services.ai.persona_generator import create_fallback_persona
        from services.ai.seed_generators.documents import DocumentSeedGenerator
        from services.ai.gemini_client import GeminiClient
        
        persona = create_fallback_persona(
            occupation="Marketing Manager",
            profile_type="office_user",
        )
        
        seed_gen = DocumentSeedGenerator(
            client=GeminiClient(),
            seed_count=10,
            total_target=100,
        )
        seeds = seed_gen._generate_fallback_seeds(persona)
        
        generator = BulkDocumentsGenerator(
            seed=42,
            timeline_days=90,
            target_total=100,
        )
        documents = generator.expand_seeds(seeds, persona)
        
        assert len(documents) <= 100
        assert all(d.filename for d in documents)
        assert all(d.content for d in documents)


# ---------------------------------------------------------------------------
# Scale Tests
# ---------------------------------------------------------------------------

class TestScaleGeneration:
    """Test generating large numbers of artifacts."""
    
    def test_generate_1000_filenames(self):
        """Test generating 1000 filenames efficiently."""
        import time
        
        permutator = FilenamePermutator(seed=42)
        
        start = time.time()
        filenames = permutator.expand_pattern(
            pattern="{project}_{type}_{date}{suffix}.docx",
            variables={
                "project": ["Alpha", "Beta", "Gamma", "Delta", "Epsilon"],
                "type": ["Report", "Summary", "Notes", "Plan", "Review"],
            },
            target_count=1000,
        )
        elapsed = time.time() - start
        
        assert len(filenames) >= 500  # At least 500 unique
        assert elapsed < 5.0  # Should complete in under 5 seconds
    
    def test_generate_varied_content(self):
        """Test generating varied content efficiently."""
        import time
        
        variator = ContentVariator(seed=42)
        
        start = time.time()
        contents = variator.expand_template(
            template="Meeting on {date} for {project}. Attended by {attendee}.",
            variables={
                "project": ["Alpha", "Beta", "Gamma"],
                "attendee": ["John", "Jane", "Bob", "Alice"],
            },
            target_count=500,
        )
        elapsed = time.time() - start
        
        assert len(contents) >= 100
        assert elapsed < 5.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
