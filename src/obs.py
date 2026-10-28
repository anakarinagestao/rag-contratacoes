from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now() -> str:
	return datetime.now(timezone.utc).isoformat()


class JsonlLogger:
	def __init__(self, path: str | Path | None = None) -> None:
		self.path = Path(path) if path else None

	def event(self, event: str, **fields: Any) -> None:
		record = {"timestamp": utc_now(), "event": event, **fields}
		line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
		if self.path:
			self.path.parent.mkdir(parents=True, exist_ok=True)
			with self.path.open("a", encoding="utf-8") as file:
				file.write(line + "\n")
		else:
			print(line)


class Timer:
	def __enter__(self) -> "Timer":
		self.started = time.perf_counter()
		return self

	def __exit__(self, *_: Any) -> None:
		self.elapsed_seconds = time.perf_counter() - self.started
