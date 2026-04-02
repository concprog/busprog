import pandas as pd
from collections import deque
import time
import logging

logger = logging.getLogger(__name__)

DEFAULT_HOURLY_QUEUE = {
    0: 2,
    1: 1,
    2: 1,
    3: 1,
    4: 2,
    5: 5,
    6: 12,
    7: 25,
    8: 30,
    9: 20,
    10: 15,
    11: 15,
    12: 18,
    13: 20,
    14: 22,
    15: 28,
    16: 35,
    17: 40,
    18: 30,
    19: 20,
    20: 15,
    21: 10,
    22: 8,
    23: 5,
}


class DemandEstimator:
    def __init__(self, stop_id: str, ridership_csv: str = None):
        self._profile = None
        if ridership_csv:
            try:
                df = pd.read_csv(ridership_csv)
                df.columns = [c.lower().replace(" ", "_") for c in df.columns]
                df["stop_id"] = df["stop_id"].astype(str)
                self._profile = df[df["stop_id"] == str(stop_id)].set_index("hour")
                logger.info(f"Loaded ridership data from {ridership_csv}")
            except FileNotFoundError:
                logger.warning(f"CSV not found: {ridership_csv}, using default profile")
            except Exception as e:
                logger.warning(f"Error loading CSV: {e}, using default profile")

    def queue_length(self, hour: int) -> int:
        if self._profile is not None and hour in self._profile.index:
            return int(self._profile.loc[hour, "avg_boardings"])
        return DEFAULT_HOURLY_QUEUE.get(hour, 10)


class ArrivalTracker:
    WINDOW_SEC = 20

    def __init__(self):
        self._log: deque = deque()

    def record(self, vehicle_id: str, ts: float):
        self._log.append((ts, vehicle_id))
        cutoff = time.time() - self.WINDOW_SEC
        while self._log and self._log[0][0] < cutoff:
            self._log.popleft()

    def frequency(self) -> float:
        unique = len({v for _, v in self._log})
        return unique / (self.WINDOW_SEC / 60.0)
