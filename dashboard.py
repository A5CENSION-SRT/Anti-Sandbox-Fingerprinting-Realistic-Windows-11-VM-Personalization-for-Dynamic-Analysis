#!/usr/bin/env python3
"""ARC Dashboard - Streamlit-based control panel for artifact generation.

A comprehensive dashboard for coordinating all steps of the Windows VM
personalization process, including:
- Profile configuration
- Artifact generation
- Real-time progress monitoring
- Evaluation and quality analysis
- File browser for generated artifacts

Usage:
    streamlit run dashboard.py
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
import yaml

# Add project root to path for imports
PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.audit_logger import AuditLogger
from core.orchestrator import Orchestrator, OrchestrationResult, ServiceResult
from core.profile_engine import ProfileEngine


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.yaml"
PROFILES_DIR = PROJECT_ROOT / "profiles"
DATA_DIR = PROJECT_ROOT / "data"


# ---------------------------------------------------------------------------
# Session State Management
# ---------------------------------------------------------------------------

@dataclass
class GenerationState:
    """Tracks the state of artifact generation."""
    is_running: bool = False
    current_service: str = ""
    current_index: int = 0
    total_services: int = 0
    results: List[ServiceResult] = field(default_factory=list)
    error: Optional[str] = None
    start_time: Optional[float] = None
    end_time: Optional[float] = None


def init_session_state() -> None:
    """Initialize Streamlit session state variables."""
    if "generation_state" not in st.session_state:
        st.session_state.generation_state = GenerationState()
    if "audit_entries" not in st.session_state:
        st.session_state.audit_entries = []
    if "last_result" not in st.session_state:
        st.session_state.last_result = None
    if "output_path" not in st.session_state:
        st.session_state.output_path = str(PROJECT_ROOT / "output")
    if "evaluation_report" not in st.session_state:
        st.session_state.evaluation_report = None
    if "last_context" not in st.session_state:
        st.session_state.last_context = None


# ---------------------------------------------------------------------------
# Configuration Helpers
# ---------------------------------------------------------------------------

def load_config(config_path: Path) -> Dict[str, Any]:
    """Load YAML configuration file."""
    if config_path.exists():
        with config_path.open("r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


def get_available_profiles() -> List[str]:
    """Get list of available profile names."""
    profiles = []
    if PROFILES_DIR.is_dir():
        for f in PROFILES_DIR.glob("*.yaml"):
            profiles.append(f.stem)
    return sorted(profiles)


def load_profile_details(profile_name: str) -> Dict[str, Any]:
    """Load and display profile details."""
    try:
        engine = ProfileEngine(PROFILES_DIR)
        context = engine.load_profile(profile_name)
        return {
            "username": context.username,
            "organization": context.organization,
            "locale": context.locale,
            "installed_apps": list(context.installed_apps),
            "work_hours": {
                "start": context.work_hours.start,
                "end": context.work_hours.end,
                "active_days": list(context.work_hours.active_days),
            },
            "browsing": {
                "categories": list(context.browsing.categories),
                "daily_avg_sites": context.browsing.daily_avg_sites,
            },
        }
    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Generation Runner
# ---------------------------------------------------------------------------

class DashboardAuditLogger(AuditLogger):
    """Custom audit logger that updates session state."""

    def __init__(self, session_entries: list):
        super().__init__()
        self._session_entries = session_entries

    def log(self, entry: dict) -> None:
        super().log(entry)
        self._session_entries.append(entry)


def run_generation(
    config: Dict[str, Any],
    profile_name: str,
    output_path: str,
    timeline_days: int,
    override_username: Optional[str],
    override_hostname: Optional[str],
    dry_run: bool,
    categories: Optional[List[str]],
) -> OrchestrationResult:
    """Run artifact generation with the given configuration."""
    import importlib

    # Service module mappings
    service_modules = {
        "filesystem": [
            ("services.filesystem.user_directory", "UserDirectoryService"),
            ("services.filesystem.installed_apps_stub", "InstalledAppsStub"),
            ("services.filesystem.document_generator", "DocumentGenerator"),
            ("services.filesystem.media_stub", "MediaStubService"),
            ("services.filesystem.prefetch", "PrefetchService"),
            ("services.filesystem.thumbnail_cache", "ThumbnailCacheService"),
            ("services.filesystem.recent_items", "RecentItemsService"),
            ("services.filesystem.recycle_bin", "RecycleBinService"),
        ],
        "registry": [
            ("services.registry.hive_writer", "HiveWriter"),
            ("services.registry.installed_programs", "InstalledPrograms"),
            ("services.registry.mru_recentdocs", "MruRecentDocs"),
            ("services.registry.network_profiles", "NetworkProfiles"),
            ("services.registry.system_identity", "SystemIdentity"),
            ("services.registry.userassist", "UserAssist"),
        ],
        "browser": [
            ("services.browser.browser_profile", "BrowserProfileService"),
            ("services.browser.bookmarks", "BookmarksService"),
            ("services.browser.history", "BrowserHistoryService"),
            ("services.browser.cookies_cache", "CookiesCacheService"),
            ("services.browser.downloads", "BrowserDownloadService"),
        ],
        "applications": [
            ("services.applications.dev_environment", "DevEnvironment"),
            ("services.applications.office_artifacts", "OfficeArtifacts"),
            ("services.applications.email_client", "EmailClient"),
            ("services.applications.comms_apps", "CommsApps"),
        ],
        "eventlog": [
            ("services.eventlog.evtx_writer", "EvtxWriter"),
            ("services.eventlog.application_log", "ApplicationLog"),
            ("services.eventlog.security_log", "SecurityLog"),
            ("services.eventlog.system_log", "SystemLog"),
            ("services.eventlog.update_artifacts", "UpdateArtifacts"),
        ],
        "anti_fingerprint": [
            ("services.anti_fingerprint.hardware_normalizer", "HardwareNormalizer"),
            ("services.anti_fingerprint.process_faker", "ProcessFaker"),
            ("services.anti_fingerprint.vm_scrubber", "VmScrubber"),
        ],
    }

    # Build config
    merged_config = config.copy()
    merged_config["mount_path"] = output_path
    merged_config["profile_name"] = profile_name
    merged_config["timeline_days"] = timeline_days
    if override_username:
        merged_config["override_username"] = override_username
    if override_hostname:
        merged_config["override_hostname"] = override_hostname

    # Clear and prepare audit entries
    st.session_state.audit_entries.clear()
    audit_logger = DashboardAuditLogger(st.session_state.audit_entries)

    # Create orchestrator
    orchestrator = Orchestrator(
        config=merged_config,
        audit_logger=audit_logger,
        dry_run=dry_run,
    )
    orchestrator.initialize()

    # Store context for evaluation
    st.session_state.last_context = orchestrator.context.copy()

    # Register services
    active_categories = categories or list(service_modules.keys())
    for category in active_categories:
        if category not in service_modules:
            continue
        for module_path, class_name in service_modules[category]:
            try:
                module = importlib.import_module(module_path)
                service_class = getattr(module, class_name)
                orchestrator.register_service(service_class)
            except Exception as e:
                logging.warning("Could not register %s: %s", class_name, e)

    # Progress callback
    def progress_callback(current: int, total: int, service_name: str):
        state = st.session_state.generation_state
        state.current_index = current
        state.total_services = total
        state.current_service = service_name

    # Run generation
    state = st.session_state.generation_state
    state.is_running = True
    state.start_time = time.time()
    state.results = []
    state.error = None

    try:
        result = orchestrator.run(progress_callback=progress_callback)
        state.results = result.results
        st.session_state.last_result = result
        return result
    except Exception as e:
        state.error = str(e)
        raise
    finally:
        state.is_running = False
        state.end_time = time.time()
        orchestrator.cleanup()


# ---------------------------------------------------------------------------
# Evaluation Helpers
# ---------------------------------------------------------------------------

def run_evaluation(context: Dict[str, Any]) -> Dict[str, Any]:
    """Run evaluation on generated artifacts."""
    from evaluation.report_generator import ReportGenerator

    audit_logger = AuditLogger()
    # Populate with session entries
    for entry in st.session_state.audit_entries:
        audit_logger._entries.append(entry)

    mount_root = Path(st.session_state.output_path)
    generator = ReportGenerator(audit_logger, mount_root)
    return generator.generate(context)


# ---------------------------------------------------------------------------
# UI Components
# ---------------------------------------------------------------------------

def render_sidebar() -> Dict[str, Any]:
    """Render the sidebar configuration panel."""
    st.sidebar.title("Configuration")

    # Profile Selection
    st.sidebar.subheader("Profile")
    profiles = get_available_profiles()
    profile_name = st.sidebar.selectbox(
        "Select Profile",
        profiles,
        index=profiles.index("developer") if "developer" in profiles else 0,
        help="Choose the user persona for artifact generation",
    )

    # Output Configuration
    st.sidebar.subheader("Output")
    output_path = st.sidebar.text_input(
        "Output Directory",
        value=st.session_state.output_path,
        help="Directory where artifacts will be generated",
    )
    st.session_state.output_path = output_path

    # Timeline Configuration
    st.sidebar.subheader("Timeline")
    timeline_days = st.sidebar.slider(
        "Days of History",
        min_value=7,
        max_value=365,
        value=90,
        step=7,
        help="Number of days of artifact history to simulate",
    )

    # Identity Overrides
    st.sidebar.subheader("Identity Overrides")
    override_username = st.sidebar.text_input(
        "Override Username (optional)",
        value="",
        help="Force a specific Windows username",
    )
    override_hostname = st.sidebar.text_input(
        "Override Hostname (optional)",
        value="",
        help="Force a specific computer name",
    )

    # Service Categories
    st.sidebar.subheader("Service Categories")
    all_categories = [
        "filesystem", "registry", "browser",
        "applications", "eventlog", "anti_fingerprint"
    ]
    selected_categories = st.sidebar.multiselect(
        "Active Categories",
        all_categories,
        default=all_categories,
        help="Select which service categories to run",
    )

    # Execution Mode
    st.sidebar.subheader("Execution Mode")
    dry_run = st.sidebar.checkbox(
        "Dry Run",
        value=False,
        help="Simulate execution without writing files",
    )

    return {
        "profile_name": profile_name,
        "output_path": output_path,
        "timeline_days": timeline_days,
        "override_username": override_username or None,
        "override_hostname": override_hostname or None,
        "categories": selected_categories if selected_categories else None,
        "dry_run": dry_run,
    }


def render_profile_details(profile_name: str) -> None:
    """Render profile details card."""
    st.subheader(f"Profile: {profile_name}")

    details = load_profile_details(profile_name)
    if "error" in details:
        st.error(f"Error loading profile: {details['error']}")
        return

    col1, col2, col3 = st.columns(3)

    with col1:
        st.metric("Organization", details.get("organization", "N/A"))
        st.metric("Locale", details.get("locale", "N/A"))

    with col2:
        work_hours = details.get("work_hours", {})
        st.metric(
            "Work Hours",
            f"{work_hours.get('start', 9)}:00 - {work_hours.get('end', 17)}:00"
        )
        days_map = {0: "Mon", 1: "Tue", 2: "Wed", 3: "Thu", 4: "Fri", 5: "Sat", 6: "Sun"}
        active_days = [days_map.get(d, str(d)) for d in work_hours.get("active_days", [])]
        st.metric("Active Days", ", ".join(active_days))

    with col3:
        browsing = details.get("browsing", {})
        st.metric("Daily Avg Sites", browsing.get("daily_avg_sites", 0))
        st.metric("Apps Count", len(details.get("installed_apps", [])))

    # Expandable sections
    with st.expander("Installed Applications"):
        apps = details.get("installed_apps", [])
        if apps:
            cols = st.columns(3)
            for i, app in enumerate(apps):
                cols[i % 3].write(f"- {app}")
        else:
            st.write("No applications configured")

    with st.expander("Browsing Categories"):
        categories = details.get("browsing", {}).get("categories", [])
        if categories:
            st.write(", ".join(categories))
        else:
            st.write("No categories configured")


def render_generation_controls(config: Dict[str, Any]) -> None:
    """Render generation control buttons and progress."""
    st.subheader("Generation Control")

    col1, col2, col3 = st.columns([2, 1, 1])

    state = st.session_state.generation_state

    with col1:
        if state.is_running:
            st.warning("Generation in progress...")
        elif st.button("Start Generation", type="primary", use_container_width=True):
            base_config = load_config(DEFAULT_CONFIG_PATH)
            try:
                with st.spinner("Running artifact generation..."):
                    result = run_generation(
                        config=base_config,
                        profile_name=config["profile_name"],
                        output_path=config["output_path"],
                        timeline_days=config["timeline_days"],
                        override_username=config["override_username"],
                        override_hostname=config["override_hostname"],
                        dry_run=config["dry_run"],
                        categories=config["categories"],
                    )
                if result.success:
                    st.success(f"Generation complete! {result.services_executed} services succeeded.")
                else:
                    st.error(f"Generation failed. {result.services_failed} services failed.")
                st.rerun()
            except Exception as e:
                st.error(f"Generation error: {e}")

    with col2:
        if st.button("Clear Output", use_container_width=True):
            output_path = Path(config["output_path"])
            if output_path.exists():
                import shutil
                shutil.rmtree(output_path)
                st.success("Output directory cleared")
                st.rerun()

    with col3:
        mode_text = "Dry Run Mode" if config["dry_run"] else "Live Mode"
        st.info(mode_text)

    # Progress display
    if state.is_running or state.current_service:
        progress = state.current_index / max(state.total_services, 1)
        st.progress(progress, text=f"Processing: {state.current_service}")


def render_results_summary() -> None:
    """Render generation results summary."""
    result = st.session_state.last_result
    if not result:
        st.info("No generation results yet. Run generation to see results.")
        return

    st.subheader("Generation Results")

    # Summary metrics
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Total Services", len(result.results))
    with col2:
        st.metric("Succeeded", result.services_executed, delta_color="normal")
    with col3:
        st.metric("Failed", result.services_failed, delta_color="inverse")
    with col4:
        st.metric("Duration", f"{result.total_duration_ms:.1f}ms")

    # Results table
    if result.results:
        df = pd.DataFrame([
            {
                "Service": r.service_name,
                "Status": "PASS" if r.success else "FAIL",
                "Duration (ms)": f"{r.duration_ms:.1f}",
                "Error": r.error or "",
            }
            for r in result.results
        ])
        st.dataframe(df, use_container_width=True, hide_index=True)

    # Duration chart
    if result.results:
        durations = [
            {"Service": r.service_name, "Duration": r.duration_ms, "Status": "Pass" if r.success else "Fail"}
            for r in result.results
        ]
        df_chart = pd.DataFrame(durations)
        fig = px.bar(
            df_chart,
            x="Service",
            y="Duration",
            color="Status",
            color_discrete_map={"Pass": "#28a745", "Fail": "#dc3545"},
            title="Service Execution Duration",
        )
        fig.update_layout(xaxis_tickangle=-45, height=400)
        st.plotly_chart(fig, use_container_width=True)


def render_file_browser() -> None:
    """Render the generated files browser."""
    st.subheader("Generated Files")

    output_path = Path(st.session_state.output_path)
    if not output_path.exists():
        st.info("Output directory does not exist. Run generation first.")
        return

    # File statistics
    all_files = list(output_path.rglob("*"))
    files_only = [f for f in all_files if f.is_file()]
    dirs_only = [f for f in all_files if f.is_dir()]

    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Total Files", len(files_only))
    with col2:
        st.metric("Directories", len(dirs_only))
    with col3:
        total_size = sum(f.stat().st_size for f in files_only)
        size_mb = total_size / (1024 * 1024)
        st.metric("Total Size", f"{size_mb:.2f} MB")

    # File type breakdown
    file_types: Dict[str, int] = {}
    for f in files_only:
        ext = f.suffix.lower() or "(no ext)"
        file_types[ext] = file_types.get(ext, 0) + 1

    if file_types:
        df_types = pd.DataFrame([
            {"Extension": k, "Count": v}
            for k, v in sorted(file_types.items(), key=lambda x: -x[1])
        ])
        fig = px.pie(df_types, values="Count", names="Extension", title="File Types Distribution")
        st.plotly_chart(fig, use_container_width=True)

    # Directory tree (limited)
    with st.expander("Directory Structure", expanded=False):
        tree_lines = []
        for item in sorted(output_path.iterdir())[:20]:
            if item.is_dir():
                tree_lines.append(f"[DIR] {item.name}/")
                for sub in sorted(item.iterdir())[:10]:
                    tree_lines.append(f"    - {sub.name}")
                if len(list(item.iterdir())) > 10:
                    tree_lines.append("    ... (more files)")
            else:
                tree_lines.append(f"[FILE] {item.name}")
        st.code("\n".join(tree_lines), language=None)

    # Recent files table
    with st.expander("Recent Files (by modification time)", expanded=False):
        recent_files = sorted(files_only, key=lambda f: f.stat().st_mtime, reverse=True)[:50]
        df_recent = pd.DataFrame([
            {
                "Path": str(f.relative_to(output_path)),
                "Size": f"{f.stat().st_size:,} bytes",
                "Modified": datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
            }
            for f in recent_files
        ])
        st.dataframe(df_recent, use_container_width=True, hide_index=True)


def render_evaluation_tab() -> None:
    """Render the evaluation/quality analysis tab."""
    st.subheader("Quality Evaluation")

    context = st.session_state.last_context
    if not context:
        st.info("Run generation first to enable evaluation.")
        return

    col1, col2 = st.columns([3, 1])
    with col1:
        st.write(f"**Profile:** {context.get('profile_type', 'N/A')}")
        st.write(f"**Username:** {context.get('username', 'N/A')}")
        st.write(f"**Computer:** {context.get('computer_name', 'N/A')}")

    with col2:
        if st.button("Run Evaluation", type="primary"):
            with st.spinner("Running evaluation..."):
                try:
                    report = run_evaluation(context)
                    st.session_state.evaluation_report = report
                    st.success("Evaluation complete!")
                    st.rerun()
                except Exception as e:
                    st.error(f"Evaluation error: {e}")

    report = st.session_state.evaluation_report
    if not report:
        st.info("Click 'Run Evaluation' to analyze generated artifacts.")
        return

    # Scores summary
    st.subheader("Summary Scores")
    scores = report.get("scores", {})

    col1, col2, col3 = st.columns(3)
    with col1:
        score = scores.get("consistency", 0)
        st.metric("Consistency", f"{score:.0%}")
    with col2:
        score = scores.get("density", 0)
        st.metric("Density", f"{score:.0%}")
    with col3:
        score = scores.get("detection_resistance", 0)
        st.metric("Detection Resistance", f"{score:.0%}")

    # Radar chart of scores
    score_names = list(scores.keys())
    score_values = [scores[k] * 100 for k in score_names]

    fig = go.Figure()
    fig.add_trace(go.Scatterpolar(
        r=score_values + [score_values[0]],
        theta=score_names + [score_names[0]],
        fill='toself',
        name='Scores'
    ))
    fig.update_layout(
        polar=dict(radialaxis=dict(visible=True, range=[0, 100])),
        showlegend=False,
        title="Quality Score Radar"
    )
    st.plotly_chart(fig, use_container_width=True)

    # Consistency checks
    with st.expander("Consistency Checks", expanded=True):
        consistency = report.get("consistency", [])
        if consistency:
            df_cons = pd.DataFrame([
                {
                    "Check": c["name"],
                    "Result": "PASS" if c["passed"] else "FAIL",
                    "Detail": c["detail"],
                }
                for c in consistency
            ])
            st.dataframe(df_cons, use_container_width=True, hide_index=True)

    # Density analysis
    with st.expander("Artifact Density", expanded=True):
        density = report.get("density", {})
        if density:
            df_density = pd.DataFrame([
                {
                    "Category": cat,
                    "Count": d["entry_count"],
                    "Min Required": d["min_baseline"],
                    "Typical": d["typical_baseline"],
                    "Ratio": f"{d['density_ratio']:.2f}",
                    "Status": "PASS" if d["meets_minimum"] else "FAIL",
                }
                for cat, d in density.items()
            ])
            st.dataframe(df_density, use_container_width=True, hide_index=True)

            # Bar chart
            df_chart = pd.DataFrame([
                {"Category": cat, "Count": d["entry_count"], "Minimum": d["min_baseline"]}
                for cat, d in density.items()
            ])
            fig = px.bar(df_chart, x="Category", y=["Count", "Minimum"],
                        barmode="group", title="Artifact Counts vs Minimum Required")
            st.plotly_chart(fig, use_container_width=True)

    # Sandbox signals
    with st.expander("Sandbox Signal Tests", expanded=True):
        signals = report.get("signals", [])
        if signals:
            df_signals = pd.DataFrame([
                {
                    "Signal": s["signal_name"],
                    "Status": "DETECTED" if s["detected"] else "CLEAN",
                    "Detail": s["detail"],
                }
                for s in signals
            ])
            st.dataframe(df_signals, use_container_width=True, hide_index=True)

    # Full markdown report
    with st.expander("Full Report (Markdown)", expanded=False):
        st.markdown(report.get("markdown", "No report available"))


def render_audit_log() -> None:
    """Render the audit log viewer."""
    st.subheader("Audit Log")

    entries = st.session_state.audit_entries
    if not entries:
        st.info("No audit entries yet. Run generation to see audit log.")
        return

    st.metric("Total Entries", len(entries))

    # Filter controls
    col1, col2 = st.columns(2)
    with col1:
        services = sorted(set(e.get("service", "unknown") for e in entries))
        selected_service = st.selectbox("Filter by Service", ["All"] + services)
    with col2:
        operations = sorted(set(e.get("operation", "unknown") for e in entries))
        selected_op = st.selectbox("Filter by Operation", ["All"] + operations)

    # Filter entries
    filtered = entries
    if selected_service != "All":
        filtered = [e for e in filtered if e.get("service") == selected_service]
    if selected_op != "All":
        filtered = [e for e in filtered if e.get("operation") == selected_op]

    st.write(f"Showing {len(filtered)} of {len(entries)} entries")

    # Display entries
    for i, entry in enumerate(filtered[:100]):  # Limit to 100
        timestamp = entry.get("timestamp", "")
        service = entry.get("service", "unknown")
        operation = entry.get("operation", "unknown")

        with st.expander(f"[{i+1}] {service} - {operation}", expanded=False):
            st.json(entry)

    if len(filtered) > 100:
        st.warning(f"Showing first 100 of {len(filtered)} entries")


# ---------------------------------------------------------------------------
# Main Application
# ---------------------------------------------------------------------------

def main() -> None:
    """Main Streamlit application entry point."""
    st.set_page_config(
        page_title="ARC Dashboard",
        page_icon="🎭",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    init_session_state()

    # Header
    st.title("ARC - Artifact Reality Composer")
    st.markdown("*Generate realistic Windows filesystem artifacts for VM personalization*")

    # Sidebar configuration
    config = render_sidebar()

    # Main content tabs
    tab1, tab2, tab3, tab4, tab5 = st.tabs([
        "Profile & Generation",
        "Results",
        "File Browser",
        "Evaluation",
        "Audit Log"
    ])

    with tab1:
        render_profile_details(config["profile_name"])
        st.divider()
        render_generation_controls(config)

    with tab2:
        render_results_summary()

    with tab3:
        render_file_browser()

    with tab4:
        render_evaluation_tab()

    with tab5:
        render_audit_log()

    # Footer
    st.divider()
    st.caption("ARC Dashboard v1.0 | Anti-Sandbox Fingerprinting Project")


if __name__ == "__main__":
    main()
