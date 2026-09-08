"""Where a developer's checkout fetches a database password.

The one endpoint a laptop talks to. It presents the token its owner created in the admin
panel and gets back a password that expires, so nothing standing is kept on a machine this
system does not run on. The token is not itself a database credential: it does this and
nothing else, and switching Database access off takes it back.
"""
from datetime import timedelta

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from .utils import issue_password, user_for_token

LIFETIME = timedelta(hours=12)
"""How long a fetched password lasts, matching a notebook's."""


@csrf_exempt
@require_http_methods(["POST"])
def password(request):
    """Exchange a token for a short-lived password on its owner's own role.

    No session and no CSRF token: the caller is a command-line tool, and the bearer of the
    token is the only thing that authenticates it -- which is why the token is worth no
    more than the database access it stands for.

    Args:
        request: The HTTP request, carrying "Authorization: Bearer <token>".

    Returns:
        401 when the token names nobody, 409 when they have no usable account, and
        otherwise the role, the password and when it expires.
    """
    header = request.headers.get("Authorization", "")
    token = header[7:].strip() if header.lower().startswith("bearer ") else ""

    user = user_for_token(token)
    if user is None:
        return JsonResponse({"detail": "unknown or revoked token"}, status=401)

    try:
        role, secret, expires_at = issue_password(user, LIFETIME)
    except ValueError as error:
        return JsonResponse({"detail": str(error)}, status=409)

    return JsonResponse(
        {"db_user": role, "db_password": secret, "expires_at": expires_at.isoformat()},
        # The credential must not survive in any cache between here and the laptop.
        headers={"Cache-Control": "no-store"},
    )

