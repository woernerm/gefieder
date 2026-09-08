# notebooks

Analysis notebooks: the exploring, charting and explaining that is not a model.

A model lives in `models/` as a `.sql` or `.py` file and opens as a notebook by itself, so
nothing here is needed to write one. What belongs here is the work that has outputs worth
keeping -- a chart, a comparison, a note explaining why a metric is defined the way it is.
These are ordinary `.ipynb` notebooks: their outputs are saved, they render on a git host,
and `nbdime` (installed with the git panel) diffs them sensibly.

A notebook opened here shares the project the models use, so `context`, `%evaluate`,
`%fetchdf` and `%plan` work in any new notebook without setup. A cell starting with SELECT
runs as a query and returns a dataframe.
