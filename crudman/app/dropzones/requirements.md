# Requirements for the "dropzones" django app.

The "dropzones" app offers convenient ways for users and external tools to upload
source data files. Massive historical data is still written directly to the database;
dropzones are the entry point for everything else: hand-maintained mappings, exports
from other systems, files agreed upon with their producers.

# Models
- There shall be a model `Dropzone`. It defines the purpose of a set of files, the
  expected file format, who may upload (by user or by secret URL) and by what method
  (browser upload, POST to an API endpoint, SFTP, webhook GET — all four feed the
  same pipeline).
- Every dropzone has an unguessable token that forms its upload URL, so a secret link
  can be handed to someone without creating an account.
- A dropzone has exactly one upload method, so one `secret` field serves as the machine
  credential of whichever method needs one: the Bearer token of an API or webhook
  dropzone or the SFTP password of an SFTP dropzone.
- A dropzone carries a `default_validity`: preselected on the browser upload page,
  applied to API uploads that send no validity and to SFTP and webhook uploads (which
  cannot send one). "Valid for a given time period" needs dates from the uploader, so
  it is only allowed as default for browser dropzones.
- There shall be a model `Upload` keeping the metadata of each upload: upload time,
  uploading user (empty for secret-link uploads), validity start and end date, the
  directory of the stored files (relative to the uploads volume, using the dropzone's
  primary key so the path stays immutable when a dropzone is renamed) and one sha256
  hash covering all files as uploaded (order-independent).
- One upload consists of one or more files; each stored file is an `UploadFile` row
  with a `FileField`. Deleting rows removes the files from the volume.

# Validity
- The uploading user selects one validity period per set of files:
  "always valid" (no bounds), "valid until replacement" (start defaults to the upload
  time, open end) or a fixed period (both bounds, end after start).
- When a new upload with a concrete start arrives, previously open-ended uploads of the
  same dropzone that started earlier are shortened to end at the new upload's start.
  "Always valid" uploads are never clipped; overlaps are resolved newest-first.
- Analytics code finds the files valid at a timestamp with one query over
  `crudman.dropzones_upload` joined with `crudman.dropzones_uploadfile`
  (one row per directory and file name; see the `Upload` model docstring), reading the
  files from the uploads volume, which the sqlmesh container mounts read-only at the
  same path.

# Check and convert functions
- Python functions for error checking and for converting the uploaded files live in a
  designated folder (`dropzones/functions/`, named by the `FUNCTIONS_PACKAGE` constant
  in `dropzones/registry.py`) and are autodiscovered at startup via the
  `@checker`/`@converter` decorators. New functions require a rebuilt image.
- Default functions ship in `functions/default.py`, Polars-based: the
  `reject_empty_files` checker and the `csv_to_parquet`, `excel_to_parquet` (every
  sheet becomes its own Parquet file, named after the source file and the sheet) and
  `json_to_parquet` converters. They are usable as-is and double as templates for
  custom functions.
- Both run immediately after upload, before anything is stored. Any exception rejects
  the upload; the message is shown to the uploading user and no files are kept.
- The converter receives the uploaded files and an output directory and writes the
  files to store into it (e.g. Excel in, Parquet out); everything found there
  afterwards is stored. Without a converter, the files are stored as uploaded.

# Browser upload
- The upload page is a self-contained drag-and-drop form (multiple files at once)
  under the dropzone's secret URL, below CRUDMAN_PATH so the proxy needs no extra
  route. Disabled dropzones and non-browser methods answer 404 like unknown tokens.
- Access: without `require_login`, anyone with the link may upload; with it, any
  logged-in user, or only the listed users if some are listed (superusers always).

# API upload
- Dropzones with the API method take a `multipart/form-data` POST at
  `api/<token>/` (alongside the browser page, below CRUDMAN_PATH). The files travel
  under the `files` field; the optional `validity`, `valid_from` and `valid_until`
  fields mean exactly what they do on the browser form, and a POST without a
  `validity` gets the dropzone's default validity. Success returns 201 with the
  upload id, file count and hash as JSON; a rejected upload returns 400 with the
  checker/converter message. Disabled dropzones and non-API methods answer 404.
- Authentication: an unattended client cannot hold a session, so the dropzone's
  `secret` (an `Authorization: Bearer` header) stands in for a login. A dropzone
  that requires a login must have a secret set and matched; without a login requirement
  an empty secret leaves the endpoint open to anyone holding the secret URL. API
  uploads are recorded with no user, like a secret-link browser upload.

# Webhook upload
- Dropzones with the webhook method are made for devices that can only call a URL with
  measured values substituted into it (e.g. a Shelly relay reporting a temperature):
  an HTTP GET at `webhook/<token>/` (below CRUDMAN_PATH like the other web routes)
  whose query parameters are the payload. Each call becomes one upload holding a
  one-row CSV file — the parameter names as the sorted header, the values as the data
  row — which runs through the same pipeline as every other method (a converter like
  `csv_to_parquet` may turn it into Parquet). Success returns 201 with the upload id
  and hash as JSON; a rejected call returns 400 with the message.
- Hygiene: parameter names are restricted to letters, digits and underscores (they
  become column names downstream); duplicate names, more than 100 parameters, values
  longer than 1000 characters and calls without parameters are rejected.
- Authentication is exactly the API endpoint's: the dropzone's `secret` as an
  `Authorization: Bearer` header for clients that can send one; without a login
  requirement and secret, the unguessable URL alone authorizes — the fit for devices
  that cannot set headers. Webhook uploads are recorded with no user.
- The validity is the dropzone's default ("until replaced" or "always"), like SFTP.
  With "until replaced", each reading is valid exactly while it is the newest, so the
  canonical validity query answers "the reading in effect at a timestamp".

# SFTP upload
- Dropzones with the SFTP method are served by an SFTP server the application runs itself
  (`manage.py sftpserver` in `dropzones/sftp.py`, run by the `sftp` container and
  published on port 2222). Owning the server is what keeps the uploader's side free of
  conventions: no marker files, no manifest, no rename dance — connect, `put` one or
  more files with any SFTP/scp client, disconnect.
- Authentication: username = the dropzone's name, password = its secret. An SFTP
  dropzone must have a secret (there is no unguessable URL standing in for one), so an
  empty secret rejects every login and the admin form requires one. Disabled and
  non-SFTP dropzones reject logins exactly like unknown names.
- One cleanly disconnected session becomes one upload: every file the client put runs
  through the same pipeline (checker, converter, hash, validity clipping) as one set,
  preserving the multi-file contract of the other methods. A session that ends in a
  connection error stores nothing, like an aborted POST — the uploader's client shows
  the failure and they retry. A checker/converter rejection after the disconnect can
  only be logged (visible in sftp.log), not shown to the uploader.
- Every session is chrooted into its own throwaway directory: uploaders see only their
  own running session, never stored uploads or other dropzones' data.
- The validity is the dropzone's default validity ("until replaced" or "always"; SFTP
  cannot carry dates).
- The server's ed25519 host key is generated on first start and kept on the sftp
  volume, so the server identity survives restarts and updates.
