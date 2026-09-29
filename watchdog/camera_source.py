"""Frame sources for Watchdog.

Every camera (laptop webcam, Go2 robot camera) sits behind the same
CameraSource interface, so the rest of the pipeline doesn't care where
frames come from.
"""

from abc import ABC, abstractmethod

from watchdog.datatypes import Frame


class CameraSource(ABC):
    """A source of BGR video frames."""

    @abstractmethod
    def read(self) -> Frame | None:
        """Return the next frame, with its capture timestamp.

        Returns None if no frame is available (camera closed, read failed,
        or stream ended).
        """

    def close(self) -> None:
        """Release the underlying device. Subclasses override if needed."""


class WebcamSource(CameraSource):
    """Laptop webcam via OpenCV."""

    def __init__(self, device_index: int = 0) -> None:
        self.device_index = device_index
        # TODO(me): open the camera here.

    def read(self) -> Frame | None:
        # TODO(me): implement.
        raise NotImplementedError


# TODO(me): Go2Source(CameraSource) using go2-webrtc-connect.
