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

import logging
import os
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
        {"name": "Annual_Review_Presentation.pptx", "type": "txt", "content": "notes"},
        {"name": "Team_Meeting_Agenda.docx", "type": "docx", "size": (8192, 32768)},
        {"name": "Client_Proposal_Draft.docx", "type": "docx", "size": (16384, 65536)},
        {"name": "Expense_Report_March.xlsx", "type": "xlsx", "size": (4096, 16384)},
        {"name": "Sales_Dashboard.xlsx", "type": "xlsx", "size": (4096, 16384)},
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
        {"name": "API_Documentation.docx", "type": "docx", "size": (16384, 65536)},
        {"name": "Sprint_Tracking.xlsx", "type": "xlsx", "size": (4096, 16384)},
        {"name": "Architecture_Design.docx", "type": "docx", "size": (16384, 65536)},
        {"name": "Test_Results_Q1.xlsx", "type": "xlsx", "size": (4096, 16384)},
    ],
    "home_user": [
        {"name": "Shopping_List.txt", "type": "txt", "content": "shopping"},
        {"name": "Recipe_Collection.docx", "type": "docx", "size": (8192, 32768)},
        {"name": "Vacation_Plan.docx", "type": "docx", "size": (4096, 16384)},
        {"name": "Budget.xlsx", "type": "xlsx", "size": (2048, 8192)},
        {"name": "addresses.txt", "type": "txt", "content": "addresses"},
        {"name": "notes.txt", "type": "txt", "content": "notes"},
        {"name": "Photo_Album_2024.pdf", "type": "pdf", "size": (262144, 1048576)},
        {"name": "Household_Budget.xlsx", "type": "xlsx", "size": (4096, 16384)},
        {"name": "Vacation_Itinerary.docx", "type": "docx", "size": (8192, 32768)},
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

    def apply(self, ctx: "ServiceContext") -> None:
        """Generate documents for the user profile.

        Primary path: writes all ``DocumentDescriptor`` entries from
        ``ctx.expansion.documents`` (populated by ExpansionOrchestrator —
        typically 3,000-5,000 files at 360-day scale).

        Fallback: if no expansion bundle is present, writes the small
        hardcoded profile-specific document list (~10-15 files).

        Raises:
            DocumentGeneratorError: If document creation fails.
        """
        username = ctx.identity_bundle.user.username
        profile_type = ctx.persona.profile_archetype
        seed = ctx.identity_bundle.user.computer_name

        rng = (ctx.scheduler.child_rng("DocumentGenerator")
               if ctx.scheduler else Random(hash(seed + profile_type)))
        from core.time_utils import sched_now as _sched_now
        sched_now = _sched_now(ctx)
        install_time = ctx.install_time
        created_files = 0

        try:
            if ctx.expansion is not None and ctx.expansion.documents:
                created_files = self._write_expansion_documents(
                    ctx.expansion.documents, username, rng, sched_now, install_time
                )
            else:
                # Scaled standalone path: generate documents at timeline density.
                # Target ≥500 openable files (A14) without the expansion bundle.
                target = max(500, int(ctx.persona.timeline_days * 1.5))
                created_files = self._write_scaled_documents(
                    profile_type, username, rng, sched_now, install_time,
                    ctx.persona.timeline_days, target
                )

            self._audit.log({
                "service": self.service_name,
                "operation": "generate_documents",
                "username": username,
                "profile_type": profile_type,
                "files_created": created_files,
            })
            logger.info(
                "Generated %d documents for user '%s' (%s profile)",
                created_files, username, profile_type,
            )

        except Exception as exc:
            raise DocumentGeneratorError(
                f"Document generation failed: {exc}"
            ) from exc

    def _write_expansion_documents(
        self,
        descriptors: list,
        username: str,
        rng: Random,
        now: datetime | None = None,
        install_time: datetime | None = None,
    ) -> int:
        """Write document files from ExpansionBundle descriptors.

        Each descriptor carries ``timestamp_offset_days`` — days since
        ``install_time`` when the file was "created" in the persona timeline.
        This drives both the POSIX mtime (set via os.utime) and, on a FUSE-
        mounted NTFS volume, the $STANDARD_INFORMATION timestamps patched
        later by MftTimestampPatcher.

        Args:
            descriptors:  List of DocumentDescriptor from the expansion bundle.
            username:     Windows username (for path substitution).
            rng:          RNG for content generation.
            now:          Scheduler's "now" datetime.
            install_time: OS install datetime; used to compute absolute
                          timestamps from descriptor offset_days.

        Returns:
            Number of files written.
        """
        from datetime import timedelta as _td

        written = 0
        for desc in descriptors:
            rel_path = Path(
                str(desc.relative_path).replace("{username}", username)
            )
            ext = rel_path.suffix.lower()

            if desc.content:
                raw: bytes = desc.content if isinstance(desc.content, bytes) else desc.content.encode("utf-8")
            elif ext in (".docx",):
                raw = self._generate_docx_stub(rng, (4096, 32768))
            elif ext in (".xlsx",):
                raw = self._generate_xlsx_stub(rng, (4096, 16384))
            elif ext in (".pdf",):
                raw = self._generate_pdf_stub(rng, (8192, 65536))
            else:
                raw = self._generate_text_content("notes", rng, now).encode("utf-8")

            # Determine per-descriptor timestamp
            if install_time is not None and desc.timestamp_offset_days >= 0.0:
                ts = install_time + _td(days=desc.timestamp_offset_days)
                self._write_file(rel_path, raw, event_type=desc.event_type, override_ts=ts)
            else:
                self._write_file(rel_path, raw, event_type=desc.event_type)
            written += 1

        return written

    def _write_hardcoded_documents(
        self,
        profile_type: str,
        username: str,
        rng: Random,
        now: datetime | None = None,
    ) -> int:
        """Write the small hardcoded document list for this profile type."""
        docs_dir = Path("Users") / username / "Documents"
        documents = _PROFILE_DOCUMENTS.get(profile_type, [])
        created = 0
        generated_pdf = False
        available_pdf_count = sum(1 for d in documents if d.get("type") == "pdf")

        for doc_spec in documents:
            doc_type = doc_spec["type"]
            if rng.random() < 0.1:
                if not (doc_type == "pdf" and available_pdf_count > 0 and not generated_pdf):
                    continue

            file_path = docs_dir / doc_spec["name"]
            if doc_type in ("txt", "json"):
                content = self._generate_text_content(doc_spec.get("content", "notes"), rng, now)
                self._write_file(file_path, content.encode("utf-8"))
            elif doc_type == "docx":
                self._write_file(file_path, self._generate_docx_stub(rng, doc_spec.get("size", (4096, 16384))))
            elif doc_type == "xlsx":
                self._write_file(file_path, self._generate_xlsx_stub(rng, doc_spec.get("size", (4096, 16384))))
            elif doc_type == "pdf":
                self._write_file(file_path, self._generate_pdf_stub(rng, doc_spec.get("size", (8192, 32768))))
                generated_pdf = True
            created += 1

        if available_pdf_count > 0 and not generated_pdf:
            for doc_spec in documents:
                if doc_spec.get("type") != "pdf":
                    continue
                file_path = docs_dir / doc_spec["name"]
                self._write_file(
                    file_path,
                    self._generate_pdf_stub(rng, doc_spec.get("size", (8192, 32768))),
                )
                created += 1
                break

        return created

    def _write_scaled_documents(
        self,
        profile_type: str,
        username: str,
        rng: Random,
        now: datetime | None,
        install_time: datetime | None,
        timeline_days: int,
        target: int,
    ) -> int:
        """Write *target* documents spread across standard Windows user folders.

        Uses the existing stub generators so every file has correct magic bytes
        and can be opened by real applications (A14).  Documents are distributed
        across Documents/, Downloads/, Desktop/, and Pictures/ to mimic natural
        folder usage patterns.
        """
        from datetime import timedelta

        base_dirs = [
            Path("Users") / username / "Documents",
            Path("Users") / username / "Downloads",
            Path("Users") / username / "Desktop",
            Path("Users") / username / "Documents" / "Projects",
            Path("Users") / username / "Documents" / "Reports",
            Path("Users") / username / "Documents" / "Archive",
        ]

        # File type mix per profile
        type_weights: list[tuple[str, int]] = {
            "office_user": [("docx", 40), ("xlsx", 30), ("pdf", 20), ("txt", 10)],
            "developer":   [("txt", 35), ("pdf", 25), ("docx", 20), ("xlsx", 20)],
            "home_user":   [("pdf", 35), ("docx", 30), ("xlsx", 20), ("txt", 15)],
        }.get(profile_type, [("docx", 30), ("xlsx", 25), ("pdf", 25), ("txt", 20)])

        # Name stems for realistic filenames
        stems: list[str] = {
            "office_user": [
                "Meeting_Notes", "Report", "Invoice", "Proposal", "Budget",
                "Presentation", "Contract", "Memo", "Summary", "Plan",
                "Analysis", "Review", "Update", "Draft", "Final",
                "Q1_Report", "Q2_Report", "Q3_Report", "Q4_Report",
            ],
            "developer": [
                "README", "CHANGELOG", "Design_Doc", "API_Spec", "Test_Plan",
                "Architecture", "Requirements", "Sprint_Notes", "Bug_Report",
                "Release_Notes", "Config", "Setup_Guide", "Migration",
            ],
            "home_user": [
                "Shopping_List", "Recipe", "Budget", "Photo_Log", "Travel",
                "Notes", "Journal", "Plan", "Wishlist", "Contacts",
                "Warranty", "Manual", "Receipt", "Certificate",
            ],
        }.get(profile_type, ["Document", "File", "Notes", "Report", "Data"])

        # Build weighted file type population
        file_types: list[str] = []
        for ft, w in type_weights:
            file_types.extend([ft] * w)

        created = 0
        if install_time and now:
            span_seconds = max(1, int((now - install_time).total_seconds()))
        else:
            span_seconds = timeline_days * 86400

        # Write the hardcoded profile docs first (always)
        created += self._write_hardcoded_documents(profile_type, username, rng, now)

        # Generate scaled documents until target is met
        used_names: set = set()
        while created < target:
            stem = rng.choice(stems)
            n = created + 1
            dir_path = rng.choice(base_dirs)
            ft = rng.choice(file_types)

            # Build a unique filename
            candidate = f"{stem}_{n}.{ft}"
            while candidate in used_names:
                candidate = f"{stem}_{n}_{rng.randint(1000, 9999)}.{ft}"
            used_names.add(candidate)
            file_path = dir_path / candidate

            # Generate the file body with realistic size ranges
            if ft == "docx":
                body = self._generate_docx_stub(rng, (8_192, 65_536))
            elif ft == "xlsx":
                body = self._generate_xlsx_stub(rng, (4_096, 32_768))
            elif ft == "pdf":
                body = self._generate_pdf_stub(rng, (16_384, 131_072))
            else:  # txt
                body = self._generate_text_content("notes", rng, now).encode("utf-8")

            # Spread timestamps across the full timeline
            offset_s = rng.randint(0, span_seconds)
            ts = (install_time or (now or datetime.now())) + timedelta(seconds=offset_s) \
                 if (install_time or now) else None

            self._write_file(file_path, body, override_ts=ts)
            created += 1

        return created

    def _write_file(
        self,
        rel_path: Path,
        content: bytes,
        event_type: str = "document_created",
        override_ts: Optional[datetime] = None,
    ) -> None:
        """Write file content to the mounted filesystem and apply timestamps.

        Args:
            rel_path:    Path relative to mount root.
            content:     Binary content to write.
            event_type:  Event type for timestamp generation (used when no override).
            override_ts: If given, set atime/mtime to this exact datetime instead
                         of sampling from TimestampService.
        """
        full_path = self._mount.resolve(str(rel_path))
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_bytes(content)

        if override_ts is not None:
            unix_ts = override_ts.timestamp()
            os.utime(str(full_path), (unix_ts, unix_ts))
        else:
            self._apply_timestamps(full_path, event_type)

        self._audit.log({
            "service": self.service_name,
            "operation": "create_file",
            "path": str(full_path),
            "size": len(content),
            "timestamp_event": event_type,
        })

    def _apply_timestamps(self, path: Path, event_type: str) -> None:
        """Apply created/modified/accessed timestamps from the timestamp service."""
        timestamps = self._ts.get_timestamp(event_type)
        accessed = timestamps["accessed"].timestamp()
        modified = timestamps["modified"].timestamp()
        os.utime(str(path), (accessed, modified))


    def _generate_text_content(
        self,
        template_key: str,
        rng: Random,
        now: datetime | None = None,
    ) -> str:
        """Generate text content from template."""
        template = _TEXT_TEMPLATES.get(template_key, "")

        # Fill in placeholders — gracefully handle templates that
        # contain literal curly braces (e.g. JSON config content).
        if now is None:
            raise ValueError("_generate_text_content: 'now' must be provided (use sched_now(ctx))")
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
        """Generate a structurally valid DOCX file (ZIP with OOXML content)."""
        import io
        import zipfile

        # Generate some paragraphs of lorem-like text
        sentences = [
            "The quarterly results exceeded expectations across all divisions.",
            "Please review the attached document before the meeting on Friday.",
            "Budget allocations for the next fiscal year have been finalized.",
            "The team has made significant progress on the project deliverables.",
            "All stakeholders should provide feedback by end of business today.",
            "The implementation timeline has been updated to reflect new requirements.",
            "Revenue growth remained steady at approximately four percent year over year.",
            "Customer satisfaction scores improved significantly in the latest survey.",
        ]
        body_paragraphs = ""
        num_paras = rng.randint(3, 12)
        for _ in range(num_paras):
            text = rng.choice(sentences)
            body_paragraphs += f'<w:p><w:r><w:t>{text}</w:t></w:r></w:p>'

        content_types = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            '</Types>'
        )
        rels = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
            '</Relationships>'
        )
        document = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            f'<w:body>{body_paragraphs}</w:body>'
            '</w:document>'
        )

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("[Content_Types].xml", content_types)
            zf.writestr("_rels/.rels", rels)
            zf.writestr("word/document.xml", document)
        return buf.getvalue()

    def _generate_xlsx_stub(
        self,
        rng: Random,
        size_range: Tuple[int, int],
    ) -> bytes:
        """Generate a structurally valid XLSX file (ZIP with OOXML content)."""
        import io
        import zipfile

        # Generate some spreadsheet rows
        rows = ""
        num_rows = rng.randint(5, 20)
        for r in range(1, num_rows + 1):
            val = rng.randint(100, 99999)
            rows += f'<row r="{r}"><c r="A{r}" t="n"><v>{val}</v></c><c r="B{r}" t="inlineStr"><is><t>Item {r}</t></is></c></row>'

        content_types = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            '</Types>'
        )
        rels = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            '</Relationships>'
        )
        workbook = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets>'
            '</workbook>'
        )
        wb_rels = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
            '</Relationships>'
        )
        sheet = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            f'<sheetData>{rows}</sheetData>'
            '</worksheet>'
        )

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("[Content_Types].xml", content_types)
            zf.writestr("_rels/.rels", rels)
            zf.writestr("xl/workbook.xml", workbook)
            zf.writestr("xl/_rels/workbook.xml.rels", wb_rels)
            zf.writestr("xl/worksheets/sheet1.xml", sheet)
        return buf.getvalue()

    def _generate_pdf_stub(
        self,
        rng: Random,
        size_range: Tuple[int, int],
    ) -> bytes:
        """Generate a structurally valid PDF file with correct xref."""
        # Build PDF objects with tracked offsets
        parts: list = []
        offsets: list = []

        parts.append(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")

        # Object 1: Catalog
        offsets.append(len(b"".join(parts)))
        parts.append(b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n")

        # Object 2: Pages
        offsets.append(len(b"".join(parts)))
        parts.append(b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n")

        # Object 3: Page
        offsets.append(len(b"".join(parts)))
        parts.append(b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>\nendobj\n")

        # xref
        xref_offset = len(b"".join(parts))
        xref = b"xref\n0 4\n"
        xref += b"0000000000 65535 f \n"
        for off in offsets:
            xref += f"{off:010d} 00000 n \n".encode()

        trailer = f"trailer\n<< /Root 1 0 R /Size 4 >>\nstartxref\n{xref_offset}\n%%EOF\n".encode()

        pdf_content = b"".join(parts) + xref + trailer

        # Pad to minimum target size if needed
        target_size = rng.randint(*size_range)
        if len(pdf_content) < target_size:
            # Add a padded comment stream before xref
            padding_needed = target_size - len(pdf_content)
            pad_comment = b"% " + b"x" * (padding_needed - 3) + b"\n"
            # Rebuild with padding before xref
            body = b"".join(parts) + pad_comment
            xref_offset = len(body)
            xref = b"xref\n0 4\n"
            xref += b"0000000000 65535 f \n"
            for off in offsets:
                xref += f"{off:010d} 00000 n \n".encode()
            trailer = f"trailer\n<< /Root 1 0 R /Size 4 >>\nstartxref\n{xref_offset}\n%%EOF\n".encode()
            pdf_content = body + xref + trailer

        return pdf_content

