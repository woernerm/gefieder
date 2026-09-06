# system — administering the system itself

What an administrator changes about the platform rather than about the data in it: which
version of the analytics models runs. It sits in a section of its own in the admin because
it does not belong on the documentation pages, which are open from the viewer rank up.

Deploying a version was previously a page of its own beside the documentation. It is an
admin page now: every rank is staff, so the documentation's viewer-and-up rule would have
let anyone who may read a metric also change what production computes.


## Model versions

### Why this exists

The SQLMesh project used to be baked into the engine image. Shipping a model therefore
meant a release: a push to `main`, a CI build of every image, a download of the release on
the server and a restart of the stack. Minutes of waiting, several of them needing someone
logged in, for a change that is a few lines of SQL.

That cost is not inherent. A model is not compiled, and SQLMesh promotes a plan by swapping
views rather than by moving data, so applying one takes seconds. The only thing the release
cycle really provided was provenance: an assurance that what production computes
corresponds to a commit somebody can point at.

So the models leave the image and live in a git repository the system checks out. Provenance
is kept, because a deployment *is* a commit; the wait is not, because nothing is built.

### What it must do

- **Keep a repository, always.** `REPO_MODELS` names it. The default is a bare repository
  crudman creates on the models volume and seeds with the project this release ships, as
  one commit per example project rather than one commit for everything — so a fresh
  installation has versions to move between before anybody has pushed, and each of them is
  a smaller working system rather than a broken one. A fresh installation still needs one
  command and no git host. Set it to a git host and that
  host is the origin instead. An empty value is refused: with no origin at all the working
  tree would be the only copy of the history, and nothing would say so.
- **Deploy what `main` points at**, within `MODELS_POLL_INTERVAL` seconds of a push, without
  anyone logging in to the server.
- **Let a person put an earlier version back**, and leave that choice standing until someone
  pushes. The next push then supersedes it, so there is no pin to remember to clear.
- **Refuse a commit the installed engine cannot run**, before the working tree changes.
  Dependencies are installed when the images are built, so a commit that edits the project's
  `pyproject.toml` needs a new release; checking it out would leave the engine failing on an
  import with nothing running.
- **Survive an unreachable origin.** The deployed tree keeps running and the next pass
  retries.
- **Report what happened**, in the words a person can act on: the plan's own output when it
  fails, and the reason when a deployment was refused.
- **Describe what is running.** The engine exports the model documentation as part of
  applying a commit, so the documentation pages follow the deployment rather than the
  release.
- **Ask for the rank that may change things.** The page replaces the changelist of the
  deployment model, so it is behind the admin's sign-in; `has_view_permission` then narrows
  it further, because a viewer is staff too and their business is the data rather than the
  machinery.

### How it is arranged

crudman is the only writer of the volume. It clones into `deployed/` and checks out a commit
there, detached, then writes `deployed.sha`. The engine mounts the same volume read-only,
watches that one file and plans when it changes -- so the marker is both the answer to "what
is deployed" and the signal that it is safe to read: a checkout in progress has not written
it yet. Reading it needs no git, which is why the engine image has none.

`workspaces/` is deliberately empty and deliberately separate. Deploying a version rewrites
`deployed/` wholesale, so nobody's uncommitted work may live there. Anything that later lets
a person edit models on this server gets a tree of its own here and reaches production the
same way everything else does: by pushing a commit.

The repository's own layout puts the SQLMesh project in a `sqlmesh/` subdirectory rather than
at the root, so what belongs beside the models -- dashboards, notebooks, whatever a later
version keeps with them -- has somewhere to go without moving anything.

### What it deliberately does not do

**It does not host git.** The default origin is a repository on this volume, reachable from
inside the container and nowhere else, which the versions page says plainly. Serving it over
the network means authentication, authorisation, branch protection and hooks -- a body of
work with its own risks, and one that changes nothing for an installation whose developers
already have a git host. It is additive when it is wanted: the bare repository is already
there.

**It does not install dependencies at deploy time.** It could, and then a dependency change
would deploy like any other. It would also mean this server needs a package index reachable
at runtime, which the rest of this template goes out of its way to avoid -- the DuckDB
extensions and Grafana plugins are baked in precisely so a deployment works offline.
Refusing the commit with a clear reason keeps that property.

**It does not gate on the plan succeeding.** A plan needs the database and can fail for
reasons that have nothing to do with the commit, so a failure is reported rather than
treated as a rejected change. Recovery is another deployment, which takes seconds.

**It does not protect production from a bad model.** Any push to `main` reaches production
within the poll interval. That is the point, and the control that replaces the old wait is
that a revert deploys just as fast: promotion is a view swap in both directions.

