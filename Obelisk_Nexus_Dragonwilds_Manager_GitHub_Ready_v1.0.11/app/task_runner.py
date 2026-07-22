from __future__ import annotations

import queue
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Callable


@dataclass(slots=True)
class UiEvent:
    callback: Callable[[Any], None]
    value: Any
    is_error: bool = False


class TaskRunner:
    """All blocking work is executed away from Tk's UI thread."""

    def __init__(self, root, workers: int = 6):
        self.root = root
        self.executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="dwsm-worker")
        self.events: queue.Queue[UiEvent] = queue.Queue()
        self._closed = False
        self.root.after(30, self._drain)

    def submit(
        self,
        fn: Callable[..., Any],
        *args: Any,
        on_success: Callable[[Any], None] | None = None,
        on_error: Callable[[BaseException], None] | None = None,
        **kwargs: Any,
    ) -> Future:
        future = self.executor.submit(fn, *args, **kwargs)

        def finished(fut: Future) -> None:
            try:
                value = fut.result()
            except BaseException as exc:
                if on_error:
                    self.events.put(UiEvent(on_error, exc, True))
            else:
                if on_success:
                    self.events.put(UiEvent(on_success, value, False))

        future.add_done_callback(finished)
        return future

    def _drain(self) -> None:
        if self._closed:
            return
        for _ in range(100):
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            try:
                event.callback(event.value)
            except Exception:
                # UI callback errors must not stop the message pump.
                import traceback
                traceback.print_exc()
        self.root.after(30, self._drain)

    def close(self, *, wait: bool = False) -> None:
        self._closed = True
        self.executor.shutdown(wait=wait, cancel_futures=True)
