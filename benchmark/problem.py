"""
Common problem / solution types shared by every benchmarked algorithm.

A Problem is one scenario solved with a fixed number of vehicles. Node 0 is
the depot, every route is closed (starts and ends at the depot), and the
optimization objective is the distance matrix, matching production
`mode: 'distance'`.
"""

import math
from dataclasses import dataclass, field


@dataclass
class Problem:
    scenario_id: str
    locations: list          # [{address, lat, lng}, ...]; index 0 = depot
    dist: list               # meters, square matrix
    time: list               # seconds, square matrix
    num_vehicles: int
    ortools_time_limit_s: int = None

    @property
    def n(self) -> int:
        return len(self.dist)

    @property
    def num_stops(self) -> int:
        return self.n - 1

    @property
    def max_stops_per_vehicle(self) -> int:
        # Same cap OR-Tools enforces in solvers.solve_vrp_ortools
        return math.ceil(self.num_stops / self.num_vehicles)


@dataclass
class Solution:
    routes: list                           # [[0, ..., 0], ...]
    solve_time_ms: float = 0.0
    meta: dict = field(default_factory=dict)
