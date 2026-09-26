"""The dashboards, drawn from the deployed models repository and answered as JSON.

    python dashboards_api.py

The admin panel asks here for everything it shows on a dashboard page, and a future
integration can ask the same questions. Its own process, in this image, for two reasons:
the dashboards are Python from the models repository, which must not run where the admin
panel's secrets are; and they run on the models' own dependencies, the ones a notebook has.

Reached on the pod's loopback only, as the read-only dashboards role:

    GET /dashboards                         the dashboards there are
    GET /dashboards/<name>                  one dashboard's filters and panels
    GET /dashboards/<name>/<n>              the n-th panel, drawn
    GET /dashboards/<name>/<n>.xlsx         the n-th panel's data, for Excel

Each takes ``environment`` (prod, or the environment a version is reviewed in) and the
filters as the address bar has them.
"""

import io
import json
import os
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import psycopg2

from dashboards import Catalog

MODELS_DIR = Path(os.environ.get("MODELS_DIR", "/var/lib/app/models"))
PORT = int(os.environ.get("DASHBOARDS_PORT", "8002"))
PASSWORD = Path("/run/secrets", os.environ.get("SECRET_DASHBOARDS_PASSWORD", "dashboards_password"))

STATEMENT_TIMEOUT = 60_000
"""Milliseconds a panel's query may run. A dashboard is looked at, not waited for, and a
query that runs longer holds a connection while its viewer has long moved on."""

_catalogs: dict[str, tuple[str, Catalog]] = {}
"""The loaded dashboards of each environment, with the commit they were loaded from."""


def catalog(environment: str) -> Catalog:
    """The dashboards of an environment's checkout, loaded again when its commit changes.

    The checkout and its marker are the ones crudman writes (system/repo.py ``tree_of``):
    production is ``deployed/``, every other environment is named after itself.
    """
    name = "deployed" if environment == "prod" else environment
    marker = MODELS_DIR / f"{name}.sha"
    sha = marker.read_text().strip() if marker.exists() else ""
    if environment not in _catalogs or _catalogs[environment][0] != sha:
        _catalogs[environment] = (sha, Catalog.load(MODELS_DIR / name / "dashboards"))
    return _catalogs[environment][1]


def connect(environment: str):
    """A read-only connection to the database an environment is read from.

    A version under review is read from the preview database, which holds every schema
    under the production name (postgresql/initdb/gf_0009) -- so a query is the same in both.
    """
    database = os.environ.get("POSTGRES_DB", "postgres")
    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "localhost"),
        port=os.environ.get("POSTGRES_PORT", "5432"),
        dbname=database if environment == "prod" else f"{database}_{environment}",
        user=os.environ.get("POSTGRES_USER", "dashboards"),
        password=PASSWORD.read_text().strip(),
        options=f"-c statement_timeout={STATEMENT_TIMEOUT}",
    )


def listing(dashboards: Catalog) -> dict:
    return {
        "dashboards": [
            {"name": name, "title": board.title, "description": board.description}
            for name, board in dashboards.dashboards.items()
            if name not in dashboards.problems
        ],
        "problems": dashboards.problems,
    }


def layout(dashboards: Catalog, name: str, picked: dict, cursor) -> dict:
    board = dashboards.dashboards[name]
    filters = [
        {"name": f.name, "label": f.label, "everything": f.everything,
         "choices": [str(choice) for choice in f.choices(cursor)],
         "picked": picked.get(f.name, [f.default] if f.default else [])}
        for f in dashboards.filters_of(dashboards.sqls(board))
    ]
    panels = [
        {"index": index, "title": panel.title, "wide": panel.wide, "filters": panel.filters(dashboards),
         "kind": getattr(dashboards.charts.get(panel.chart), "kind", "text")}
        for index, panel in enumerate(board.panels)
    ]
    return {"title": board.title, "description": board.description, "refresh": board.refresh,
            "filters": filters, "panels": panels}


class Handler(BaseHTTPRequestHandler):
    def _answer(self, status: int, body: bytes, content_type: str = "application/json"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, value):
        self._answer(status, json.dumps(value, default=str).encode())

    def do_GET(self):  # noqa: N802 -- the name http.server calls.
        url = urlsplit(self.path)
        picked = parse_qs(url.query)
        environment = picked.pop("environment", ["prod"])[0]
        parts = [part for part in url.path.split("/") if part]
        if parts == ["health"]:
            return self._json(200, {"status": "ok"})
        if not parts or parts[0] != "dashboards" or len(parts) > 3:
            return self._json(404, {"detail": "no such address"})

        dashboards = catalog(environment)
        if len(parts) == 1:
            return self._json(200, listing(dashboards))

        name = parts[1]
        if name in dashboards.problems:
            return self._json(409, {"detail": dashboards.problems[name]})
        if name not in dashboards.dashboards:
            return self._json(404, {"detail": f"There is no dashboard {name!r}."})

        board = dashboards.dashboards[name]
        with closing(connect(environment)) as connection, connection.cursor() as cursor:
            if len(parts) == 2:
                return self._json(200, layout(dashboards, name, picked, cursor))

            index, _, suffix = parts[2].partition(".")
            if not index.isdigit() or int(index) >= len(board.panels) or suffix not in ("", "xlsx"):
                return self._json(404, {"detail": "no such panel"})
            panel = board.panels[int(index)]
            bindings = dashboards.bindings(dashboards.sqls(board), picked, cursor)
            if not suffix:
                return self._json(200, dashboards.draw(panel, bindings, cursor))

            buffer = io.BytesIO()
            panel.frame(dashboards, bindings, cursor).write_excel(buffer, worksheet=panel.title[:31])
            self._answer(200, buffer.getvalue(),
                         "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    def log_request(self, code="-", size="-"):
        # Failures only: every panel of every page view would otherwise be a line.
        if str(code)[0] in "45":
            super().log_request(code, size)

    def log_message(self, format, *args):  # noqa: A002 -- http.server's own signature.
        # Without the timestamp http.server adds: journald stamps every line.
        print(format % args, flush=True)


if __name__ == "__main__":
    print(f"Serving the dashboards of {MODELS_DIR} on port {PORT}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
