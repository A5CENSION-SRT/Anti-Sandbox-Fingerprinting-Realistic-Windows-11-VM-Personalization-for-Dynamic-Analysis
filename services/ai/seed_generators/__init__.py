"""AI-powered seed generators for artifact creation.

Each generator produces 10-50 "seeds" via Gemini that are then expanded
into thousands of artifacts by the local permutation engines.
"""

__all__ = [
    "DownloadSeedGenerator",
    "DocumentSeedGenerator",
    "BrowsingSeedGenerator",
    "FilenameSeedGenerator",
    "RegistrySeedGenerator",
    "EvtxSeedGenerator",
    "PrefetchSeedGenerator",
    "MediaSeedGenerator",
]


def __getattr__(name):
    _map = {
        "DownloadSeedGenerator": ("services.ai.seed_generators.downloads", "DownloadSeedGenerator"),
        "DocumentSeedGenerator": ("services.ai.seed_generators.documents", "DocumentSeedGenerator"),
        "BrowsingSeedGenerator": ("services.ai.seed_generators.browsing", "BrowsingSeedGenerator"),
        "FilenameSeedGenerator": ("services.ai.seed_generators.filenames", "FilenameSeedGenerator"),
        "RegistrySeedGenerator": ("services.ai.seed_generators.registry", "RegistrySeedGenerator"),
        "EvtxSeedGenerator": ("services.ai.seed_generators.evtx", "EvtxSeedGenerator"),
        "PrefetchSeedGenerator": ("services.ai.seed_generators.prefetch", "PrefetchSeedGenerator"),
        "MediaSeedGenerator": ("services.ai.seed_generators.media", "MediaSeedGenerator"),
    }
    if name in _map:
        module_path, class_name = _map[name]
        import importlib
        mod = importlib.import_module(module_path)
        return getattr(mod, class_name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
