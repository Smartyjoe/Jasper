import time
import uuid
from typing import Dict, Any, Optional


class RequestMetrics:
    """Tracks latency and timing metrics for a single voice request."""

    def __init__(self, request_id: Optional[str] = None):
        self.request_id = request_id or str(uuid.uuid4())[:8]
        self.timestamps: Dict[str, float] = {}
        self.durations: Dict[str, float] = {}
        self.mark("request_created")

    def mark(self, event_name: str) -> float:
        """Record a timestamp for a given event name."""
        now = time.monotonic()
        self.timestamps[event_name] = now
        return now

    def record_duration(self, stage_name: str, duration_seconds: float) -> None:
        """Explicitly record a duration for a stage."""
        self.durations[stage_name] = round(duration_seconds, 3)

    def measure(self, start_event: str, end_event: str, stage_name: str) -> Optional[float]:
        """Calculate duration between two marked events."""
        if start_event in self.timestamps and end_event in self.timestamps:
            dur = self.timestamps[end_event] - self.timestamps[start_event]
            self.durations[stage_name] = round(dur, 3)
            return dur
        return None

    def get_ttfb(self) -> Optional[float]:
        """Time To First Spoken Byte (from wake detection/capture start to first playback start)."""
        start_t = self.timestamps.get("wake_detected") or self.timestamps.get("capture_start")
        first_audio_t = self.timestamps.get("playback_start") or self.timestamps.get("first_audio_start")
        if start_t and first_audio_t:
            return round(first_audio_t - start_t, 3)
        return None

    def get_total_latency(self) -> Optional[float]:
        """Total time from wake detection to completion."""
        start_t = self.timestamps.get("wake_detected") or self.timestamps.get("capture_start")
        end_t = self.timestamps.get("request_complete") or self.timestamps.get("playback_end")
        if start_t and end_t:
            return round(end_t - start_t, 3)
        return None

    def summary(self) -> Dict[str, Any]:
        """Return structured timing summary."""
        ttfb = self.get_ttfb()
        total = self.get_total_latency()
        return {
            "request_id": self.request_id,
            "ttfb_sec": ttfb,
            "total_latency_sec": total,
            "durations": self.durations,
            "events": {k: round(v, 3) for k, v in self.timestamps.items()},
        }

    def print_summary(self) -> None:
        """Print clean benchmark report to console."""
        ttfb = self.get_ttfb()
        total = self.get_total_latency()
        print(f"\n--- [TIMING BENCHMARK - Req #{self.request_id}] ---")
        if ttfb is not None:
            print(f"  TTFB (Time To First Byte): {ttfb:.3f} s")
        if total is not None:
            print(f"  Total Request Latency:    {total:.3f} s")
        if self.durations:
            print("  Stage Durations:")
            for k, v in self.durations.items():
                print(f"    - {k:20s}: {v:.3f} s")
        print("-------------------------------------------\n")
