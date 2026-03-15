"""Document file generator for realistic filesystem artifacts.

Creates placeholder document files (text, docx, xlsx, pdf) in the user's
Documents folder and other profile-specific locations. Files have minimal
but valid content/headers to appear as real documents during forensic analysis.

Profile-specific document themes:
- office_user: Reports, budgets, proposals, meeting notes
- developer: README files, specs, architecture docs
- home_user: Personal letters, recipes, lists
"""

from __future__ import annotations

import json
import logging
import struct
from datetime import datetime, timezone
from pathlib import Path
from random import Random
from typing import Any, Dict, List, Optional, Tuple

from services.base_service import BaseService

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Minimal valid file signatures / magic bytes
_DOCX_HEADER = bytes([
    0x50, 0x4B, 0x03, 0x04,  # ZIP/DOCX magic
    0x14, 0x00, 0x06, 0x00,  # Version needed
])

_XLSX_HEADER = bytes([
    0x50, 0x4B, 0x03, 0x04,  # ZIP/XLSX magic
    0x14, 0x00, 0x06, 0x00,
])

_PDF_HEADER = b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n"

_RTF_HEADER = b"{\\rtf1\\ansi\\deff0{\\fonttbl{\\f0 Times New Roman;}}"

# Profile document templates
_PROFILE_DOCUMENTS: Dict[str, List[Dict[str, Any]]] = {
    "office_user": [
        {"name": "Q4_Report_2024.docx", "type": "docx", "size": (8192, 32768)},
        {"name": "Budget_FY2025.xlsx", "type": "xlsx", "size": (4096, 16384)},
        {"name": "Meeting_Notes.txt", "type": "txt", "content": "meeting_notes"},
        {"name": "Project_Timeline.xlsx", "type": "xlsx", "size": (4096, 12288)},
        {"name": "Annual_Review.docx", "type": "docx", "size": (16384, 65536)},
        {"name": "Client_Proposal.docx", "type": "docx", "size": (32768, 131072)},
        {"name": "Expense_Report.xlsx", "type": "xlsx", "size": (2048, 8192)},
        {"name": "Policy_Document.pdf", "type": "pdf", "size": (65536, 262144)},
        {"name": "Training_Manual.pdf", "type": "pdf", "size": (131072, 524288)},
        {"name": "team_contacts.txt", "type": "txt", "content": "contacts"},
    ],
    "developer": [
        {"name": "README.md", "type": "txt", "content": "readme"},
        {"name": "ARCHITECTURE.md", "type": "txt", "content": "architecture"},
        {"name": "API_Spec.docx", "type": "docx", "size": (16384, 65536)},
        {"name": "Sprint_Notes.txt", "type": "txt", "content": "sprint_notes"},
        {"name": "requirements.txt", "type": "txt", "content": "requirements"},
        {"name": "config.json", "type": "json", "content": "config"},
        {"name": "test_report.pdf", "type": "pdf", "size": (4096, 16384)},
        {"name": "database_schema.docx", "type": "docx", "size": (8192, 32768)},
    ],
    "home_user": [
        {"name": "Shopping_List.txt", "type": "txt", "content": "shopping"},
        {"name": "Recipe_Collection.docx", "type": "docx", "size": (8192, 32768)},
        {"name": "Vacation_Plan.docx", "type": "docx", "size": (4096, 16384)},
        {"name": "Budget.xlsx", "type": "xlsx", "size": (2048, 8192)},
        {"name": "addresses.txt", "type": "txt", "content": "addresses"},
        {"name": "notes.txt", "type": "txt", "content": "notes"},
        {"name": "Photo_Album_2024.pdf", "type": "pdf", "size": (262144, 1048576)},
    ],
}

# Text content templates
_TEXT_TEMPLATES: Dict[str, str] = {
    "meeting_notes": """Meeting Notes - {date}
====================

Attendees: {names}

Agenda:
1. Project status update
2. Q4 planning
3. Resource allocation
4. Action items

Notes:
- Reviewed current sprint progress
- Discussed upcoming deadlines
- Identified blockers
- Scheduled follow-up meeting

Action Items:
- Complete documentation by EOW
- Review pull requests
- Schedule 1:1 meetings
""",
    "contacts": """Team Contacts
=============

John Smith - Project Manager
  Email: jsmith@company.com
  Phone: (555) 123-4567

Sarah Johnson - Lead Developer
  Email: sjohnson@company.com
  Phone: (555) 234-5678

Mike Wilson - QA Engineer
  Email: mwilson@company.com
  Phone: (555) 345-6789
""",
    "readme": """# Project Documentation

## Overview

This project implements the core functionality for the application.

## Getting Started

1. Install dependencies: `pip install -r requirements.txt`
2. Configure settings in `config.yaml`
3. Run: `python main.py`

## Structure

- `src/` - Source code
- `tests/` - Test files
- `docs/` - Documentation

## License

MIT License
""",
    "architecture": """# Architecture Documentation

## System Overview

The system follows a modular architecture with clear separation of concerns.

## Components

### Core Module
Handles business logic and data processing.

### API Layer
RESTful endpoints for external communication.

### Database Layer
PostgreSQL with connection pooling.

## Data Flow

1. Request received
2. Authentication verified
3. Business logic executed
4. Response returned
""",
    "sprint_notes": """Sprint {sprint_num} Notes
====================

Start: {start_date}
End: {end_date}

Completed:
- Feature A implementation
- Bug fixes for module B
- Performance optimization
- Code review sessions

In Progress:
- API documentation
- Integration tests
- UI improvements

Blockers:
- Waiting for design approval
- Third-party API access pending
""",
    "requirements": """# Python Dependencies
flask>=2.0.0
requests>=2.28.0
pydantic>=2.0.0
pytest>=7.0.0
black>=23.0.0
mypy>=1.0.0
sqlalchemy>=2.0.0
redis>=4.0.0
celery>=5.0.0
""",
    "config": """{
    "app_name": "MyApplication",
    "version": "1.0.0",
    "debug": false,
    "database": {
        "host": "localhost",
        "port": 5432,
        "name": "myapp_db"
    },
    "cache": {
        "enabled": true,
        "ttl": 3600
    }
}""",
    "shopping": """Shopping List
=============

Groceries:
- Milk
- Bread
- Eggs
- Cheese
- Vegetables
- Fruit

Household:
- Paper towels
- Dish soap
- Laundry detergent
""",
    "addresses": """Address Book
============

Mom & Dad
123 Family Lane
Hometown, ST 12345

Best Friend
456 Friend Street, Apt 2B
Somewhere, ST 23456
""",
    "notes": """Personal Notes
==============

Remember:
- Call dentist for appointment
- Renew car registration
- Birthday gift for Sarah
- Check on vacation dates

Ideas:
- Learn a new recipe
- Start exercise routine
- Organize photo collection
""",
}


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class DocumentGeneratorError(Exception):
    """Raised when document generation fails."""


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class DocumentGenerator(BaseService):
    """Creates placeholder document files with realistic content.

    Generates document files (txt, docx, xlsx, pdf) in the user's
    Documents folder matching the profile type.

    Args:
        mount_manager: Resolves paths against the mounted image root.
        timestamp_service: Provides timestamps for file operations.
        audit_logger: Structured audit logging.
    """

    def __init__(
        self,
        mount_manager,
        timestamp_service,
        audit_logger,
        data_dir: Optional[Path] = None,
    ) -> None:
        self._mount = mount_manager
        self._ts = timestamp_service
        self._audit = audit_logger
        self._data_dir = data_dir or Path("data")

    @property
    def service_name(self) -> str:
        return "DocumentGenerator"

    def apply(self, context: dict) -> None:
        """Generate documents for the user profile.

        Args:
            context: Runtime context dict. Recognised keys:

                * ``username`` (str) — Windows username.
                * ``profile_type`` (str) — ``home_user`` / ``office_user`` / ``developer``.
                * ``computer_name`` (str) — used as RNG seed.
                * ``timeline_days`` (int) — days of history to simulate.

        Raises:
            DocumentGeneratorError: If document creation fails.
        """
        username = context.get("username", "default_user")
        profile_type = context.get("profile_type", "home_user")
        seed = context.get("computer_name", username)
        timeline_days = context.get("timeline_days", 90)

        rng = Random(hash(seed + profile_type))
        docs_dir = Path("Users") / username / "Documents"
        created_files = []

        try:
            # Get documents for this profile
            documents = _PROFILE_DOCUMENTS.get(profile_type, [])

            for doc_spec in documents:
                # Randomly skip some documents for variety
                if rng.random() < 0.1:
                    continue

                file_path = docs_dir / doc_spec["name"]
                doc_type = doc_spec["type"]

                # Generate content based on type
                if doc_type == "txt":
                    content = self._generate_text_content(
                        doc_spec.get("content", "notes"),
                        rng,
                    )
                    self._write_file(file_path, content.encode("utf-8"))
                elif doc_type == "json":
                    content = self._generate_text_content(
                        doc_spec.get("content", "config"),
                        rng,
                    )
                    self._write_file(file_path, content.encode("utf-8"))
                elif doc_type == "docx":
                    size_range = doc_spec.get("size", (4096, 16384))
                    content = self._generate_docx_stub(rng, size_range)
                    self._write_file(file_path, content)
                elif doc_type == "xlsx":
                    size_range = doc_spec.get("size", (4096, 16384))
                    content = self._generate_xlsx_stub(rng, size_range)
                    self._write_file(file_path, content)
                elif doc_type == "pdf":
                    size_range = doc_spec.get("size", (8192, 32768))
                    content = self._generate_pdf_stub(rng, size_range)
                    self._write_file(file_path, content)

                created_files.append(str(file_path))

            self._audit.log({
                "service": self.service_name,
                "operation": "generate_documents",
                "username": username,
                "profile_type": profile_type,
                "files_created": len(created_files),
            })

            logger.info(
                "Generated %d documents for user '%s' (%s profile)",
                len(created_files), username, profile_type,
            )

        except Exception as exc:
            logger.error("Failed to generate documents: %s", exc)
            raise DocumentGeneratorError(
                f"Document generation failed: {exc}"
            ) from exc

    def _write_file(self, rel_path: Path, content: bytes) -> None:
        """Write file content to the mounted filesystem."""
        full_path = self._mount.resolve(str(rel_path))
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_bytes(content)

        self._audit.log({
            "service": self.service_name,
            "operation": "create_file",
            "path": str(full_path),
            "size": len(content),
        })

    def _generate_text_content(
        self,
        template_key: str,
        rng: Random,
    ) -> str:
        """Generate text content from template."""
        template = _TEXT_TEMPLATES.get(template_key, "")

        # Fill in placeholders — gracefully handle templates that
        # contain literal curly braces (e.g. JSON config content).
        now = datetime.now(timezone.utc)
        try:
            content = template.format(
                date=now.strftime("%B %d, %Y"),
                names="Team Members",
                sprint_num=rng.randint(10, 50),
                start_date=(now.replace(day=1)).strftime("%Y-%m-%d"),
                end_date=now.strftime("%Y-%m-%d"),
            )
        except (KeyError, IndexError, ValueError):
            # Template has unprocessable placeholders (e.g. JSON braces)
            content = template
        return content

    def _generate_docx_stub(
        self,
        rng: Random,
        size_range: Tuple[int, int],
    ) -> bytes:
        """Generate a minimal DOCX-like file."""
        target_size = rng.randint(*size_range)
        # DOCX is a ZIP file - generate valid ZIP header + padding
        content = _DOCX_HEADER + b"\x00" * (target_size - len(_DOCX_HEADER))
        return content

    def _generate_xlsx_stub(
        self,
        rng: Random,
        size_range: Tuple[int, int],
    ) -> bytes:
        """Generate a minimal XLSX-like file."""
        target_size = rng.randint(*size_range)
        content = _XLSX_HEADER + b"\x00" * (target_size - len(_XLSX_HEADER))
        return content

    def _generate_pdf_stub(
        self,
        rng: Random,
        size_range: Tuple[int, int],
    ) -> bytes:
        """Generate a minimal PDF-like file."""
        target_size = rng.randint(*size_range)
        # Basic PDF structure
        pdf_content = _PDF_HEADER
        pdf_content += b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
        pdf_content += b"2 0 obj\n<< /Type /Pages /Kids [] /Count 0 >>\nendobj\n"
        pdf_content += b"xref\n0 3\n"
        pdf_content += b"0000000000 65535 f \n"
        pdf_content += b"0000000015 00000 n \n"
        pdf_content += b"0000000068 00000 n \n"
        pdf_content += b"trailer\n<< /Root 1 0 R /Size 3 >>\n"
        pdf_content += b"startxref\n120\n%%EOF"

        # Pad to target size
        if len(pdf_content) < target_size:
            # Insert padding before %%EOF
            padding = b" " * (target_size - len(pdf_content))
            pdf_content = pdf_content[:-6] + padding + b"%%EOF\n"

        return pdf_content
