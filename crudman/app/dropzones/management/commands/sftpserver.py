import asyncio
import logging

from django.conf import settings
from django.core.management.base import BaseCommand

from ...sftp import serve


class Command(BaseCommand):
    help = "Run the dropzones SFTP endpoint (see dropzones/sftp.py)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--port",
            type=int,
            default=settings.SFTP_PORT,
            help="Port to listen on (default: the SFTP_PORT setting).",
        )

    def handle(self, *args, **options):
        # No timestamp in the format: the entrypoint prefixes one on every line, and
        # the persistent log must carry exactly one. asyncssh's own logger narrates
        # every channel at INFO, so it is kept to warnings.
        logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
        logging.getLogger("asyncssh").setLevel(logging.WARNING)
        asyncio.run(serve(options["port"]))
