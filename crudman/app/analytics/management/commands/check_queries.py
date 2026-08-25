"""Asserting that the stored queries still return what their panels expect.

This is the answer to "how do you test a metric". A query is a row with a signature and
a set of checks, so it can be run on its own, away from any chart: the command below
executes every query with its own defaults and reports the ones whose result no longer
matches what was recorded. A gold column renamed upstream fails here, in CI, rather than
silently blanking a panel nobody is looking at.

Unlike the signature probe, which folds the statement away and costs nothing, this runs
the queries for real -- so it belongs in a pipeline or a cron, not in ``save()``.
"""

from django.core.management.base import BaseCommand, CommandError

from analytics.models import Query
from analytics.parameters import ParameterError
from analytics.query import PanelQueryError, run


class Command(BaseCommand):
    help = "Run every stored query and check its result against the query's checks."

    def add_arguments(self, parser):
        parser.add_argument(
            "titles",
            nargs="*",
            help="Only these queries, by title. Every query when omitted.",
        )
        parser.add_argument(
            "--refresh-signature",
            action="store_true",
            help="Also re-probe and store each query's columns.",
        )

    def handle(self, *args, **options):
        queries = Query.objects.all()
        if options["titles"]:
            queries = queries.filter(title__in=options["titles"])

        failures = 0
        for query in queries:
            problems = list(self._check(query, options["refresh_signature"]))
            for problem in problems:
                self.stderr.write(self.style.ERROR(f"FAIL {query.title}: {problem}"))
            failures += len(problems)
            if not problems:
                self.stdout.write(self.style.SUCCESS(f"ok   {query.title}"))

        if failures:
            raise CommandError(f"{failures} check(s) failed.")

    def _check(self, query, refresh_signature):
        """Every way this one query disagrees with what was recorded.

        Args:
            query: The Query to run.
            refresh_signature: Whether to store the columns it returned.

        Yields:
            One message per problem found; nothing at all when the query is sound.
        """
        try:
            columns, rows = run(query.sql, query.parameter_defaults or {})
        except (PanelQueryError, ParameterError) as error:
            yield str(error)
            return

        if refresh_signature and query.probe():
            # Written straight to the row: save() would probe a second time.
            Query.objects.filter(pk=query.pk).update(signature=query.signature)

        checks = query.checks or {}

        expected = checks.get("columns")
        if expected and list(columns) != list(expected):
            yield f"columns are {', '.join(columns)}; expected {', '.join(expected)}"

        minimum = checks.get("min_rows")
        if minimum is not None and len(rows) < minimum:
            yield f"returned {len(rows)} rows; expected at least {minimum}"

        for name in checks.get("not_null") or []:
            if name not in columns:
                yield f"not_null names {name}, which the query does not return"
                continue
            index = list(columns).index(name)
            blanks = sum(1 for row in rows if row[index] is None)
            if blanks:
                yield f"{name} is null in {blanks} of {len(rows)} rows"
