SUPPLY_HIGH = 0.1
SUPPLY_LOW = 0.02
DEMAND_HIGH = 8
DEMAND_LOW = 12


def detect_choke(arrival_freq: float, queue_length: int) -> str:
    if arrival_freq > SUPPLY_HIGH and queue_length < DEMAND_LOW:
        return "OVERSUPPLY"
    if arrival_freq < SUPPLY_LOW and queue_length > DEMAND_HIGH:
        return "STARVATION"
    return "NOMINAL"
