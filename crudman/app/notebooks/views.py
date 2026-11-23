"""The endpoints the other services authenticate their visitors against.

Neither JupyterHub nor Grafana keeps accounts of its own. Both ask here with the visitor's
crudman session cookie, so there is one sign-in, and single sign-on reaches both. The hub
asks ``whoami`` itself; for Grafana, nginx's auth_request asks ``grafana``.

Both are served under ``/<CRUDMAN_PATH>/``, so they answer only requests that did not
come through the proxy.
"""
from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from dbusers.utils import db_role_for_user
from sso.roles import GROUP_FOR_RANK
from system.models import Approval, Deployment

from .utils import issue_notebook_credential, may_use_notebooks, refusal

GRAFANA_ROLE = {GROUP_FOR_RANK[rank]: rank.capitalize() for rank in GROUP_FOR_RANK}
"""Grafana's organisation role for each rank's group: Viewer, Editor, Admin."""

PREVIEW_DATASOURCE = f"{settings.APP_NAME}-{Deployment.PREVIEW}"
"""The Grafana data source that reads a version under review.

Named after the environment, so a second review is a second data source and no new rule.
grafana/provisioning/datasources/postgresql.yaml is where this one is declared, and
proxy/maps.conf.template where the production uid it replaces is spelled out.
"""

FORWARDED = "HTTP_X_FORWARDED_FOR"
"""What tells a browser's request from the hub's.

The containers share one network namespace, so both arrive from the loopback and the
address says nothing. The proxy sets this header on everything it forwards (see
proxy/locations.conf.template) and the hub, calling Django directly, sets none -- so its
absence is what marks a request as coming from inside the pod.
"""


@csrf_exempt
@require_http_methods(["GET", "POST"])
def whoami(request):
    """Identify the caller, and on POST rotate their notebook database credential.

    The hub calls GET to authenticate a visitor and POST just before it spawns their server.
    Only requests not through the proxy are answered: rotating invalidates the person's open
    notebook, so a page on another site must not trigger it with their cookie. The hub has no
    form and no CSRF token, hence the exemption.

    Args:
        request: The HTTP request, carrying the visitor's crudman session cookie.

    Returns:
        401 when the cookie names nobody, 403 when they hold too low a rank, and otherwise
        the identity as JSON, with the database role and password added on POST.
    """
    if FORWARDED in request.META:
        return JsonResponse({"detail": "not reachable from here"}, status=404)

    user = request.user
    if not user.is_authenticated:
        return JsonResponse({"detail": "not signed in"}, status=401)

    if not may_use_notebooks(user):
        return JsonResponse({"detail": refusal(user)}, status=403)

    identity = {
        "username": user.username,
        "name": user.get_full_name() or user.username,
        "email": user.email,
        "admin": user.is_superuser,
    }

    if request.method == "POST":
        try:
            role, password = issue_notebook_credential(user)
        except ValueError as error:
            return JsonResponse({"detail": str(error)}, status=409)
        identity |= {"db_user": role, "db_password": password}

    # No-store: the credential must not survive in any cache between here and the hub.
    return JsonResponse(identity, headers={"Cache-Control": "no-store"})


@require_http_methods(["GET"])
def grafana(request):
    """Identify the caller for Grafana, in the headers its auth proxy reads.

    nginx asks here with the visitor's cookies before forwarding a Grafana request and
    copies the answer's headers into it; Grafana trusts them, as only the proxy can reach
    it. Every rank may see Grafana, the viewer's included.

    Someone who owes a decision on a version under review gets a header naming that
    version's data source, which the proxy substitutes for the one every dashboard
    carries. Without the header, the proxy's map falls back to production.

    Args:
        request: The HTTP request, carrying the visitor's crudman session cookie.

    Returns:
        401 when the cookie names nobody, 403 when they hold no rank, otherwise 200 with
        the identity in X-WEBAUTH-* headers.
    """
    if FORWARDED in request.META:
        return HttpResponse(status=404)

    user = request.user
    if not user.is_authenticated:
        return HttpResponse(status=401)

    role = GRAFANA_ROLE.get(db_role_for_user(user))
    if role is None:
        return HttpResponse(status=403)

    headers = {
        "X-WEBAUTH-USER": user.username,
        "X-WEBAUTH-NAME": user.get_full_name() or user.username,
        "X-WEBAUTH-EMAIL": user.email,
        "X-WEBAUTH-ROLE": role,
        "Cache-Control": "no-store",
    }
    if Approval.owed_by(user):
        headers["X-Preview-Datasource"] = PREVIEW_DATASOURCE

    return HttpResponse(headers=headers)
