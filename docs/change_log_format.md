# Change Log Format

All artifact modifications produced by ARC are recorded in a structured audit
log maintained by `core.audit_logger.AuditLogger`. Each log entry is a Python
`dict` serialised to the application logger at `INFO` level. The format is
defined here so analysis scripts and forensic reviewers can parse the log
deterministically.

---

## Log Entry Schema

Every entry emitted via `AuditLogger.log(entry)` contains the following fields:

| Field | Type | Always Present | Description |
|-------|------|----------------|-------------|
| `timestamp` | `str` (ISO-8601 UTC) | ✅ | Auto-injected by `AuditLogger.log()` |
| `service` | `str` | ✅ | `BaseService.service_name` of the emitting service |
| `operation` | `str` | ✅ | Short verb describing the operation (e.g. `"write_system_log"`) |

Additional fields are service-specific and documented per service below.

---

## Per-Service Fields

### `HiveWriter` — `service: "HiveWriter"`

| Field | Type | Description |
|-------|------|-------------|
| `hive_path` | `str` | Mount-relative path of the hive file |
| `operations_count` | `int` | Number of `HiveOperation` records executed |

### `SystemIdentity` — `service: "SystemIdentity"`

| Field | Type | Description |
|-------|------|-------------|
| `computer_name` | `str` | Written computer name |
| `registered_owner` | `str` | Written owner field |
| `operations_count` | `int` | Number of registry operations |

### `SystemLog` — `service: "SystemLog"`

| Field | Type | Description |
|-------|------|-------------|
| `profile_type` | `str` | Profile used (`home`/`office`/`developer`) |
| `computer_name` | `str` | VM hostname |
| `record_count` | `int` | EVTX records written |

### `SecurityLog` — `service: "SecurityLog"`

| Field | Type | Description |
|-------|------|-------------|
| `profile_type` | `str` | Profile used |
| `username` | `str` | Windows username |
| `computer_name` | `str` | VM hostname |
| `record_count` | `int` | EVTX records written |

### `ApplicationLog` — `service: "ApplicationLog"`

| Field | Type | Description |
|-------|------|-------------|
| `profile_type` | `str` | Profile used |
| `computer_name` | `str` | VM hostname |
| `record_count` | `int` | EVTX records written |

### `UpdateArtifacts` — `service: "UpdateArtifacts"`

| Field | Type | Description |
|-------|------|-------------|
| `profile_type` | `str` | Profile used |
| `kb_count` | `int` | Number of KB updates applied |
| `registry_ops` | `int` | Registry operations written |
| `evtx_records` | `int` | EVTX records written |

### `VmScrubber` — `service: "VmScrubber"`

| Field | Type | Description |
|-------|------|-------------|
| `computer_name` | `str` | Used as RNG seed |
| `operations_count` | `int` | Total scrub operations |

### `HardwareNormalizer` — `service: "HardwareNormalizer"`

| Field | Type | Description |
|-------|------|-------------|
| `bios_vendor` | `str` | Written BIOS vendor string |
| `motherboard_model` | `str` | Written motherboard model |
| `operations_count` | `int` | Registry operations written |

### `ProcessFaker` — `service: "ProcessFaker"`

| Field | Type | Description |
|-------|------|-------------|
| `profile_type` | `str` | Profile used |
| `username` | `str` | Windows username (for NTUSER path) |
| `operations_count` | `int` | Registry operations written |

---

## Example Log Output

```
2024-03-15T09:00:00.123456+00:00  INFO  AUDIT: {
  "timestamp": "2024-03-15T09:00:00.123456+00:00",
  "service": "SystemLog",
  "operation": "write_system_log",
  "profile_type": "office",
  "computer_name": "CORP-LT-042",
  "record_count": 34
}
```

---

## Retrieving Entries Programmatically

```python
from core.audit_logger import AuditLogger

logger = AuditLogger()
# ... run services ...
for entry in logger.entries:
    print(entry["service"], entry["operation"], entry["timestamp"])
```

---

## Replay / Reproducibility

Because all RNG in ARC is seeded deterministically (using `computer_name` and
`profile_type` as seeds), re-running the tool with identical inputs produces
identical log entries. This satisfies the project's **reproducibility**
requirement.
