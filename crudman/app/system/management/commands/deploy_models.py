"""Deploy what the branch points at, once or on a loop.

The entrypoint runs it once before serving, so the engine finds a working tree, and then
leaves it looping in the background. A management command rather than a thread in the web
process: it is the same code an operator can run by hand to see why a deployment did not
happen.
"""

import logging
import os
import time

from django.core.management.base import BaseCommand

from system import repo

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Deploy the commit the models branch points at."

    def add_arguments(self, parser):
        parser.add_argument(
            "--loop",
            action="store_true",
            help="Keep polling, at MODELS_POLL_INTERVAL seconds.",
        )

    def handle(self, *args, **options):
        interval = int(os.environ.get("MODELS_POLL_INTERVAL", "20"))

        # Zero is how an operator turns the poll off, leaving the versions page the only
        # way to deploy. Looping every zero seconds would instead be a busy wait.
        if options["loop"] and interval <= 0:
            self.stdout.write("MODELS_POLL_INTERVAL is 0; not polling.")
            return

        while True:
            try:
                if deployment := repo.poll():
                    self.stdout.write(f"Deployed {deployment.short_sha}.")
            except Exception:
                # A git host that is briefly unreachable must not end the loop; the
                # deployed tree keeps running and the next pass tries again.
                logger.exception("Could not deploy the models")

            if not options["loop"]:
                return
            time.sleep(interval)
