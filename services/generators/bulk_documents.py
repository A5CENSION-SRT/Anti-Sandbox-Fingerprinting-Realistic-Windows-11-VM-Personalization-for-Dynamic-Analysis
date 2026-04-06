"""Bulk documents generator that expands seeds into thousands of document artifacts.

Takes 20-50 document seeds from the AI generator and expands them into
2000-5000 unique documents using filename permutation and content variation.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from random import Random
from typing import Any, Dict, List, Optional

from services.ai.schemas import DocumentSeed, FileCategory, PersonaContext
from services.generators.filename_permutator import FilenamePermutator
from services.generators.content_variator import ContentVariator

logger = logging.getLogger(__name__)


# Minimal file headers for different document types
_FILE_HEADERS = {
    "docx": bytes([0x50, 0x4B, 0x03, 0x04, 0x14, 0x00, 0x06, 0x00]),  # ZIP/DOCX
    "xlsx": bytes([0x50, 0x4B, 0x03, 0x04, 0x14, 0x00, 0x06, 0x00]),  # ZIP/XLSX
    "pdf": b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n",
    "txt": b"",  # Plain text, no header
    "md": b"",   # Markdown, no header
    "json": b"{\n",  # JSON start
    "csv": b"",  # CSV, no header
    "rtf": b"{\\rtf1\\ansi\\deff0{\\fonttbl{\\f0 Times New Roman;}}",
}


@dataclass
class ExpandedDocument:
    """A fully expanded document artifact ready for creation."""
    
    filename: str
    relative_path: str  # Relative to user directory
    document_type: str
    category: FileCategory
    content: bytes
    created_time: datetime
    modified_time: datetime
    accessed_time: datetime
    context: str
    
    @property
    def size_bytes(self) -> int:
        return len(self.content)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization (excludes content)."""
        return {
            "filename": self.filename,
            "relative_path": self.relative_path,
            "document_type": self.document_type,
            "category": self.category.value,
            "size_bytes": self.size_bytes,
            "created_time": self.created_time.isoformat(),
            "modified_time": self.modified_time.isoformat(),
            "accessed_time": self.accessed_time.isoformat(),
            "context": self.context,
        }


class BulkDocumentsGenerator:
    """Expands document seeds into thousands of unique documents.
    
    Args:
        seed: Random seed for reproducibility.
        timeline_days: Days to spread documents across.
        target_total: Target total number of documents to generate.
    
    Example:
        >>> generator = BulkDocumentsGenerator(target_total=4500)
        >>> documents = generator.expand_seeds(seeds, persona)
        >>> print(len(documents))  # ~4500
    """
    
    def __init__(
        self,
        seed: int = 42,
        timeline_days: int = 90,
        target_total: int = 4500,
    ) -> None:
        self._rng = Random(seed)
        self._timeline_days = timeline_days
        self._target_total = target_total
        self._filename_permutator = FilenamePermutator(seed=seed, timeline_days=timeline_days)
        self._content_variator = ContentVariator(seed=seed, timeline_days=timeline_days)
        self._base_date = datetime.now(timezone.utc)
    
    def expand_seeds(
        self,
        seeds: List[DocumentSeed],
        persona: PersonaContext,
    ) -> List[ExpandedDocument]:
        """Expand all document seeds into complete document records.
        
        Args:
            seeds: List of DocumentSeed instances from AI generator.
            persona: PersonaContext for personalization and work patterns.
        
        Returns:
            List of ExpandedDocument instances.
        """
        if not seeds:
            logger.warning("No document seeds provided")
            return []
        
        all_documents: List[ExpandedDocument] = []
        
        for seed in seeds:
            target_count = seed.expansion.target_count
            if target_count <= 0:
                target_count = self._target_total // len(seeds)
            
            expanded = self._expand_single_seed(seed, persona, target_count)
            all_documents.extend(expanded)
            
            logger.debug(
                "Expanded seed '%s' → %d documents",
                seed.seed_id, len(expanded)
            )
        
        # Shuffle for realistic order
        self._rng.shuffle(all_documents)
        
        logger.info(
            "Expanded %d seeds → %d total documents",
            len(seeds), len(all_documents)
        )
        
        return all_documents[:self._target_total]
    
    def _expand_single_seed(
        self,
        seed: DocumentSeed,
        persona: PersonaContext,
        target_count: int,
    ) -> List[ExpandedDocument]:
        """Expand a single document seed into multiple documents."""
        # Add persona-specific variables
        variables = dict(seed.variables)
        variables.setdefault("author", [persona.full_name])
        variables.setdefault("project", persona.project_names)
        variables.setdefault("colleague", persona.colleague_names)
        
        # Generate filenames using permutator
        filenames = self._filename_permutator.expand_pattern(
            pattern=seed.filename_pattern,
            variables=variables,
            target_count=target_count,
            include_versions=seed.expansion.include_versions,
            include_dates=seed.expansion.include_dates,
            include_suffixes=seed.expansion.include_drafts,
        )
        
        # Generate content variations if template provided
        contents: List[str] = []
        if seed.content_template:
            contents = self._content_variator.expand_template(
                template=seed.content_template,
                variables=variables,
                target_count=len(filenames),
            )
        else:
            # Generate generic content based on theme
            contents = [
                self._generate_themed_content(seed.content_theme, seed.document_type, variables)
                for _ in range(len(filenames))
            ]
        
        documents = []
        
        for i, filename in enumerate(filenames):
            # Get or generate content
            content_text = contents[i] if i < len(contents) else contents[-1] if contents else ""
            content_bytes = self._prepare_content(content_text, seed.document_type)
            
            # Generate timestamps
            created_time = self._generate_document_time(persona, seed.category)
            modified_time = created_time + timedelta(
                minutes=self._rng.randint(5, 60 * 24 * 7)
            )
            accessed_time = modified_time + timedelta(
                minutes=self._rng.randint(0, 60 * 24)
            )
            
            # Construct relative path
            relative_path = f"{seed.subfolder}/{filename}"
            
            documents.append(ExpandedDocument(
                filename=filename,
                relative_path=relative_path,
                document_type=seed.document_type,
                category=seed.category,
                content=content_bytes,
                created_time=created_time,
                modified_time=modified_time,
                accessed_time=accessed_time,
                context=seed.content_theme,
            ))
        
        return documents
    
    def _prepare_content(
        self,
        content_text: str,
        document_type: str,
    ) -> bytes:
        """Prepare content bytes with appropriate file header."""
        header = _FILE_HEADERS.get(document_type.lower(), b"")
        
        if document_type.lower() in ("txt", "md", "csv"):
            # Plain text formats
            return content_text.encode("utf-8")
        elif document_type.lower() == "json":
            # JSON needs proper structure
            return (header + content_text.encode("utf-8") + b"\n}").replace(b"{\n{\n", b"{\n")
        elif document_type.lower() in ("docx", "xlsx", "pdf"):
            # Binary formats - use header + padding + encoded text as pseudo-content
            text_bytes = content_text.encode("utf-8")
            # Pad to realistic size
            target_size = self._rng.randint(4096, 65536)
            padding = b"\x00" * max(0, target_size - len(header) - len(text_bytes))
            return header + text_bytes[:1000] + padding
        elif document_type.lower() == "rtf":
            # RTF needs closing brace
            return header + content_text.encode("utf-8") + b"}"
        else:
            return content_text.encode("utf-8")
    
    def _generate_themed_content(
        self,
        theme: str,
        document_type: str,
        variables: Dict[str, List[str]],
    ) -> str:
        """Generate content based on theme when no template provided."""
        return self._content_variator.generate_document_content(
            content_theme=theme,
            document_type=document_type,
            variables=variables,
        )
    
    def _generate_document_time(
        self,
        persona: PersonaContext,
        category: FileCategory,
    ) -> datetime:
        """Generate a realistic document creation timestamp."""
        days_ago = self._rng.randint(0, self._timeline_days)
        date = self._base_date - timedelta(days=days_ago)
        
        day_of_week = date.isoweekday()
        is_work_day = day_of_week in persona.active_days
        is_work_doc = category in (FileCategory.WORK, FileCategory.FINANCIAL, FileCategory.TECHNICAL)
        
        if is_work_day and is_work_doc:
            # Work document during work hours
            hour = self._rng.randint(persona.work_hours_start, persona.work_hours_end - 1)
        else:
            # Personal document or off-hours
            hour = self._rng.choice([7, 8, 9, 19, 20, 21, 22, 23])
        
        minute = self._rng.randint(0, 59)
        second = self._rng.randint(0, 59)
        
        return date.replace(hour=hour, minute=minute, second=second, microsecond=0)
    
    def create_filesystem_documents(
        self,
        documents: List[ExpandedDocument],
        user_dir: Path,
    ) -> int:
        """Create actual document files in the filesystem.
        
        Args:
            documents: List of ExpandedDocument instances.
            user_dir: Path to the user directory (e.g., Users/john.doe/).
        
        Returns:
            Number of files created.
        """
        created = 0
        
        for doc in documents:
            file_path = user_dir / doc.relative_path
            
            # Ensure parent directory exists
            file_path.parent.mkdir(parents=True, exist_ok=True)
            
            # Avoid overwriting
            if file_path.exists():
                continue
            
            try:
                file_path.write_bytes(doc.content)
                created += 1
            except OSError as e:
                logger.warning("Failed to create document %s: %s", file_path, e)
        
        logger.info("Created %d documents under %s", created, user_dir)
        return created
