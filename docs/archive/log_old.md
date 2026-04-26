# ARC - Artifact Reality Composer: Project Log

This document summarizes the current state of the Anti-Sandbox Personalization Engine, instructions for execution, and final realism analysis.

## 🚀 Getting Started

### 1. Prerequisites
Ensure you have Python 3.10+ installed. Install the required dependencies:
```powershell
pip install -r requirements.txt
```

### 2. Running the Pipeline
To personalize the "mounted" image (defaulting to the `./output` directory):
```powershell
# Run with a specific profile (developer, office_user, home_user)
python main.py --profile developer
```

### 3. Verification & Testing
To run the realism suite and sanity checks:
```powershell
# Run the realism verification script
python verify_realism.py
```

---

## 🛠️ Implementation Summary

### Core Engine
- **Orchestrator**: Manages execution phases and context propagation.
- **Profile Engine**: YAML-driven configuration with inheritance.
- **Identity Generator**: Creates consistent user/machine identities.
- **Timestamp Service**: Generates realistic timelines for artifact creation times.

### Key Artifact Services (Implemented & Verified)
- **Filesystem**: `DocumentGenerator` (valid OOXML), `RecycleBinService`, `ThumbnailCacheService`, `SystemContentPopulator` (>500 files).
- **Registry**: `HiveWriter` for SOFTWARE, SYSTEM, AM, SECURITY, and NTUSER.DAT.
- **Browser**: Profile directories, Cookies, Bookmarks, and Cache stubs for Chrome and Edge.
- **Windows Features**: Event Logs (evtx), Scheduled Tasks, Prefetch, and Shell Folders.
- **Personalization**: Wallpaper, Desktop icons, and Application-specific traces (VS Code, Office).

---

## 📊 Realism Status (Developer Profile)

| Metric | Status | Result |
| :--- | :--- | :--- |
| **Pipeline Stability** | [PASS] | 32/32 services executed successfully |
| **Realism Score** | [PASS] | **56 / 60 checks passed (93%)** |
| **Empty Directories** | [PASS] | 0 empty directories found (Fingerprints eliminated) |
| **Artifact Density** | [PASS] | 504 files generated |
| **History Logic** | [FAIL] | 0 URLs/Visits in history SQLite (Logic exists, data path issue) |
| **Document Quality** | [PASS] | DOCX is valid OOXML ZIP |

### Failure Analysis
1.  **Browser History URLs**: The `UrlLoader` is searching for browsing seed data in `data/wordlists/` while the file resides in `data/`.
2.  **PDF Generation**: The `DocumentGenerator` currently skips PDF generation in the `developer` profile loop.
3.  **Active Day Bias**: Current ratio is **1.3**; the target for realistic user behavior is **> 1.5** (higher activity during work hours).

---

## 📅 Remaining Work
- [ ] **Fix UrlLoader**: Correct path for `urls_by_category.json`.
- [ ] **Implement PDF Stub**: Add valid PDF header/footer generation to `DocumentGenerator`.
- [ ] **Tune Timestamps**: Adjust the `TimestampService` weights to increase daytime bias.
- [ ] **Full VM Validation**: Final deployment test on a raw VHDX image.
