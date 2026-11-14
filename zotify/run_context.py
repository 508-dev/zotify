"""Per-query state for session access, journaling, and structured run events."""
from __future__ import annotations

import json
from pathlib import Path
from time import time


class RunContext:
    def __init__(self, run_id: str, session, journal, root_path: str | Path):
        self.run_id = run_id
        self.session = session
        self.session_generation = 0
        self.journal = journal
        self.completed_downloads: dict[str, Path] = {}
        self.song_archives: dict = {}
        self.root_path = Path(root_path)
        self.events_path = self.root_path / f"zotify-run-{run_id}.jsonl"

    def replace_session(self, session) -> None:
        self.session = session
        self.session_generation += 1
        self.record("session_renewed", generation=self.session_generation)

    def record(self, event: str, **fields) -> None:
        self.events_path.parent.mkdir(parents=True, exist_ok=True)
        record = {"run_id": self.run_id, "timestamp": time(), "event": event}
        record.update(fields)
        with self.events_path.open("a", encoding="utf-8") as output:
            output.write(json.dumps(record, ensure_ascii=False) + "\n")
