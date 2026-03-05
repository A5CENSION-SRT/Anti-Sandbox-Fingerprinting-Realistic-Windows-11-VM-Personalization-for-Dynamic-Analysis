# Profile Schema Reference

Profiles are YAML files stored under `profiles/`. Each profile defines the usage
pattern of a simulated Windows user. Profiles support inheritance via the
`extends` key, which resolves recursively with deep-merge semantics (child
values override parent values).

---

## Inheritance Chain

```
base.yaml
  └── home_user.yaml
  └── office_user.yaml
        └── developer.yaml
```

All profiles that omit a field inherit it from `base.yaml`.

---

## Full Schema

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `extends` | `str` | No | Name of parent profile (without `.yaml`) |
| `username` | `str` | Yes | Windows username (e.g. `"alice.smith"`) |
| `organization` | `str` | Yes | Registered organisation name |
| `locale` | `str` | Yes | BCP-47 locale string (e.g. `"en_US"`) |
| `installed_apps` | `list[str]` | Yes | List of application identifiers |
| `browsing.categories` | `list[str]` | Yes | URL category buckets from `data/urls_by_category.json` |
| `browsing.daily_avg_sites` | `int` | Yes | Average distinct sites visited per day |
| `work_hours.start` | `int` | Yes | Start hour (24h, inclusive) |
| `work_hours.end` | `int` | Yes | End hour (24h, exclusive) |
| `work_hours.active_days` | `list[int]` | Yes | ISO weekday numbers (1=Mon … 7=Sun) |

---

## Pydantic Validation

Profiles are loaded through `ProfileEngine` and validated against the
`ProfileContext` Pydantic model (`core/profile_engine.py`).  The model uses
`frozen=True, extra="forbid"`, so any unrecognised key raises a validation
error at load time.

---

## Example — `office_user.yaml`

```yaml
extends: base
username: "jane.doe"
organization: "Contoso Ltd."
locale: "en_US"
installed_apps:
  - msoffice
  - chrome
  - teams
  - onedrive
browsing:
  categories:
    - news
    - productivity
    - social_media
  daily_avg_sites: 15
work_hours:
  start: 8
  end: 18
  active_days: [1, 2, 3, 4, 5]
```

---

## Adding a New Profile

1. Create `profiles/<name>.yaml`.
2. Set `extends: <parent>` (typically `base` or `office_user`).
3. Override only the fields that differ from the parent.
4. Run `python -c "from core.profile_engine import ProfileEngine; ProfileEngine('profiles').load('<name>')"` to validate.
