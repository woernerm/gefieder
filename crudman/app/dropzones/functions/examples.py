"""Example check and convert functions, kept as templates for real ones.

Like the example tenants, these illustrate where real functions go and what their
signatures look like; delete them when developing for production.
"""

import polars as pl

from dropzones.registry import checker, converter


@checker("reject_empty_files")
def reject_empty_files(files):
    """Reject the upload if any file is empty, regardless of its format."""
    for path in files:
        if path.stat().st_size == 0:
            raise ValueError(f"File '{path.name}' is empty.")


@converter("csv_to_parquet")
def csv_to_parquet(files, out_dir):
    """Store every uploaded CSV file as Parquet, named after the source file."""
    for path in files:
        pl.read_csv(path).write_parquet(out_dir / (path.stem + ".parquet"))
