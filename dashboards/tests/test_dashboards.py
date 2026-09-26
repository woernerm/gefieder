"""Checks on the dashboards. Run them in a notebook's terminal:

    pytest dashboards
"""
from pathlib import Path

import polars as pl

from dashboards import Catalog

CATALOG = Catalog.load(Path(__file__).parents[1])


def test_every_file_shall_load_and_name_only_what_exists():
    assert CATALOG.problems == {}


def test_a_pareto_shall_run_up_to_the_whole():
    frame = pl.DataFrame({"component": ["b", "a", "c"], "Changes": [1, 6, 3]})
    source = CATALOG.charts["pareto"].payload(frame)["option"]["dataset"]["source"]
    assert [row[0] for row in source] == ["a", "c", "b"]
    assert source[-1][-1] == 100
