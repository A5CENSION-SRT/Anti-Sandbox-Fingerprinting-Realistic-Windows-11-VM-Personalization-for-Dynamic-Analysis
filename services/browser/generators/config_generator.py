"""Generates browser configuration JSON files.

Creates Local State, Preferences, and Secure Preferences that
match what Chrome/Edge produce after a genuine first-run +
some usage.
"""

import json
from pathlib import Path


def generate_local_state(browser_name: str, chrome_ts: int) -> dict:
    """Build a Local State JSON dict."""
    return {
        "browser": {
            "enabled_labs_experiments": [],
            "has_seen_welcome_page": True,
        },
        "data_reduction": {
            "daily_original_length": ["0"] * 7,
        },
        "profile": {
            "created_by_version": "120.0.6099.130",
            "info_cache": {
                "Default": {
                    "active_time": chrome_ts,
                    "is_consented_primary_account": False,
                    "is_using_default_avatar": True,
                    "is_using_default_name": True,
                    "name": "Person 1",
                }
            },
            "last_used": "Default",
            "profiles_created": 1,
            "profiles_order": ["Default"],
        },
        "uninstall_metrics": {
            "installation_date2": str(chrome_ts),
        },
    }


def generate_preferences(username: str, chrome_ts: int) -> dict:
    """Build a Preferences JSON dict."""
    dl_dir = f"C:\\Users\\{username}\\Downloads"
    return {
        "bookmark_bar": {"show_on_all_tabs": True},
        "browser": {
            "has_seen_welcome_page": True,
            "show_home_button": True,
            "check_default_browser": False,
            "window_placement": {
                "bottom": 1040, "left": 0, "maximized": True,
                "right": 1920, "top": 0,
                "work_area_bottom": 1040, "work_area_left": 0,
                "work_area_right": 1920, "work_area_top": 0,
            },
        },
        "creation_time": str(chrome_ts),
        "default_search_provider_data": {
            "template_url_data": {
                "keyword": "google.com",
                "short_name": "Google",
                "url": "https://www.google.com/search?q={searchTerms}",
            }
        },
        "download": {
            "default_directory": dl_dir,
            "directory_upgrade": True,
            "prompt_for_download": False,
        },
        "homepage": "https://www.google.com/",
        "homepage_is_newtabpage": True,
        "profile": {
            "avatar_index": 0,
            "content_settings": {"exceptions": {}},
            "creation_time": str(chrome_ts),
            "default_content_setting_values": {
                "cookies": 1, "notifications": 2, "popups": 2,
            },
            "exit_type": "Normal",
            "exited_cleanly": True,
            "name": "Person 1",
        },
        "savefile": {"default_directory": dl_dir},
        "session": {"restore_on_startup": 1},
        "translate_accepted_count": {},
        "translate_blocked_languages": ["en"],
    }


def generate_secure_preferences() -> dict:
    """Build a minimal Secure Preferences JSON dict."""
    return {
        "protection": {
            "macs": {
                "browser": {"show_home_button": ""},
                "default_search_provider_data": {
                    "template_url_data": "",
                },
                "homepage": "",
                "homepage_is_newtabpage": "",
                "session": {"restore_on_startup": ""},
            },
        },
    }


def write_json(mount_manager, rel_path: str, data: dict,
               audit_logger, service_name: str,
               browser_name: str) -> None:
    """Persist a JSON file onto the mounted image and log it."""
    full = mount_manager.resolve(rel_path)
    full.parent.mkdir(parents=True, exist_ok=True)
    with open(full, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
    audit_logger.log({
        "service": service_name,
        "operation": "create_file",
        "path": str(full),
        "browser": browser_name,
        "file_type": "json_config",
    })
