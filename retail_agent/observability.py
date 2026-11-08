import json
import os
from datetime import datetime, timezone
from pathlib import Path


class Events:
    """Allowlisted metadata only. No prompts, results, SQL parameters or exceptions."""

    ALLOWED = {
        "stage",
        "status",
        "code",
        "elapsed_ms",
        "query_id",
        "sql_hash",
        "job_id",
        "bytes_processed",
        "rows",
        "attempt",
        "input_tokens",
        "output_tokens",
        "model",
        "persona_version",
        "golden_ids",
        "compiler_version",
        "count",
    }

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = path

    def emit(self, trace_id: str, event: str, **fields):
        record = {
            "time": datetime.now(timezone.utc).isoformat(),
            "trace_id": trace_id,
            "event": event,
            **{k: v for k, v in fields.items() if k in self.ALLOWED},
        }
        fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, default=str) + "\n")
