SUPPLY_HIGH = 0.5
SUPPLY_LOW = 0.15
DEMAND_HIGH = 20
DEMAND_LOW = 5


def detect_choke(arrival_freq: float, queue_length: int) -> str:
    if arrival_freq > SUPPLY_HIGH and queue_length < DEMAND_LOW:
        return "OVERSUPPLY"
    if arrival_freq < SUPPLY_LOW and queue_length > DEMAND_HIGH:
        return "STARVATION"
    return "NOMINAL"
