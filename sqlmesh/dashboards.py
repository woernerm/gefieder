"""Dashboards as code: the building blocks of a models repository's ``dashboards/`` folder.

A dashboard is put together from four kinds of thing, each defined once and referred to by
name everywhere else::

    filters.py          the controls a viewer narrows the data with
    queries/<name>.sql  SQL, in which :name is the value of a filter, and :from and :to
                        the dashboard's time range
    charts/<name>.py    how data looks: an ECharts option, without data
    boards/<name>.py    the dashboards: which query is shown in which chart

What makes a chart reusable is that every query hands over the same shape of data: the
first column is the x axis and every other column is a series. A query in long form, with a
column named ``series`` saying which series a row belongs to, is turned into that shape
before it reaches the chart. So any query can be shown in any chart without being edited.

The same objects are drawn in two places, a dashboard and a notebook cell, and both go
through ``Panel.payload`` and the one script that draws a payload (crudman's
``dashboards.js``). A notebook therefore shows exactly what the dashboard will.
"""

import copy
import json
import os
import re
import runpy
import traceback
import uuid
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import polars as pl
import polars.selectors as cs

TABLE_LIMIT = 1000
"""Rows a table sends to the browser. A download carries all of them."""

PLACEHOLDER = re.compile(
    r"'(?:[^']|'')*'|\"[^\"]*\"|--[^\n]*|/\*.*?\*/|::|:([A-Za-z_]\w*)", re.DOTALL
)
"""A :name to bind, found past string literals, comments and ``::`` casts, which hold colons
that are not placeholders -- ``'12:30'`` or ``created::date``."""

SAMPLE = pl.DataFrame({
    "category": ["Alpha", "Bravo", "Charlie", "Delta", "Echo", "Foxtrot"],
    "This year": [42, 31, 24, 15, 9, 4],
    "Last year": [35, 33, 18, 17, 6, 5],
})
"""What a chart shows in a notebook before any query is attached to it."""

TIME_SAMPLE = pl.DataFrame({
    "time": [datetime(2026, 1, 1) + timedelta(hours=hour) for hour in range(48)],
    "Read": [20 + 10 * ((hour * 7) % 5) for hour in range(48)],
    "Write": [10 + 4 * ((hour * 3) % 7) for hour in range(48)],
})
"""The same for a chart whose x axis is time."""

CARTESIAN = {"bar", "line", "scatter", "effectScatter", "pictorialBar", "candlestick", "boxplot"}
"""The series types drawn against an x and a y axis. The others -- a pie, a funnel -- take
their names from the first column and their values from the second, and one of them
per chart is all there is room for."""


TIME = {"from": "now-6h", "to": "now"}
"""The time range every dashboard has, and what it is when nothing was picked. A query reads
it as ``time >= :from AND time < :to``; the dashboard shows the time picker when one does."""

UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800, "M": 2592000, "y": 31536000}
"""Seconds per unit of a relative time, as Grafana writes them: now-15m, now-7d, now-1y."""


def moment(text: str) -> datetime:
    """A point in time as the time picker writes it: ``now``, ``now-6h``, or a date and time,
    which without a zone is the server's own.

    Raises:
        ValueError: The text is none of these.
    """
    now = datetime.now(timezone.utc)
    if text == "now":
        return now
    if relative := re.fullmatch(r"now-(\d+)([smhdwMy])", text):
        return now - timedelta(seconds=int(relative[1]) * UNITS[relative[2]])
    return datetime.fromisoformat(text).astimezone()


class Filter:
    """A control above a dashboard, bound to every :placeholder of the same name.

    Named after the variable it is assigned to in ``filters.py``. Each kind says what the
    control offers (``choices``) and what the queries receive for what was picked (``bind``).
    """

    name = ""
    everything = False
    """Whether picking nothing is a choice of its own, offered as "All"."""

    def __init__(self, label: str | None = None, default: str | None = None):
        self._label = label
        self.default = default

    @property
    def label(self) -> str:
        return self._label or self.name.replace("_", " ").capitalize()


class Select(Filter):
    """Pick from a list. Binds a list, so a query writes ``column = ANY(:name)``.

    Picking nothing means everything, which spares every query a special case for it.

    Args:
        options: SQL whose first column is the list, or the list itself.
    """

    everything = True

    def __init__(self, options: str | list, label: str | None = None):
        super().__init__(label)
        self.options = options

    def choices(self, cursor) -> list:
        if not isinstance(self.options, str):
            return list(self.options)
        cursor.execute(*statement(self.options, {}))
        return [row[0] for row in cursor.fetchall()]

    def bind(self, picked, choices):
        # Matched as text, the way a value arrives from the address bar; an unknown one is
        # ignored rather than reaching the database as something nobody offered.
        known = {str(choice): choice for choice in choices}
        return [known[value] for value in picked if value in known] or choices


def statement(sql: str, bindings: dict) -> tuple[str, dict]:
    """SQL with :name placeholders as the database driver takes it, with its parameters.

    Args:
        sql: The query as written.
        bindings: A value for every placeholder, by name.

    Returns:
        The statement in pyformat, every literal ``%`` doubled because the driver reads
        them all, and the parameters.
    """
    def swap(match):
        return f"%({match.group(1)})s" if match.group(1) else match.group()

    return PLACEHOLDER.sub(swap, sql.replace("%", "%%")), bindings


def placeholders(sql: str) -> list[str]:
    """The filters a query reads, in order of first use."""
    return list(dict.fromkeys(m.group(1) for m in PLACEHOLDER.finditer(sql) if m.group(1)))


def fetch(cursor, sql: str, bindings: dict) -> pl.DataFrame:
    """Run a query and return its rows as a DataFrame.

    Numeric columns arrive as floats rather than decimals: a chart has no use for exact
    arithmetic, and a transform written against floats is the one people expect.
    """
    cursor.execute(*statement(sql, bindings))
    columns = [column[0] for column in cursor.description]
    frame = pl.DataFrame(cursor.fetchall(), schema=columns, orient="row", infer_schema_length=None)
    return frame.with_columns(cs.decimal().cast(pl.Float64))


def wide(frame: pl.DataFrame) -> pl.DataFrame:
    """The one shape charts read: x first, then a column per series.

    A frame in long form -- x, a column named ``series``, a value -- is pivoted into it, the
    series in order of their names: a series keeps its colour whatever the data holds first.
    """
    if "series" not in frame.columns or frame.width != 3:
        return frame
    x, value = (column for column in frame.columns if column != "series")
    frame = frame.pivot(on="series", index=x, values=value, aggregate_function="sum", maintain_order=True)
    return frame.select(x, *sorted(frame.columns[1:]))


def _plain(value):
    """A value JSON can carry: dates as ISO text, which ECharts reads on a time axis."""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, float) and value != value:
        return None
    return value


def rows(frame: pl.DataFrame) -> list[list]:
    return [[_plain(value) for value in row] for row in frame.rows()]


class Chart:
    """How data looks: an ECharts option, from echarts.apache.org/examples, without data.

    ``series`` written as one dict is a template, repeated for every series the data has;
    written as a list it is used as it stands, each entry's ``encode`` naming its columns.

    Args:
        option: The ECharts option.
        prepare: What this chart does to any data before drawing it -- a pareto sorts and
            adds the running share -- as a function of a DataFrame.
        sample: The data a notebook shows it with, when the built-in samples do not fit.
    """

    kind = "echarts"

    def __init__(self, option: dict | None = None, prepare=None, sample: pl.DataFrame | None = None):
        self.option = option or {}
        self.prepare = prepare
        self.sample = sample

    def _sample(self) -> pl.DataFrame:
        if self.sample is not None:
            return self.sample
        axis = self.option.get("xAxis")
        return TIME_SAMPLE if isinstance(axis, dict) and axis.get("type") == "time" else SAMPLE

    def _encode(self, series: dict, x: str, column: str) -> dict:
        if series.get("type", "bar") not in CARTESIAN:
            return {"itemName": x, "value": column}
        axis = self.option.get("yAxis")
        if isinstance(axis, dict) and axis.get("type") == "category":
            return {"y": x, "x": column}
        return {"x": x, "y": column}

    def draw(self, frame: pl.DataFrame) -> dict:
        """The payload the browser draws: the option with the data put into it."""
        option = copy.deepcopy(self.option)
        option["dataset"] = {"dimensions": frame.columns, "source": rows(frame)}
        template = option.get("series", {"type": "bar"})
        if isinstance(template, dict):
            x, *values = frame.columns
            if template.get("type", "bar") not in CARTESIAN:
                values = values[:1]
            option["series"] = [
                {**copy.deepcopy(template), "name": column, "encode": self._encode(template, x, column)}
                for column in values
            ]
        # What every example spells out anyway, so a chart need not: axes that fit the data,
        # a tooltip, and a legend once there is more than one series to tell apart.
        cartesian = any(series.get("type", "bar") in CARTESIAN for series in option["series"])
        if cartesian and "xAxis" not in option and "yAxis" not in option:
            temporal = frame.width and frame.dtypes[0].is_temporal()
            option["xAxis"] = {"type": "time" if temporal else "category"}
            option["yAxis"] = {}
        option.setdefault("tooltip", {"trigger": "axis" if cartesian else "item"})
        if len(option["series"]) > 1:
            option.setdefault("legend", {"bottom": 0})
        if cartesian:
            option.setdefault("grid", {"left": 8, "right": 8, "top": 16, "bottom": 32, "containLabel": True})
        return {"kind": self.kind, "option": option}

    def payload(self, frame: pl.DataFrame, highlight: list[str] = ()) -> dict:
        """What the browser draws, the x values in ``highlight`` standing out: the rest is
        faded rather than left out, so what was clicked is seen against the whole."""
        frame = wide(frame)
        payload = self.draw(self.prepare(frame) if self.prepare else frame)
        if highlight and "option" in payload:
            dataset = payload["option"]["dataset"]
            dataset["dimensions"].append("highlighted")
            for row in dataset["source"]:
                row.append(int(str(row[0]) in highlight))
            payload["option"]["visualMap"] = {
                "show": False, "type": "piecewise", "dimension": len(dataset["dimensions"]) - 1,
                "pieces": [{"value": 1, "opacity": 1}, {"value": 0, "opacity": 0.25}],
            }
        return payload

    def _repr_html_(self) -> str:
        return html([self.payload(self._sample())])


class Table(Chart):
    """The data as a table: sortable by any column, filterable by every one."""

    kind = "table"

    def draw(self, frame):
        return {"kind": self.kind, "columns": frame.columns, "rows": rows(frame.head(TABLE_LIMIT)),
                "total": frame.height}


class Stat(Chart):
    """One number, large: the last value of the last column."""

    kind = "stat"

    def _sample(self):
        return pl.DataFrame({"value": [1234]})

    def draw(self, frame):
        values = frame.get_column(frame.columns[-1]).drop_nulls() if frame.width else []
        return {"kind": self.kind, "value": _plain(values[-1]) if len(values) else None}


class Panel:
    """One query in one chart, on a dashboard.

    Args:
        title: What the panel is headed with.
        query: The name of a query in ``queries/``.
        chart: The name of a chart in ``charts/``.
        transform: What this panel does to the query's data before the chart gets it, as a
            function of a DataFrame. The same query can so feed several panels.
        click: The filter a click on the chart sets to the value clicked.
        unit: How values read: ``bytes``, ``bytes/s``, ``percent``, or any text to put after
            the number.
        wide: Take a whole row rather than sharing one.
    """

    def __init__(self, title, query, chart, transform=None, click=None, unit=None, wide=False):
        self.title, self.query, self.chart = title, query, chart
        self.transform, self.click, self.unit, self.wide = transform, click, unit, wide

    def filters(self, catalog) -> list[str]:
        return placeholders(catalog.queries[self.query]) if self.query in catalog.queries else []

    def frame(self, catalog, bindings: dict, cursor) -> pl.DataFrame:
        """The query's data as this panel shows it, before the chart's own preparation."""
        frame = fetch(cursor, catalog.queries[self.query], bindings)
        return self.transform(frame) if self.transform else frame

    def payload(self, catalog, bindings: dict, cursor, highlight: list[str] = ()) -> dict:
        chart = catalog.charts[self.chart]
        frame = self.frame(catalog, bindings, cursor)
        return {**chart.payload(frame, highlight), "unit": self.unit, "click": self.click}

    def _repr_html_(self) -> str:
        return Dashboard(self.title, self)._repr_html_()


class Text(Panel):
    """Words on a dashboard: what a metric means, or how to read the ones around it."""

    def __init__(self, title: str, text: str, wide: bool = True):
        super().__init__(title, None, None, wide=wide)
        self.text = text

    def filters(self, catalog):
        return []

    def payload(self, catalog, bindings, cursor, highlight=()):
        return {"kind": "text", "text": self.text}


class Video(Text):
    """A video on a dashboard: a file the browser plays, or a page that embeds one."""

    def payload(self, catalog, bindings, cursor, highlight=()):
        return {"kind": "video", "url": self.text}


class Dashboard:
    """A page of panels, laid out in the order given and wrapping to the window's width.

    Args:
        title: The dashboard's name, as the list of dashboards shows it.
        *panels: What it shows.
        description: A sentence under the title.
        time: The time range it opens with, as the time picker writes it: ``now-6h``.
        refresh: How often it draws itself again, as the picker offers it: ``1m``, or None.
    """

    def __init__(self, title: str, *panels: Panel, description: str = "", time: str = TIME["from"],
                 refresh: str | None = None):
        self.title, self.panels, self.description = title, panels, description
        self.time, self.refresh = time, refresh

    def _repr_html_(self) -> str:
        catalog = Catalog.load(workspace())
        with closing(connect()) as connection, connection.cursor() as cursor:
            return html([catalog.draw(panel, {"from": [self.time]}, cursor) for panel in self.panels])


class Catalog:
    """Everything a ``dashboards/`` folder defines, loaded and checked.

    A file that fails to load, or a dashboard naming something that does not exist, is a
    problem reported by name rather than an exception: one broken dashboard must not take
    the others with it.
    """

    def __init__(self, root: Path):
        self.root = Path(root)
        self.filters: dict[str, Filter] = {}
        self.queries: dict[str, str] = {}
        self.charts: dict[str, Chart] = {}
        self.dashboards: dict[str, Dashboard] = {}
        self.problems: dict[str, str] = {}

    def _run(self, path: Path) -> dict:
        """A file's variables, or an empty dict and a problem when it does not run."""
        try:
            return runpy.run_path(str(path))
        except Exception:  # noqa: BLE001 -- reported, so the other files still load.
            self.problems[path.stem] = traceback.format_exc(limit=0).strip()
            return {}

    def _defined(self, folder: str, variable: str, kind: type) -> dict:
        found = {}
        for path in sorted((self.root / folder).glob("*.py")):
            value = self._run(path).get(variable)
            if isinstance(value, kind):
                found[path.stem] = value
            elif path.stem not in self.problems:
                self.problems[path.stem] = f"{folder}/{path.name} defines no {variable} = {kind.__name__}(...)"
        return found

    @classmethod
    def load(cls, root: Path) -> "Catalog":
        catalog = cls(root)
        filters = root / "filters.py"
        for name, value in (catalog._run(filters) if filters.exists() else {}).items():
            if isinstance(value, Filter):
                value.name = name
                catalog.filters[name] = value
        catalog.queries = {path.stem: path.read_text() for path in sorted((root / "queries").glob("*.sql"))}
        catalog.charts = catalog._defined("charts", "chart", Chart)
        catalog.dashboards = catalog._defined("boards", "dashboard", Dashboard)
        for name, dashboard in catalog.dashboards.items():
            if problem := catalog.check(dashboard):
                catalog.problems[name] = problem
        return catalog

    def check(self, dashboard: Dashboard) -> str | None:
        """What a dashboard names that does not exist, or None."""
        for panel in dashboard.panels:
            if isinstance(panel, Text):
                continue
            wanted = {"query": [panel.query], "chart": [panel.chart],
                      "filter": [name for name in [*panel.filters(self), panel.click] if name and name not in TIME]}
            known = {"query": self.queries, "chart": self.charts, "filter": self.filters}
            missing = [f"{kind} {name!r}" for kind, names in wanted.items()
                       for name in names if name not in known[kind]]
            if missing:
                return f"The panel {panel.title!r} names {', '.join(missing)}, which do not exist."
        return None

    def sqls(self, dashboard: Dashboard) -> list[str]:
        """The queries a dashboard runs."""
        return [self.queries[panel.query] for panel in dashboard.panels if panel.query in self.queries]

    def filters_of(self, sqls: list[str]) -> list[Filter]:
        """The filters some queries read, in the order they first appear."""
        names = dict.fromkeys(name for sql in sqls for name in placeholders(sql))
        return [self.filters[name] for name in names if name in self.filters]

    def bindings(self, sqls: list[str], picked: dict[str, list[str]], cursor) -> dict:
        """What every placeholder of some queries is bound to, given what was picked."""
        bound = {f.name: f.bind(picked.get(f.name, []), f.choices(cursor)) for f in self.filters_of(sqls)}
        used = {name for sql in sqls for name in placeholders(sql)}
        for name in TIME.keys() & used:
            try:
                bound[name] = moment(picked.get(name, [TIME[name]])[0])
            except ValueError:
                bound[name] = moment(TIME[name])
        return bound

    def panel_bindings(self, panel: Panel, picked: dict[str, list[str]], cursor) -> dict:
        """A panel's bindings, but for the filter a click on it sets: that one it shows by
        highlighting what was picked, not by leaving the rest out."""
        own = {name: values for name, values in picked.items() if name != panel.click}
        return self.bindings([self.queries[panel.query]] if panel.query in self.queries else [], own, cursor)

    def draw(self, panel: Panel, picked: dict[str, list[str]], cursor) -> dict:
        """A panel's payload, or what went wrong drawing it."""
        try:
            bindings = self.panel_bindings(panel, picked, cursor)
            payload = panel.payload(self, bindings, cursor, picked.get(panel.click, []))
        except Exception as error:  # noqa: BLE001 -- one broken panel is shown as such.
            cursor.connection.rollback()
            payload = {"kind": "error", "message": str(error).strip()}
        return {**payload, "title": panel.title, "wide": panel.wide}


def workspace() -> Path:
    """The ``dashboards/`` folder a notebook is working in.

    Searched upwards from the working directory -- a notebook opened from a chart or a
    board starts inside it -- then at the root of the server's workspace.
    """
    here = Path.cwd()
    for directory in [here, *here.parents]:
        if directory.name == "dashboards" and (directory / "charts").is_dir():
            return directory
    return Path(os.environ.get("JUPYTERHUB_ROOT_DIR") or here, "dashboards")


def connect():
    """A connection as the person the notebook runs for, as sqlmesh/config.py makes it."""
    import psycopg2

    return psycopg2.connect(
        host=os.environ.get("SQLMESH_HOST", "localhost"),
        port=os.environ.get("SQLMESH_PORT", "5432"),
        dbname=os.environ.get("SQLMESH_DATABASE", "postgres"),
        user=os.environ.get("SQLMESH_USER"),
        password=os.environ.get("SQLMESH_PASSWORD"),
    )


def html(payloads: list[dict]) -> str:
    """Payloads as a notebook output, drawn by the script the dashboards themselves use.

    The script and its libraries come from the admin panel's static files, which the notebook
    reaches on the same host: the server has no internet, and one copy of each means a
    notebook cannot draw differently from a dashboard.
    """
    static = f"/{os.environ.get('CRUDMAN_PATH', 'crudman')}/static/"
    target = f"dashboards-{uuid.uuid4().hex}"
    return f"""<div id="{target}"></div>
<script>
(async () => {{
  const load = (tag, attributes) => new Promise((ready, fail) => document.head.append(
    Object.assign(document.createElement(tag), attributes, {{ onload: ready, onerror: fail }})));
  // Once per page, however many outputs ask at the same time.
  // Tabulator's stylesheet before the one recolouring it, the libraries before the script.
  window.dashboardsLoaded ??= Promise.all([
    load("link", {{ rel: "stylesheet", href: "{static}dashboards/tabulator.min.css" }}),
    load("link", {{ rel: "stylesheet", href: "{static}dashboards/dashboards.css" }}),
    Promise.all([
      load("script", {{ src: "{static}docs/echarts.min.js" }}),
      load("script", {{ src: "{static}dashboards/tabulator.min.js" }}),
    ]).then(() => load("script", {{ src: "{static}dashboards/dashboards.js" }})),
  ]);
  await window.dashboardsLoaded;
  Dashboards.drawAll(document.getElementById("{target}"), {json.dumps(payloads)});
}})();
</script>"""


def load_ipython_extension(ipython):
    """Run a query from ``queries/`` in a notebook, its filters at their defaults.

    A query cell with a :placeholder cannot run as it stands; this binds every placeholder
    the way a dashboard opening with nothing picked would, so what the cell shows is what a
    panel gets. ``sqlnotebook`` routes such a cell here by itself.
    """
    from IPython.core.magic import register_cell_magic

    @register_cell_magic
    def query(line, cell):
        catalog = Catalog.load(workspace())
        with closing(connect()) as connection, connection.cursor() as cursor:
            return fetch(cursor, cell, catalog.bindings([cell], {}, cursor))
