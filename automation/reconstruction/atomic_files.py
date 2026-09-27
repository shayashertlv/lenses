"""Atomic file replacement that outlives a transient Windows denial.

An on-access scanner can hold a freshly written file for a moment; ``os.replace``
then fails with ``PermissionError`` even though nothing is wrong with the
bytes. Receipts are written with a temporary-then-replace pattern, so one such
denial would abort a job mid-stage. The retry is bounded and only for that
error; any other failure propagates unchanged.
"""
from __future__ import annotations

import os
import time


def replace_with_retry(source, destination, *, attempts: int = 12, first_delay: float = 0.05) -> None:
    if type(attempts) is not int or attempts < 1 or not 0 < first_delay <= 5:
        raise ValueError('attempts must be a positive integer and first_delay in (0, 5] seconds')
    delay = first_delay
    for attempt in range(attempts):
        try:
            os.replace(source, destination)
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(delay)
            delay = min(delay * 2, 1.0)
