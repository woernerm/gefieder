# Requirements for the "dropzones" django app.

The "dropzones" app offers convenient ways for users and external tools to upload
source data files. Massive historical data is still written directly to the database;
dropzones are the entry point for everything else: hand-maintained mappings, exports
from other systems, files agreed upon with their producers.

# Models
- There shall be a model `Dropzone`. It defines the purpose of a set of files, the
  expected file format, who may upload (by user or by secret URL) and by what method
  (browser upload, POST to an API endpoint, sftp — the browser upload and the API
  endpoint are implemented; sftp must feed the same pipeline later).
- Every dropzone has an unguessable token that forms its upload URL, so a secret link
  can be handed to someone without creating an account.
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
- Both run immediately after upload, before anything is stored. Any exception rejects
  the upload; the message is shown to the uploading user and no files are kept.
- The converter receives the uploaded files and an output directory and returns the
  files to store — the same files (no conversion) or entirely different ones
  (e.g. Excel in, Parquet out).

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
  fields mean exactly what they do on the browser form. Success returns 201 with the
  upload id, file count and hash as JSON; a rejected upload returns 400 with the
  checker/converter message. Disabled dropzones and non-API methods answer 404.
- Authentication: an unattended client cannot hold a session, so the dropzone's
  `api_token` (an `Authorization: Bearer` header) stands in for a login. A dropzone
  that requires a login must have a token set and matched; without a login requirement
  an empty token leaves the endpoint open to anyone holding the secret URL. API uploads
  are recorded with no user, like a secret-link browser upload.
