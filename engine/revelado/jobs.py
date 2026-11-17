# SPDX-License-Identifier: GPL-3.0-or-later
"""Job queue with bounded concurrency and a WebSocket event bus.

Two worker threads:
  heavy  - exactly ONE: develop sample / batch / export. A sample jumps ahead of queued batches (priority) but never
           pre-empts the photo in progress.
  light  - exactly ONE: technical cull, Claude cull (network + reduced images).
Jobs are cancellable between items; a running heavy subprocess is killed on cancel.
"""
from __future__ import annotations

import asyncio
import itertools
import queue
import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from .errors import ReveladoError
from .log import get_logger

log = get_logger("jobs")
HEAVY_KINDS = {"sample", "batch", "export"}


class EventBus:
    def __init__(self):
        self._subs: set[asyncio.Queue] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock = threading.Lock()

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=500)
        with self._lock:
            self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        with self._lock:
            self._subs.discard(q)

    def _put(self, ev: dict) -> None:
        with self._lock:
            subs = list(self._subs)
        for q in subs:
            try:
                q.put_nowait(ev)
            except asyncio.QueueFull:   # slow client: drop oldest
                try:
                    q.get_nowait()
                    q.put_nowait(ev)
                except Exception:  # noqa: BLE001
                    pass

    def emit(self, ev: dict) -> None:
        if self._loop is None or self._loop.is_closed():
            return
        try:
            self._loop.call_soon_threadsafe(self._put, ev)
        except RuntimeError:
            pass


class Cancelled(Exception):
    pass


@dataclass
class Job:
    job_id: str
    kind: str
    payload: dict
    priority: int = 5
    state: str = "queued"
    progress: float = 0.0
    stage: str | None = None
    eta_s: float | None = None
    outputs: list[dict] = field(default_factory=list)
    log: list[str] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)
    items_total: int = 0
    items_done: int = 0
    items_failed: int = 0
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    result: dict = field(default_factory=dict)
    cancel_flag: threading.Event = field(default_factory=threading.Event)
    on_cancel: Callable[[], None] | None = None
    _bus: EventBus | None = None

    def public(self) -> dict:
        return {"job_id": self.job_id, "kind": self.kind, "state": self.state, "progress": round(self.progress, 4),
                "stage": self.stage, "eta_s": None if self.eta_s is None else round(self.eta_s, 1),
                "outputs": self.outputs, "log": self.log[-300:], "errors": self.errors,
                "items_total": self.items_total, "items_done": self.items_done, "items_failed": self.items_failed,
                "created_at": self.created_at, "started_at": self.started_at, "finished_at": self.finished_at,
                "result": self.result, "photo_ids": self.payload.get("photo_ids") or
                ([self.payload["photo_id"]] if self.payload.get("photo_id") else None)}

    # --------------------------------------------------------------- reporting helpers used by runners
    def say(self, line: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        self.log.append(f"{stamp} {line}")
        log.info("[%s] %s", self.job_id, line)
        if self._bus:
            self._bus.emit({"type": "job_log", "job_id": self.job_id, "line": f"{stamp} {line}"})

    def update(self, progress: float | None = None, stage: str | None = None, eta_s: float | None = None,
               photo_id: str | None = None) -> None:
        if progress is not None:
            self.progress = max(0.0, min(1.0, progress))
        if stage is not None:
            self.stage = stage
        if eta_s is not None:
            self.eta_s = max(0.0, eta_s)
        elif self.started_at and self.progress > 0.02:
            el = time.time() - self.started_at
            self.eta_s = el * (1 - self.progress) / self.progress
        if self._bus:
            ev = {"type": "job_progress", "job_id": self.job_id, "kind": self.kind, "progress": round(self.progress, 4),
                  "stage": self.stage, "eta_s": None if self.eta_s is None else round(self.eta_s, 1)}
            if photo_id:
                ev["photo_id"] = photo_id
            self._bus.emit(ev)

    def check_cancel(self) -> None:
        if self.cancel_flag.is_set():
            raise Cancelled()


class JobManager:
    def __init__(self, bus: EventBus, persist: Callable[[Job, bool], None] | None = None):
        self.bus = bus
        self.jobs: dict[str, Job] = {}
        self._persist = persist
        self._runners: dict[str, Callable[[Job], None]] = {}
        self._seq = itertools.count()
        self._heavy: queue.PriorityQueue = queue.PriorityQueue()
        self._light: queue.PriorityQueue = queue.PriorityQueue()
        self._stop = threading.Event()
        self._threads = [threading.Thread(target=self._loop, args=(self._heavy, "heavy"), daemon=True, name="heavy"),
                         threading.Thread(target=self._loop, args=(self._light, "light"), daemon=True, name="light")]
        for t in self._threads:
            t.start()

    def register(self, kind: str, runner: Callable[[Job], None]) -> None:
        self._runners[kind] = runner

    def submit(self, kind: str, payload: dict, priority: int = 5) -> Job:
        if kind not in self._runners:
            raise ReveladoError("E_INTERNAL", f"tipo de trabajo sin ejecutor: {kind}")
        j = Job(job_id=uuid.uuid4().hex[:12], kind=kind, payload=payload, priority=priority, _bus=self.bus)
        self.jobs[j.job_id] = j
        q = self._heavy if kind in HEAVY_KINDS else self._light
        q.put((priority, next(self._seq), j.job_id))
        pos = q.qsize()
        j.say(f"en cola ({'pesado' if kind in HEAVY_KINDS else 'ligero'}, posición {pos})")
        self._save(j, False)
        self.bus.emit({"type": "job_progress", "job_id": j.job_id, "kind": kind, "progress": 0.0, "stage": "queued",
                       "eta_s": None})
        return j

    def get(self, job_id: str) -> Job | None:
        return self.jobs.get(job_id)

    def cancel(self, job_id: str) -> bool:
        j = self.jobs.get(job_id)
        if not j or j.state in ("done", "error", "cancelled"):
            return False
        j.cancel_flag.set()
        j.say("cancelación solicitada")
        if j.state == "queued":
            j.state = "cancelled"
            j.finished_at = time.time()
            self._done_event(j)
        elif j.on_cancel:
            try:
                j.on_cancel()
            except Exception:  # noqa: BLE001
                log.exception("on_cancel failed")
        return True

    def active(self) -> list[Job]:
        return [j for j in self.jobs.values() if j.state in ("queued", "running")]

    def _save(self, j: Job, finished: bool) -> None:
        if self._persist:
            try:
                self._persist(j, finished)
            except Exception:  # noqa: BLE001
                log.exception("persist job failed")

    def _done_event(self, j: Job) -> None:
        self._save(j, True)
        self.bus.emit({"type": "job_done", "job_id": j.job_id, "kind": j.kind, "state": j.state,
                       "outputs": j.outputs, "items_done": j.items_done, "items_failed": j.items_failed})

    def _loop(self, q: queue.PriorityQueue, name: str) -> None:
        while not self._stop.is_set():
            try:
                _, _, jid = q.get(timeout=0.5)
            except queue.Empty:
                continue
            j = self.jobs.get(jid)
            if not j or j.state != "queued":
                continue
            j.state = "running"
            j.started_at = time.time()
            j.update(0.0, "starting")
            self._save(j, False)
            try:
                self._runners[j.kind](j)
                if j.cancel_flag.is_set():
                    j.state = "cancelled"
                else:
                    j.state = "error" if (j.items_total and j.items_failed == j.items_total) else "done"
                    j.progress = 1.0
                    j.eta_s = 0.0
                    j.stage = "done"
            except Cancelled:
                j.state = "cancelled"
                j.say("cancelado")
            except ReveladoError as e:
                j.state = "error"
                j.errors.append({"code": e.code, "message": e.message_es, "detail": e.detail})
                j.say(f"ERROR {e.code}: {e.message_es} {e.detail}".strip())
            except Exception as e:  # noqa: BLE001
                j.state = "error"
                j.errors.append({"code": "E_INTERNAL", "message": "Error interno del motor", "detail": repr(e)})
                j.say(f"ERROR interno: {e!r}")
                log.error("job %s crashed:\n%s", jid, traceback.format_exc())
            j.finished_at = time.time()
            self._done_event(j)

    def shutdown(self) -> None:
        self._stop.set()
        for j in self.active():
            self.cancel(j.job_id)


def run_item(job: Job, photo_id: str, fn: Callable[[], Any]) -> Any:
    """Run one item of a multi-item job, recording per-item failure without stopping the job."""
    try:
        return fn()
    except Cancelled:
        raise
    except ReveladoError as e:
        if e.code == "E_CANCELLED":
            raise Cancelled() from e
        job.items_failed += 1
        job.errors.append({"photo_id": photo_id, "code": e.code, "message": e.message_es, "detail": e.detail})
        job.say(f"{photo_id}: ERROR {e.code} {e.message_es} {e.detail}".strip())
    except Exception as e:  # noqa: BLE001
        job.items_failed += 1
        job.errors.append({"photo_id": photo_id, "code": "E_INTERNAL", "message": "Error interno", "detail": repr(e)})
        job.say(f"{photo_id}: ERROR interno {e!r}")
        log.error("item %s failed:\n%s", photo_id, traceback.format_exc())
    return None
