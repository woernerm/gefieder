"""Project-level views: the ones that belong to no app."""


def error_logging_probe(request):
    """Raise, so the integration suite can watch the traceback arrive in the journal.

    Only a real request reaches the server process, whose output journald captures.
    """
    raise RuntimeError(f"error logging probe: {request.GET.get('marker', 'no marker')}")
