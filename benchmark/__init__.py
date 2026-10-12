"""
Algorithm comparison benchmark.

Pipeline: saved scenario -> Problem per fleet size -> every registered
algorithm's solve() -> identical scoring -> derived gap metrics.
"""

from .algorithms import ALGORITHMS, ALGORITHMS_BY_ID, Algorithm
from .runner import list_scenarios, load_scenario, run_benchmark
