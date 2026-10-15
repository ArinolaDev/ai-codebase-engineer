"""
Tiny in-memory job runner with progress events.

Long operations (scan, review, verify, retry loop) run in a background thread.
Each job records timestamped progress events; the API can return them as a
snapshot or stream them live (Server-Sent Events). Jobs live only as long as
the server process (a restart clears them) - fine for a single-user tool.
"""

import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

MAX_JOBS = 100


class Job:
    def __init__(self, kind: str):
        self.id = uuid.uuid4().hex[:12]
        self.kind = kind
        self.status = "queued"          # queued | running | done | error
        self.events: list[dict] = []
        self.result = None
        self.error: str | None = None
        self.created = time.time()
        self._lock = threading.Lock()

    def progress(self, step: str, message: str, **extra):
        with self._lock:
            self.events.append({"i": len(self.events), "t": round(time.time() - self.created, 2),
                                "step": step, "message": message, **extra})

    def events_since(self, index: int) -> list[dict]:
        with self._lock:
            return self.events[index:]

    def snapshot(self, include_events: bool = True) -> dict:
        with self._lock:
            d = {"id": self.id, "kind": self.kind, "status": self.status,
                 "error": self.error, "result": self.result if self.status == "done" else None,
                 "elapsed": round(time.time() - self.created, 2)}
            if include_events:
                d["events"] = list(self.events)
            return d


class JobStore:
    def __init__(self, workers: int = 2):
        self._jobs: dict[str, Job] = {}
        self._pool = ThreadPoolExecutor(max_workers=workers)
        self._lock = threading.Lock()

    def submit(self, kind: str, fn) -> Job:
        """fn(progress) -> JSON-serialisable result. Exceptions become job.error."""
        job = Job(kind)
        with self._lock:
            self._jobs[job.id] = job
            while len(self._jobs) > MAX_JOBS:
                self._jobs.pop(next(iter(self._jobs)))

        def run():
            job.status = "running"
            job.progress("start", f"Started {kind}")
            try:
                job.result = fn(job.progress)
                job.status = "done"
                job.progress("done", "Finished")
            except Exception as e:  # noqa: BLE001 - report any failure to the client
                job.error = f"{type(e).__name__}: {e}"
                job.status = "error"
                job.progress("error", job.error)

        self._pool.submit(run)
        return job

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)


store = JobStore()
