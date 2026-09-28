# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PocketKode
"""Background jobs that the UI can poll and cancel."""
import threading


class Cancelled(Exception):
    pass


class Task(threading.Thread):
    """Runs fn(task) in a thread. fn may set task.status / task.progress and should call
    task.check() often so B can cancel it."""

    def __init__(self, fn):
        super().__init__(daemon=True)
        self.fn = fn
        self.result = None
        self.error = None
        self.done = False
        self.cancelled = False
        self.status = ""
        self.progress = None  # 0..1, or None for "unknown"
        self.start()

    def run(self):
        try:
            self.result = self.fn(self)
        except Cancelled:
            pass
        except Exception as e:  # noqa: BLE001 - shown to the user
            self.error = str(e) or e.__class__.__name__
        finally:
            self.done = True

    def check(self):
        if self.cancelled:
            raise Cancelled()

    def cancel(self):
        self.cancelled = True
