"""The endpoint JupyterHub authenticates its visitors against.

The hub has no directory of its own. It asks here, carrying the visitor's own crudman
session cookie, and gets back who they are and what rank they hold -- so there is one set
of accounts, one sign-in, and single sign-on reaches the notebooks without being configured
a second time.

It lives under ``/<CRUDMAN_PATH>/``, which the proxy forwards, so a browser could otherwise
reach it as well. It answers only requests that did not come through the proxy.
"""
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from .utils import issue_notebook_credential, may_use_notebooks, refusal

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

    The hub calls GET to authenticate a visitor and POST just before it spawns their
    server. Both act on the session's own user and nothing else, so the hub holds no
    credential that would let it ask about anybody else.

    Answered only for a request that did not come through the proxy, which means the hub
    rather than a browser: rotating the credential invalidates the notebook a person has
    open, so a page on another site must not be able to trigger it with their cookie. That
    is also what makes exempting CSRF safe -- the caller is the hub, which has no form and
    no token to send.

    Args:
        request: The HTTP request, carrying the visitor's crudman session cookie.

    Returns:
        401 when the cookie names nobody, 403 when they hold too low a rank, and otherwise
        the identity as JSON -- with the database role and password added on POST.
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
            login, password, role = issue_notebook_credential(user)
        except ValueError as error:
            return JsonResponse({"detail": str(error)}, status=409)
        identity |= {"db_user": login, "db_password": password, "db_role": role}

    # No-store: the credential must not survive in any cache between here and the hub.
    return JsonResponse(identity, headers={"Cache-Control": "no-store"})
