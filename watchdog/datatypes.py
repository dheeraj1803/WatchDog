"""Shared data types passed between Watchdog modules."""

from dataclasses import dataclass

import numpy as np


@dataclass
class Frame:
    image: np.ndarray  # BGR, HxWx3, uint8
    t_capture_ms: int  # capture time in milliseconds, monotonic clock
