"""Persistent logging: every service logs to stdout/stderr.

podman forwards the stream to journald, so the log survives a crash, a restart and the
container being replaced.

This is the behaviour CLAUDE.md requires: the services log to the journal, which persists
and rotates them, rather than to a file on a data volume. Reading a service's log needs no
`podman unshare`, because journalctl reads the journal rather than a file owned by a user
inside the container's namespace."""
import re
import subprocess
import time
from datetime import datetime, timedelta

import pytest

from conftest import CRUDMAN_PATH, LOGGING_UNITS

# A line is considered timestamped if it carries a calendar date and clock time somewhere
# in it. This matches journald's own ISO-8601 stamp as well as the formats the services
# emit themselves: grafana's mid-line "t=2026-06-25T22:31:05", gunicorn's bracketed
# "[2026-06-25 22:31:05 +0000]" and nginx's "2026/06/25 22:31:05".
TIMESTAMP = re.compile(r"\d{4}[-/]\d{2}[-/]\d{2}[ T]\d{2}:\d{2}:\d{2}")

# journald's own stamp, which "-o short-iso" puts at the start of every line. Anchored, so
# it identifies the prefix rather than just finding a date. Its offset carries a colon
# ("+02:00"), unlike the bare form journalctl's other output modes use.
JOURNAL_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}\s")

# What journalctl puts between its stamp and the service's own text: "<host> <unit>[<pid>]:".
SYSLOG_HEADER = re.compile(r"^\S+ \S+?\[\d+\]: ")

# The services that stamp their own records on top of journald's: whether that stamp names
# a zone, and why the service cannot be configured out of it. CLAUDE.md allows the second
# stamp only on that condition, so one that merely *was* configured to add it belongs in a
# fix, not here. PostgreSQL is listed for a remnant only: its steady-state log lost its
# stamp with %m, and what is left is the base image's bootstrap, which runs before gf_0001.
# test_postgresql_shall_not_add_its_own_timestamp guards the part that is ours.
#
# The zone flag is what the "must not contradict" rule turns on: a stamp naming its zone is
# unambiguous however it is set, while one that does not is read as the host's — true only
# when the container runs on the host's clock, hence Timezone=local on those quadlets.
SELF_TIMESTAMPING = {
    # unit: (its own stamp names a zone, why it cannot be configured away)
    "postgresql": (True, "the base image's bootstrap logs 'UTC' before gf_0001 runs"),
    "crudman": (True, "gunicorn's error-log format is fixed"),
    "sqlmesh": (False, "SQLMesh formats its records with a hardcoded module constant"),
    "grafana": (True, "every Grafana log format puts t= inside the line, never first"),
    "proxy": (False, "nginx's error_log format is fixed"),
}

# How far a service's own stamp may sit from journald's before the two contradict. They
# mark emission and reception, so they differ slightly in the ordinary case; the failure
# this guards against is a whole timezone.
TIMESTAMP_TOLERANCE = timedelta(minutes=2)

# The self-stamping formats. Group 1 is the date and time; group 2, where the format names
# a zone, is that zone. Mutually exclusive: each is anchored on punctuation only its own
# producer emits.
OWN_TIMESTAMP_FORMATS = (
    # gunicorn: "[2026-08-05 22:50:02 +0200]"
    (re.compile(r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) ([+-]\d{4})\]"), True),
    # grafana: "t=2026-08-05T22:49:54.042764214Z"
    (re.compile(r"t=(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.\d+)?(Z|[+-]\d{2}:?\d{2})"),
     True),
    # nginx: "2026/08/05 22:50:17"
    (re.compile(r"(\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2})"), False),
    # SQLMesh: "2026-08-05 22:49:58,688"
    (re.compile(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+"), False),
)


def _own_timestamp(message, host_zone):
    """The timestamp a service put in its own record, or None if it wrote none.

    A format naming no zone is read in host_zone — the assumption `Timezone=local` exists
    to make true, since on UTC the same text means a different moment.
    """
    for pattern, names_zone in OWN_TIMESTAMP_FORMATS:
        found = pattern.search(message)
        if not found:
            continue
        moment = datetime.strptime(
            found.group(1).replace("/", "-").replace("T", " "), "%Y-%m-%d %H:%M:%S"
        )
        if not names_zone:
            return moment.replace(tzinfo=host_zone)
        zone = found.group(2)
        # "Z" and the compact "+0200" both have to reach fromisoformat as "+02:00".
        if zone == "Z":
            zone = "+00:00"
        elif ":" not in zone:
            zone = f"{zone[:3]}:{zone[3:]}"
        return datetime.fromisoformat(f"{moment.isoformat()}{zone}")
    return None


def _message_body(line):
    """What the service itself wrote: the line without journald's stamp and syslog header.

    None when the line does not start with journald's stamp, which means it is the
    continuation of a multi-line entry — journalctl prints those raw, so there is no
    header to strip and no record of their own for a stamp to belong to.
    """
    stamp = JOURNAL_TIMESTAMP.match(line)
    if not stamp:
        return None
    return SYSLOG_HEADER.sub("", line[stamp.end():])


def _added_timestamp(body):
    """The timestamp the service put in its own message, or None if it added none.

    Two channels, because a logging library places its stamp either way. One at the very
    start is what a prefixing format produces (nginx, SQLMesh, gunicorn behind its bracket,
    and what PostgreSQL's %m did); one further in is matched only against the formats those
    libraries actually emit.

    Deliberately not "any date anywhere in the message": a log line may quote a timestamp
    belonging to the data — an upload's validity period, a file name carrying one — and
    that is content, not a second stamp on the record.
    """
    leading = TIMESTAMP.match(body.lstrip("["))
    if leading:
        return leading.group(0)
    for pattern, _ in OWN_TIMESTAMP_FORMATS:
        found = pattern.search(body)
        if found:
            return found.group(0)
    return None


def journal(unit, *args):
    """Return the journal of one service's systemd unit.

    short-iso rather than the default format, whose syslog-style "Jul 31 14:37:43" stamp
    carries no year and so cannot place a line in time on its own.
    """
    out = subprocess.run(
        ["journalctl", "--user", "-u", f"{unit}.service", "--no-pager", "-o", "short-iso",
         *args],
        capture_output=True, text=True, check=True,
    )
    return out.stdout


def _container_lines(unit):
    """The service's own output since the unit last started.

    Two filters, both needed to assert anything about a service's log format. `-t <unit>`
    keeps the container's stdout and drops the systemd and podman records that also land in
    a unit's journal: those are the platform talking *about* the service, and podman's
    event lines carry a stamp no service setting reaches. `--since` the last start keeps
    the assertion about the code running now, since the journal outlives a release.
    """
    # InactiveExitTimestamp is when the unit began starting, not ActiveEnterTimestamp,
    # which is when it finished: the units gate "active" on their healthcheck, so anything
    # logged while coming up — all of gunicorn's output, as it goes quiet once serving --
    # falls before that mark.
    started = subprocess.run(
        ["systemctl", "--user", "show", "-p", "InactiveExitTimestamp", "--value",
         f"{unit}.service"],
        capture_output=True, text=True,
    ).stdout.split()
    # "Wed 2026-08-05 22:49:50 CEST" -> "2026-08-05 22:49:50"; journalctl does not take
    # the weekday or the zone name. An unparsable value means no window rather than a
    # failure, which only ever widens what is checked.
    since = ["--since", " ".join(started[1:3])] if len(started) >= 3 else []
    out = subprocess.run(
        ["journalctl", "--user", "-u", f"{unit}.service", "-t", unit, "--no-pager",
         "-o", "short-iso", *since],
        capture_output=True, text=True, check=True,
    ).stdout
    return [ln for ln in out.splitlines() if ln.strip() and not ln.startswith("-- ")]


def _journal_lines(unit, timeout=60):
    """The unit's journal lines, waiting for the first one to appear.

    Polled, because some services (grafana, postgresql) emit their first line only after
    the HTTP/DB readiness the suite already waits for.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        lines = [ln for ln in journal(unit).splitlines() if ln.strip()]
        # journalctl prints a "-- No entries --" placeholder when the unit logged nothing.
        lines = [ln for ln in lines if not ln.startswith("-- ")]
        if lines:
            return lines
        time.sleep(2)
    return []


class TestPersistentLogs:
    """Each service's log is captured by journald."""

    @pytest.mark.parametrize("unit", LOGGING_UNITS)
    def test_each_service_shall_write_a_persistent_log(self, unit):
        assert _journal_lines(unit), f"{unit} wrote nothing to its journal"

    def test_a_restart_shall_not_lose_the_persistent_log(self):
        # The journal belongs to the unit, not the container, so a restart appends to it
        # rather than starting over — the point of the whole arrangement: a crashed
        # container's output is still there afterwards to diagnose it from.
        before = len(_journal_lines("crudman"))
        subprocess.run(
            ["systemctl", "--user", "restart", "crudman.service"], check=True,
        )
        deadline = time.time() + 60
        while time.time() < deadline and len(_journal_lines("crudman")) <= before:
            time.sleep(2)
        after = len(_journal_lines("crudman"))
        assert after >= before, "the journal lost lines across the restart"
        assert after > before, "the restart appended nothing to the journal"

    def test_the_log_shall_be_readable_without_unshare(self):
        # journalctl --user reads the journal directly, so no `podman unshare` is needed
        # to get at a log written by a non-root user inside a container.
        assert _journal_lines("postgresql"), "postgresql's journal is not readable"


class TestLogTimestamps:
    """One timestamp per message, at the beginning of it.

    journald records one per entry, which is why it is always first. A service that stamps
    its own as well puts a second date on the line, which invites the two to disagree — and
    a disagreement is what misleads someone reading a log during an incident. The rule is
    enforced where a service can be configured out of it; SELF_TIMESTAMPING lists the rest.

    That journald's stamp comes *first* is not tested: "-o short-iso" prints it as a prefix
    on every line by construction, so the assertion would describe journalctl's formatter
    rather than anything this repository controls — and it would fail on a multi-line entry,
    whose continuation lines journalctl prints raw. What is ours is whether the service adds
    a stamp of its own, which is what the tests below check.
    """

    @pytest.mark.parametrize("unit", LOGGING_UNITS)
    def test_every_log_line_shall_carry_a_timestamp(self, unit):
        lines = _journal_lines(unit)
        assert lines, f"{unit} wrote nothing to its journal"
        missing = [ln for ln in lines if not TIMESTAMP.search(ln)]
        assert not missing, (
            f"{unit}'s journal has {len(missing)} line(s) without a timestamp, "
            f"e.g.: {missing[0]!r}"
        )

    @pytest.mark.parametrize(
        "unit", [u for u in LOGGING_UNITS if u not in SELF_TIMESTAMPING]
    )
    def test_no_second_timestamp_shall_be_added(self, unit):
        # Only the services that can be configured out of it are checked; SELF_TIMESTAMPING
        # holds the rest, and each of those is covered by the contradiction test below.
        lines = _container_lines(unit)
        assert lines, f"{unit} wrote nothing to its journal"
        doubled = []
        for line in lines:
            body = _message_body(line)
            if body is None:
                continue
            found = _added_timestamp(body)
            if found:
                doubled.append((found, line))
        assert not doubled, (
            f"{unit} adds a second timestamp to {len(doubled)} line(s) on top of "
            f"journald's, first {doubled[0][0]!r} in: {doubled[0][1]!r}"
        )

    # The units whose steady-state log carries a second timestamp in a readable format.
    # postgresql is left out: its only self-stamped lines are the base image's bootstrap,
    # which spells the zone as a name ("UTC") rather than an offset.
    @pytest.mark.parametrize("unit", ["crudman", "sqlmesh", "grafana", "proxy"])
    def test_a_second_timestamp_shall_not_contradict_the_journal(self, unit):
        compared, disagreeing = 0, []
        for line in _container_lines(unit):
            body = _message_body(line)
            if body is None:
                continue
            journal_time = datetime.fromisoformat(
                JOURNAL_TIMESTAMP.match(line).group(0).strip()
            )
            own = _own_timestamp(body, journal_time.tzinfo)
            if own is None:
                continue
            compared += 1
            if abs(own - journal_time) > TIMESTAMP_TOLERANCE:
                disagreeing.append((own - journal_time, line))
        # Without this, a parser that stopped matching passes as silently as a service
        # that behaved.
        assert compared, f"no second timestamp of {unit}'s was recognised to compare"
        assert not disagreeing, (
            f"{unit}'s own timestamp contradicts journald's on {len(disagreeing)} of "
            f"{compared} line(s), the first by {disagreeing[0][0]}: {disagreeing[0][1]!r}"
        )

    def test_postgresql_shall_not_add_its_own_timestamp(self, admin_db):
        # The half of PostgreSQL's format that is ours, asserted at the source rather than
        # by counting lines: no time escape in log_line_prefix, so every record after
        # startup is stamped once. %m is the one this replaced, %t the same at second
        # resolution.
        with admin_db.cursor() as cur:
            cur.execute("SHOW log_line_prefix")
            prefix = cur.fetchone()[0]
        assert "%m" not in prefix and "%t" not in prefix, (
            f"log_line_prefix adds a timestamp of its own: {prefix!r}"
        )


class TestApplicationErrors:
    """An unhandled exception reaches the journal.

    Django's own default routes django.request through the "mail_admins" handler alone once
    DEBUG is off, and adds a console handler only while DEBUG is on. A production stack with
    no ADMINS address therefore swallows every 500: the user sees the error page, and the
    traceback that says what happened is discarded. Nothing else records it either, since
    Django catches the exception and gunicorn only ever sees the 500 response it returns.

    That is the failure this asserts against, because it is invisible until the day someone
    reports an error and there is nothing to read.

    The request goes over HTTP rather than through "podman exec", which would prove nothing:
    podman forwards only the container's main process to journald, so an exec's output never
    reaches the journal however logging is configured. Only a real request runs inside the
    gunicorn workers whose stream is captured.
    """

    def test_an_unhandled_exception_shall_reach_the_journal(self, http):
        # Unique per run, so the assertion cannot pass on an entry left by a previous one.
        marker = f"gf-logging-probe-{int(time.time())}"
        resp = http.get(
            f"/{CRUDMAN_PATH}/error-logging-probe/", params={"marker": marker}
        )
        assert resp.status_code == 500, (
            f"the probe route answered {resp.status_code}; ERROR_LOGGING_PROBE is not set "
            "for this stack, so the test would pass without testing anything"
        )

        deadline = time.time() + 60
        while time.time() < deadline:
            log = journal("crudman")
            if marker in log:
                break
            time.sleep(2)
        else:
            pytest.fail(
                f"crudman's journal has no record of the 500 raised for {marker}; the "
                "traceback is being discarded"
            )

        # The message alone would not say what went wrong, which is the point of keeping
        # the log: the traceback is what names the line that raised.
        assert "Traceback" in log and "RuntimeError" in log, (
            f"{marker} was logged without its traceback, so the log does not say what "
            "raised"
        )

    def test_the_request_that_failed_shall_be_identifiable(self, http):
        """The access log says which request the traceback belongs to.

        gunicorn ships with its access log off, which leaves a report of "an error around
        14:30" with nothing to match against: the traceback names the code but not the URL,
        the user or the time the request arrived.
        """
        marker = f"gf-access-probe-{int(time.time())}"
        http.get(f"/{CRUDMAN_PATH}/error-logging-probe/", params={"marker": marker})

        deadline = time.time() + 60
        while time.time() < deadline:
            log = journal("crudman")
            if "error-logging-probe" in log and " 500 " in log:
                return
            time.sleep(2)
        pytest.fail(
            "crudman's journal has no access-log line for the failing request, so a "
            "reported error cannot be tied to the request that caused it"
        )
