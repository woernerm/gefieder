"""The ready-to-run upload examples shown on a dropzone's admin page.

One template per upload method, each a complete client an uploader can copy and run
after replacing the example payload with their own. The placeholders are filled from
the dropzone when the field is rendered (see ``Dropzone.upload_example``), so an
example always carries that dropzone's real address, name and token.

Placeholders, all optional per template:

``{url}``       the full upload URL (browser, API and webhook dropzones)
``{address}``   the SFTP or Arrow Flight address
``{name}``      the dropzone's name, which is the SFTP and Arrow Flight login
``{secret}``    the dropzone's secret, or a ``<secret>`` marker while none is set
``{port}``      the SFTP or Arrow Flight port
``{host}``      the server name

The examples are executed by the test suite against a live dropzone, so a template that
stops matching its endpoint fails the build rather than misleading an uploader. Keep
them runnable as they stand: no shell variables to fill in, no pseudo-code.
"""

# The browser upload has no client to write — the link is the whole interface — so the
# example only says what to do with it.
BROWSER = """\
Open the upload link in a browser and drop the file(s) onto the page:

    {url}

The page asks how long the upload is valid and shows the result of the file check
right away, so a rejected upload can be fixed and sent again immediately."""

API = """\
# Upload one or more files with a single POST. Repeat -F files=@... per file;
# everything sent in one request becomes one upload.
curl -X POST \\
  -H "Authorization: Bearer {secret}" \\
  -F "files=@yourfile.csv" \\
  {url}"""

WEBHOOK = """\
# Send readings as query parameters; each call stores one row as a CSV file.
# For a device that cannot send headers, leave the dropzone's secret empty and
# drop the -H line: the secret token in the URL then authorizes the call.
curl -H "Authorization: Bearer {secret}" \\
  "{url}?temperature=21.5&humidity=48"\
"""

SFTP = """\
# Log in with the dropzone's name and secret, put the file(s), disconnect.
# Everything transferred in one session becomes one upload.
sftp -P {port} {name}@{host}
# password: {secret}
sftp> put yourfile.csv
sftp> put anotherfile.csv
sftp> bye"""

# Arrow Flight sends tables rather than files, so the example is a real client: the
# bearer token from the login ties the DoPuts together and the commit stores them.
FLIGHT = """\
import pyarrow as pa
import pyarrow.flight as fl

client = fl.connect("{address}")
options = fl.FlightCallOptions(headers=[
    client.authenticate_basic_token(b"{name}", b"{secret}")])

# Each table becomes one parquet file named after it. Use df.to_arrow() for a
# polars frame, or con.sql(...).to_arrow_table() for a duckdb result.
tables = {{"issues": pa.table({{"id": [1, 2], "state": ["open", "done"]}}),
          "commits": pa.table({{"sha": ["a1", "b2"]}})}}

for table_name, table in tables.items():
    writer, _ = client.do_put(
        fl.FlightDescriptor.for_path("{name}", table_name), table.schema, options)
    writer.write_table(table)
    writer.close()

# Nothing is stored until the commit: a client that dies before it stores nothing.
client.do_action(fl.Action("commit", b""), options)"""

# The template each upload method is shown with, keyed by Dropzone.Method values. Kept
# as plain strings rather than a dict of callables so a template can be read, tested and
# copied on its own.
TEMPLATES = {
    "browser": BROWSER,
    "api": API,
    "webhook": WEBHOOK,
    "sftp": SFTP,
    "flight": FLIGHT,
}
