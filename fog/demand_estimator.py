import pandas as pd
from collections import deque
import time


class DemandEstimator:
    def __init__(self, stop_id: str, ridership_csv: str):
        df = pd.read_csv(ridership_csv)
        df.columns = [c.lower().replace(" ", "_") for c in df.columns]
        self._profile = df[df["stop_id"] == stop_id].set_index("hour")

    def queue_length(self, hour: int) -> int:
        if hour in self._profile.index:
            return int(self._profile.loc[hour, "avg_boardings"])
        return 10


class ArrivalTracker:
    WINDOW_SEC = 300

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
