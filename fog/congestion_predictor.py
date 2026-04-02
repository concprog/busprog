import statistics
import time
from collections import deque, defaultdict


class CongestionPredictor:
    def __init__(self, ideal_headway_sec: float):
        self.ideal_hw = ideal_headway_sec
        self._arrivals: dict = {}

    def record_arrival(self, vehicle_id: str, ts: float):
        self._arrivals[vehicle_id] = ts

    def congestion(self) -> float:
        if len(self._arrivals) < 2:
            return 0.0
        times = sorted(self._arrivals.values())
        headways = [
            times[i] - times[i - 1]
            for i in range(1, len(times))
            if 0 < times[i] - times[i - 1] < 3600
        ]
        if not headways:
            return 0.0
        return abs(statistics.mean(headways) - self.ideal_hw)

    def mean_headway(self) -> float:
        if len(self._arrivals) < 2:
            return self.ideal_hw
        times = sorted(self._arrivals.values())
        hw = [
            times[i] - times[i - 1]
            for i in range(1, len(times))
            if 0 < times[i] - times[i - 1] < 3600
        ]
        return statistics.mean(hw) if hw else self.ideal_hw


class RouteDelayAggregator:
    WINDOW_SEC = 30

    def __init__(self):
        self._reports: dict = defaultdict(lambda: deque(maxlen=30))

    def record(self, vehicle_id: str, ts: float, pred_delay: int):
        self._reports[vehicle_id].append((ts, pred_delay))

    def route_mean_pred_delay(self) -> float:
        cutoff = time.time() - self.WINDOW_SEC
        values = [
            d for records in self._reports.values() for ts, d in records if ts > cutoff
        ]
        return statistics.mean(values) if values else 0.0
