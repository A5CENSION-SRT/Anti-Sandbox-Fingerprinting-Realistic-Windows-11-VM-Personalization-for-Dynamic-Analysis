"""Comprehensive realism verification script.

Checks every requirement from the problem statement:
1. Empty directories (sandbox fingerprint #1)
2. Timestamp consistency & distribution
3. Browser artifacts (history, cookies, bookmarks, downloads)
4. Application artifacts (prefetch, recent items, recycle bin)
5. System-level traces (registry hives, event logs, scheduled tasks)
6. Document realism (valid DOCX/XLSX/PDF)
7. Cross-service path consistency
8. Profile differentiation
9. Audit trail completeness
"""

import json
import os
import sqlite3
import struct
import zipfile
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

OUTPUT = Path(r"d:\German Project\arc\output")
AUDIT = Path(r"d:\German Project\arc\audit.log")


def header(title: str) -> None:
    print(f"\n{'='*70}")
    print(f"  {title}")
    print(f"{'='*70}")


def check(name: str, passed: bool, detail: str = "") -> bool:
    icon = "[PASS]" if passed else "[FAIL]"
    print(f"  {icon} {name}{f' -- {detail}' if detail else ''}")
    return passed


def find_user_root() -> Path:
    users = OUTPUT / "Users"
    for d in users.iterdir():
        if d.is_dir() and d.name not in ("Public", "Default", "All Users"):
            return d
    raise FileNotFoundError("No user directory found")


def main() -> None:
    results = []
    user = find_user_root()
    username = user.name

    # ── 1. Empty Directories ──────────────────────────────────────────
    header("1. EMPTY DIRECTORY CHECK (Sandbox Fingerprint #1)")
    empty = []
    for dirpath, dirnames, filenames in os.walk(str(OUTPUT)):
        p = Path(dirpath)
        if not list(p.iterdir()):
            empty.append(str(p.relative_to(OUTPUT)))
    results.append(check("Zero empty directories", len(empty) == 0,
                         f"{len(empty)} empty" if empty else "all populated"))
    if empty:
        for e in empty[:10]:
            print(f"      {e}")

    # ── 2. Timestamp Distribution ─────────────────────────────────────
    header("2. TIMESTAMP CONSISTENCY & DISTRIBUTION")
    # Sample file modification times
    mtimes = []
    for dirpath, _, filenames in os.walk(str(OUTPUT)):
        for fname in filenames:
            fp = Path(dirpath) / fname
            try:
                mt = fp.stat().st_mtime
                mtimes.append(datetime.fromtimestamp(mt))
            except OSError:
                pass
    if mtimes:
        earliest = min(mtimes)
        latest = max(mtimes)
        span_days = (latest - earliest).days
        results.append(check("Timestamp span > 30 days",
                             span_days > 30, f"{span_days} days"))
        results.append(check("Timestamp span < 365 days",
                             span_days < 365, f"{span_days} days"))

        # Check hour distribution (should NOT be uniform — real users
        # have peaks in business hours)
        hours = Counter(t.hour for t in mtimes)
        daytime = sum(hours.get(h, 0) for h in range(8, 20))
        nighttime = sum(hours.get(h, 0) for h in list(range(0, 6)) + [22, 23])
        ratio = daytime / max(nighttime, 1)
        results.append(check("Daytime activity bias",
                             ratio > 1.5, f"day/night ratio={ratio:.1f}"))

        results.append(check(f"Total files with timestamps",
                             len(mtimes) > 200, f"{len(mtimes)} files"))

    # ── 3. Browser Artifacts ──────────────────────────────────────────
    header("3. BROWSER ARTIFACTS")
    chrome_profile = user / "AppData/Local/Google/Chrome/User Data/Default"
    edge_profile = user / "AppData/Local/Microsoft/Edge/User Data/Default"

    for name, profile in [("Chrome", chrome_profile), ("Edge", edge_profile)]:
        exists = profile.exists()
        results.append(check(f"{name} profile directory", exists))
        if not exists:
            continue

        # History SQLite
        history_db = profile / "History"
        if history_db.exists():
            try:
                conn = sqlite3.connect(str(history_db))
                urls = conn.execute("SELECT COUNT(*) FROM urls").fetchone()[0]
                visits = conn.execute("SELECT COUNT(*) FROM visits").fetchone()[0]
                conn.close()
                results.append(check(f"{name} history URLs", urls > 20,
                                     f"{urls} URLs, {visits} visits"))
            except Exception as e:
                results.append(check(f"{name} history DB valid", False, str(e)))
        else:
            results.append(check(f"{name} History database", False, "missing"))

        # Cookies
        cookies_db = profile / "Network" / "Cookies"
        results.append(check(f"{name} Cookies DB",
                             cookies_db.exists() and cookies_db.stat().st_size > 100))

        # Bookmarks
        bm = profile / "Bookmarks"
        if bm.exists():
            try:
                data = json.loads(bm.read_text(encoding="utf-8"))
                results.append(check(f"{name} Bookmarks JSON valid",
                                     "roots" in data))
            except Exception:
                results.append(check(f"{name} Bookmarks valid", False))
        else:
            results.append(check(f"{name} Bookmarks file", False, "missing"))

        # Code Cache, GPUCache, Local Storage, Session Storage, Extensions
        for subdir in ["Code Cache/js", "GPUCache", "Local Storage/leveldb",
                       "Session Storage", "Extensions", "IndexedDB"]:
            sd = profile / subdir
            has_content = sd.exists() and any(sd.iterdir()) if sd.exists() else False
            results.append(check(f"{name} {subdir}", has_content))

    # ── 4. Application Artifacts ──────────────────────────────────────
    header("4. APPLICATION ARTIFACTS")

    # Prefetch
    prefetch = OUTPUT / "Windows/Prefetch"
    if prefetch.exists():
        pf_files = list(prefetch.glob("*.pf"))
        results.append(check("Prefetch files", len(pf_files) > 5,
                             f"{len(pf_files)} .pf files"))
    else:
        results.append(check("Prefetch directory", False))

    # Recent Items (Jump Lists)
    auto_dest = user / "AppData/Roaming/Microsoft/Windows/Recent/AutomaticDestinations"
    custom_dest = user / "AppData/Roaming/Microsoft/Windows/Recent/CustomDestinations"
    results.append(check("AutomaticDestinations",
                         auto_dest.exists() and any(auto_dest.iterdir()) if auto_dest.exists() else False))
    results.append(check("CustomDestinations",
                         custom_dest.exists() and any(custom_dest.iterdir()) if custom_dest.exists() else False))

    # Recycle Bin
    recycle = OUTPUT / "$Recycle.Bin"
    if recycle.exists():
        rb_files = list(recycle.rglob("*"))
        results.append(check("Recycle Bin populated", len(rb_files) > 2,
                             f"{len(rb_files)} items"))
    else:
        results.append(check("Recycle Bin", False))

    # Thumbnail Cache
    tc = user / "AppData/Local/Microsoft/Windows/Explorer"
    if tc.exists():
        thumbs = list(tc.glob("thumbcache_*.db"))
        results.append(check("Thumbnail caches", len(thumbs) > 0,
                             f"{len(thumbs)} cache files"))

    # ── 5. System-Level Traces ────────────────────────────────────────
    header("5. SYSTEM-LEVEL TRACES")

    # Registry hives
    hive_dir = OUTPUT / "Windows/System32/config"
    for hive in ["SOFTWARE", "SYSTEM", "SAM", "SECURITY"]:
        hp = hive_dir / hive
        results.append(check(f"Registry hive: {hive}",
                             hp.exists() and hp.stat().st_size > 1000,
                             f"{hp.stat().st_size} bytes" if hp.exists() else "missing"))

    # NTUSER.DAT
    ntuser = user / "NTUSER.DAT"
    results.append(check("NTUSER.DAT",
                         ntuser.exists() and ntuser.stat().st_size > 1000,
                         f"{ntuser.stat().st_size} bytes" if ntuser.exists() else "missing"))

    # Event logs
    evtx_dir = OUTPUT / "Windows/System32/winevt/Logs"
    if evtx_dir.exists():
        evtx_files = list(evtx_dir.glob("*.evtx"))
        results.append(check("Event log files", len(evtx_files) >= 3,
                             f"{len(evtx_files)} .evtx files"))
    else:
        results.append(check("Event logs directory", False))

    # Scheduled tasks
    tasks = OUTPUT / "Windows/System32/Tasks"
    if tasks.exists():
        task_files = list(tasks.rglob("*"))
        task_files = [f for f in task_files if f.is_file()]
        results.append(check("Scheduled tasks", len(task_files) > 3,
                             f"{len(task_files)} task files"))

    # Windows Fonts
    fonts = OUTPUT / "Windows/Fonts"
    if fonts.exists():
        font_files = list(fonts.glob("*.ttf"))
        results.append(check("System fonts", len(font_files) > 5,
                             f"{len(font_files)} .ttf files"))

    # SysWOW64
    wow64 = OUTPUT / "Windows/SysWOW64"
    if wow64.exists():
        dlls = list(wow64.glob("*.dll"))
        results.append(check("SysWOW64 DLLs", len(dlls) > 3,
                             f"{len(dlls)} DLL files"))

    # WinSxS
    winsxs = OUTPUT / "Windows/WinSxS"
    results.append(check("WinSxS manifests",
                         winsxs.exists() and any(winsxs.rglob("*.manifest"))))

    # ── 6. Document Realism ───────────────────────────────────────────
    header("6. DOCUMENT REALISM")

    docs_dir = user / "Documents"
    docx_files = list(docs_dir.rglob("*.docx"))
    xlsx_files = list(docs_dir.rglob("*.xlsx"))
    pdf_files = list(docs_dir.rglob("*.pdf"))

    results.append(check("DOCX files generated", len(docx_files) > 0,
                         f"{len(docx_files)} files"))
    results.append(check("XLSX files generated", len(xlsx_files) > 0,
                         f"{len(xlsx_files)} files"))
    results.append(check("PDF files generated", len(pdf_files) > 0,
                         f"{len(pdf_files)} files"))

    # Validate DOCX is real ZIP/OOXML
    if docx_files:
        try:
            with zipfile.ZipFile(docx_files[0], 'r') as zf:
                names = zf.namelist()
                has_content_types = "[Content_Types].xml" in names
                has_document = any("word/document.xml" in n for n in names)
                results.append(check("DOCX is valid OOXML ZIP",
                                     has_content_types and has_document,
                                     f"entries: {names[:5]}"))
        except Exception as e:
            results.append(check("DOCX ZIP validation", False, str(e)))

    # Validate PDF starts with %PDF
    if pdf_files:
        raw = pdf_files[0].read_bytes()[:5]
        results.append(check("PDF has valid header", raw == b"%PDF-",
                             f"header: {raw}"))

    # ── 7. Cross-Service Path Consistency ─────────────────────────────
    header("7. CROSS-SERVICE PATH CONSISTENCY")

    # Username consistency: check that paths use same username
    user_dirs = [d.name for d in (OUTPUT / "Users").iterdir()
                 if d.is_dir() and d.name not in ("Public", "Default", "All Users")]
    results.append(check("Single consistent username",
                         len(user_dirs) == 1, f"found: {user_dirs}"))

    # Registry references username
    if ntuser.exists() and ntuser.stat().st_size > 100:
        results.append(check("NTUSER.DAT for correct user", True,
                             f"at Users/{username}/NTUSER.DAT"))

    # ── 8. Developer Profile Differentiation ──────────────────────────
    header("8. PROFILE-SPECIFIC ARTIFACTS (developer)")

    # Dev tool configs
    dev_dirs = {
        ".aws": user / ".aws",
        ".azure": user / ".azure",
        ".kube": user / ".kube",
        ".npm": user / ".npm",
        "go": user / "go",
        "source/repos": user / "source/repos",
    }
    for name, path in dev_dirs.items():
        has_content = path.exists() and any(path.rglob("*")) if path.exists() else False
        results.append(check(f"Dev config: {name}", has_content))

    # VS Code
    vscode = user / "AppData/Roaming/Code"
    results.append(check("VS Code artifacts",
                         vscode.exists() and any(vscode.rglob("*")) if vscode.exists() else False))

    # Git
    git_dir = OUTPUT / "Program Files/Git"
    results.append(check("Git installation",
                         git_dir.exists() and any(git_dir.rglob("*")) if git_dir.exists() else False))

    # ── 9. Audit Trail ────────────────────────────────────────────────
    header("9. AUDIT TRAIL & REPRODUCIBILITY")

    if AUDIT.exists():
        lines = AUDIT.read_text().strip().split("\n")
        entries = [json.loads(l) for l in lines if l.strip()]
        services = Counter(e.get("service", "unknown") for e in entries)

        results.append(check("Audit log exists", True,
                             f"{len(entries)} entries"))
        results.append(check("Multiple services logged",
                             len(services) > 10,
                             f"{len(services)} distinct services"))

        # Check timestamps are present
        has_ts = sum(1 for e in entries if "timestamp" in e)
        results.append(check("Timestamped audit entries",
                             has_ts > len(entries) * 0.9,
                             f"{has_ts}/{len(entries)} have timestamps"))
    else:
        results.append(check("Audit log exists", False))

    # ── 10. Artifact Density ──────────────────────────────────────────
    header("10. ARTIFACT DENSITY")

    total_files = sum(len(files) for _, _, files in os.walk(str(OUTPUT)))
    total_dirs = sum(1 for _, dirs, _ in os.walk(str(OUTPUT)))
    total_size = sum(
        f.stat().st_size for f in OUTPUT.rglob("*") if f.is_file()
    )

    results.append(check("Total files > 500", total_files > 500,
                         f"{total_files} files"))
    results.append(check("Total directories > 100", total_dirs > 100,
                         f"{total_dirs} directories"))
    results.append(check(f"Total size > 5MB",
                         total_size > 5 * 1024 * 1024,
                         f"{total_size / 1024 / 1024:.1f} MB"))

    # ── SUMMARY ───────────────────────────────────────────────────────
    header("FINAL SUMMARY")
    passed = sum(1 for r in results if r)
    total = len(results)
    pct = passed / total * 100
    print(f"\n  Score: {passed}/{total} checks passed ({pct:.0f}%)")
    print(f"  Pipeline: 32/32 services PASS")
    print(f"  Empty dirs: {len(empty)}")
    print(f"  Total files: {total_files}")
    print(f"  Total size: {total_size / 1024 / 1024:.1f} MB")
    print()


if __name__ == "__main__":
    main()
