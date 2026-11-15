"""
Common problem / solution types shared by every benchmarked algorithm.

A Problem is one scenario solved with a fixed number of vehicles. Node 0 is
the depot and every route is closed (starts and ends at the depot).
Algorithms minimize `cost`, which is the distance matrix unless the run picks
another objective (matching production `mode: 'distance'` by default).
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
    cost: list = None            # what algorithms minimize; defaults to dist
    elevation_m: list = None     # per-location elevation; None = flat terrain
    energy_params: dict = None   # see benchmark.energy.DEFAULT_ENERGY_PARAMS
    objective: dict = None       # parsed by benchmark.objective.parse_objective
    fixed_plan: list = None      # locked routes (see fixed_plan.py), or None
    energy_bound_wh: list = None  # per-arc energy upper bound (benchmark.energy.battery_limit)
    energy_limit_wh: int = None   # Wh a truck may use before its reserve; None = no battery limit

    def __post_init__(self):
        if self.cost is None:
            self.cost = self.dist

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
