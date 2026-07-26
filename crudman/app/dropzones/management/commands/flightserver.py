import logging

from django.conf import settings
from django.core.management.base import BaseCommand

from ...flight import serve


class Command(BaseCommand):
    help = "Run the dropzones Arrow Flight endpoint (see dropzones/flight.py)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--port",
            type=int,
            default=settings.FLIGHT_PORT,
            help="Port to listen on (default: the FLIGHT_PORT setting).",
        )

    def handle(self, *args, **options):
        # No timestamp in the format: the entrypoint prefixes one on every line, and
        # the persistent log must carry exactly one.
        logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
        serve(options["port"])
