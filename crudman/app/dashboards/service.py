"""The dashboards service (sqlmesh/dashboards_api.py), as crudman asks it.

crudman runs none of a dashboard's own code: the dashboards are Python from the models
repository, which is the service's to run, as the read-only dashboards role and away from
this process's secrets. So every page here is an answer the service gave, rendered.
"""
import json
import os
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen

from system.models import Approval, Deployment

SERVICE = f"http://127.0.0.1:{os.environ.get('DASHBOARDS_PORT', '8002')}/dashboards/"
"""Where the service listens: in the pod, which shares one loopback."""

TIMEOUT = 90
"""Seconds to wait for an answer: longer than the statement timeout the service sets on a
query, so a slow panel reports its own error rather than this one."""


def environment(user) -> str:
    """Which version this person reads: the one they owe a decision on, or production.

    The same rule as the documentation's, so what a reviewer reads about the models and
    what their dashboards show are one version.
    """
    return Deployment.PREVIEW if Approval.owed_by(user) else Deployment.PROD


def ask(request, path: str = "") -> tuple[int, bytes]:
    """Pass a request on to the service, with the reader's filters and version.

    Args:
        request: The reader's request; its query string holds the filters.
        path: What to ask for, below /dashboards/.

    Returns:
        The service's status and body -- or, when it did not answer, 503 and a detail
        in the shape of its own errors, so a caller handles both alike.
    """
    query = [*request.GET.lists(), ("environment", [environment(request.user)])]
    url = SERVICE + path + "?" + urlencode([(k, v) for k, values in query for v in values])
    try:
        with urlopen(url, timeout=TIMEOUT) as response:
            return response.status, response.read()
    except HTTPError as error:
        return error.code, error.read()
    except (URLError, TimeoutError):
        return 503, json.dumps({"detail": "The dashboards are not available right now."}).encode()


def ask_json(request, path: str = "") -> tuple[int, dict]:
    status, body = ask(request, path)
    return status, json.loads(body)
