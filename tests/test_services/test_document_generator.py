"""Tests for DocumentGenerator — A14 openable-doc validation."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from services.filesystem.document_generator import DocumentGenerator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_svc(mount_manager, timestamp_service, audit_logger):
    return DocumentGenerator(
        mount_manager=mount_manager,
        timestamp_service=timestamp_service,
        audit_logger=audit_logger,
    )


def _all_docs(mount_dir: Path):
    user_root = mount_dir / "Users" / "TestUser"
    if not user_root.exists():
        return []
    return list(user_root.rglob("*.*"))


# ---------------------------------------------------------------------------
# Structural tests
# ---------------------------------------------------------------------------

class TestDocumentGeneratorBasic:

    @pytest.fixture(autouse=True)
    def _inject(self, service_ctx):
        self.ctx = service_ctx

    def test_documents_folder_created(self, mount_manager, timestamp_service, audit_logger):
        svc = _make_svc(mount_manager, timestamp_service, audit_logger)
        svc.apply(self.ctx)
        docs = mount_manager.root / "Users" / "TestUser" / "Documents"
        assert docs.is_dir()

    def test_at_least_ten_files_created(self, mount_manager, timestamp_service, audit_logger):
        svc = _make_svc(mount_manager, timestamp_service, audit_logger)
        svc.apply(self.ctx)
        assert len(_all_docs(mount_manager.root)) >= 10

    def test_audit_logged(self, mount_manager, timestamp_service, audit_logger):
        svc = _make_svc(mount_manager, timestamp_service, audit_logger)
        svc.apply(self.ctx)
        svcs = {e["service"] for e in audit_logger.entries}
        assert "DocumentGenerator" in svcs

    def test_expected_extensions(self, mount_manager, timestamp_service, audit_logger):
        svc = _make_svc(mount_manager, timestamp_service, audit_logger)
        svc.apply(self.ctx)
        exts = {f.suffix.lower() for f in _all_docs(mount_manager.root)}
        assert exts & {".docx", ".xlsx", ".pdf", ".txt", ".md", ".py"}


# ---------------------------------------------------------------------------
# Content validity (A14 — files openable in Word/Excel/Adobe)
# ---------------------------------------------------------------------------

class TestDocumentContent:

    @pytest.fixture(autouse=True)
    def _inject(self, service_ctx):
        self.ctx = service_ctx

    def test_docx_files_are_valid_zip(self, mount_manager, timestamp_service, audit_logger):
        svc = _make_svc(mount_manager, timestamp_service, audit_logger)
        svc.apply(self.ctx)
        docx_files = [f for f in _all_docs(mount_manager.root) if f.suffix.lower() == ".docx"]
        assert docx_files, "no .docx files found"
        for path in docx_files[:10]:
            data = path.read_bytes()
            assert len(data) >= 4, f"{path.name} is empty"
            assert zipfile.is_zipfile(io.BytesIO(data)), f"{path.name} is not a valid ZIP"

    def test_xlsx_files_are_valid_zip(self, mount_manager, timestamp_service, audit_logger):
        svc = _make_svc(mount_manager, timestamp_service, audit_logger)
        svc.apply(self.ctx)
        xlsx_files = [f for f in _all_docs(mount_manager.root) if f.suffix.lower() == ".xlsx"]
        assert xlsx_files, "no .xlsx files found"
        for path in xlsx_files[:10]:
            data = path.read_bytes()
            assert len(data) >= 4, f"{path.name} is empty"
            assert zipfile.is_zipfile(io.BytesIO(data)), f"{path.name} is not a valid ZIP"

    def test_pdf_files_have_pdf_magic(self, mount_manager, timestamp_service, audit_logger):
        svc = _make_svc(mount_manager, timestamp_service, audit_logger)
        svc.apply(self.ctx)
        pdf_files = [f for f in _all_docs(mount_manager.root) if f.suffix.lower() == ".pdf"]
        assert pdf_files, "no .pdf files found"
        for path in pdf_files[:10]:
            data = path.read_bytes()
            assert data.startswith(b"%PDF-"), f"{path.name} missing PDF magic"

    def test_docx_contains_document_xml(self, mount_manager, timestamp_service, audit_logger):
        svc = _make_svc(mount_manager, timestamp_service, audit_logger)
        svc.apply(self.ctx)
        docx_files = [f for f in _all_docs(mount_manager.root) if f.suffix.lower() == ".docx"]
        for path in docx_files[:5]:
            with zipfile.ZipFile(io.BytesIO(path.read_bytes())) as zf:
                names = zf.namelist()
                assert any("document.xml" in n for n in names), \
                    f"{path.name} missing word/document.xml"

    def test_xlsx_contains_workbook_xml(self, mount_manager, timestamp_service, audit_logger):
        svc = _make_svc(mount_manager, timestamp_service, audit_logger)
        svc.apply(self.ctx)
        xlsx_files = [f for f in _all_docs(mount_manager.root) if f.suffix.lower() == ".xlsx"]
        for path in xlsx_files[:5]:
            with zipfile.ZipFile(io.BytesIO(path.read_bytes())) as zf:
                names = zf.namelist()
                assert any("workbook.xml" in n for n in names), \
                    f"{path.name} missing xl/workbook.xml"


# ---------------------------------------------------------------------------
# Scaled standalone path (fallback without expansion bundle) — A14
# ---------------------------------------------------------------------------

class TestScaledDocuments:
    """Verify >=500 openable files when no expansion bundle present."""

    def test_scaled_path_produces_500_plus_files(
        self, mount_manager, timestamp_service, audit_logger, service_ctx
    ):
        assert service_ctx.expansion is None
        svc = _make_svc(mount_manager, timestamp_service, audit_logger)
        svc.apply(service_ctx)
        # A14: >=500 documents openable by some application (txt/md/py readable,
        # docx/xlsx/pdf openable in Word/Excel/Adobe). Zone.Identifier sidecars excluded.
        docs = [f for f in _all_docs(mount_manager.root)
                if f.suffix.lower() in (".docx", ".xlsx", ".pdf", ".txt", ".md", ".py", ".json")
                and "Zone.Identifier" not in f.name]
        assert len(docs) >= 500, f"A14 fail: only {len(docs)} openable docs"

    def test_scaled_docs_are_all_valid(
        self, mount_manager, timestamp_service, audit_logger, service_ctx
    ):
        svc = _make_svc(mount_manager, timestamp_service, audit_logger)
        svc.apply(service_ctx)
        failures = []
        docs = [f for f in _all_docs(mount_manager.root)
                if f.suffix.lower() in (".docx", ".xlsx")]
        for path in docs[:100]:
            data = path.read_bytes()
            if not zipfile.is_zipfile(io.BytesIO(data)):
                failures.append(path.name)
        assert not failures, f"Invalid ZIP files: {failures[:5]}"
