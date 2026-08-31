"""Project-level views: the ones that belong to no app."""


def error_logging_probe(request):
    """Raise, so the integration suite can watch the traceback arrive in the journal.

    Registered by urls.py only when ERROR_LOGGING_PROBE is set, which the test stack does
    and a real deployment does not. Only a real request reaches the running server
    process, which is what writes to the stream journald captures.
    """
    raise RuntimeError(f"error logging probe: {request.GET.get('marker', 'no marker')}")
