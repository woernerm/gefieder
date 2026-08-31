"""Default check and convert functions shipped with the system.

They also serve as templates: add a decorated function to a module in this folder and
rebuild the image.
"""

from pathlib import Path

import polars as pl

from dropzones.registry import checker, converter


@checker("Reject empty files")
def reject_empty_files(files: list[Path]) -> None:
    """Reject the upload if any file is empty, regardless of its format."""
    for path in files:
        if path.stat().st_size == 0:
            raise ValueError(f"File '{path.name}' is empty.")


@converter("CSV to Parquet")
def csv_to_parquet(files: list[Path], out_dir: Path) -> None:
    """Store every uploaded CSV file as Parquet, named after the source file."""
    for path in files:
        pl.read_csv(path).write_parquet(out_dir / (path.stem + ".parquet"))


@converter("Excel to Parquet (one file per sheet)")
def excel_to_parquet(files: list[Path], out_dir: Path) -> None:
    """Store every sheet as its own Parquet file, named after source file and sheet."""
    for path in files:
        for sheet, frame in pl.read_excel(path, sheet_id=0).items():
            frame.write_parquet(out_dir / f"{path.stem}_{sheet}.parquet")


@converter("JSON to Parquet")
def json_to_parquet(files: list[Path], out_dir: Path) -> None:
    """Store every uploaded JSON file (one object or an array) as Parquet."""
    for path in files:
        pl.read_json(path).write_parquet(out_dir / (path.stem + ".parquet"))
