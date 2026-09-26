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
extensions and the notebook extensions are baked in precisely so a deployment works offline.
Refusing the commit with a clear reason keeps that property.

**It does not gate on the plan succeeding.** A plan needs the database and can fail for
reasons that have nothing to do with the commit, so a failure is reported rather than
treated as a rejected change. Recovery is another deployment, which takes seconds.

**It does not protect production from a bad model.** Any push to `main` reaches production
within the poll interval. That is the point, and the control that replaces the old wait is
that a revert deploys just as fast: promotion is a view swap in both directions.

## Approval

### Why this exists

A version reaches production in seconds, which is the point of the section above. What it
leaves out is the one question nobody in this system can answer from the code: whether the
number a metric now produces is the number the business means. That is domain knowledge —
held by the people whose work the data describes, who have no analytics background, often
no git account, and no reason to acquire either.

Every obvious answer makes them pay for the workflow. A pull request asks them to read a
diff, when what they can judge is a chart. A ticket in a tracker asks them to correlate a
version they cannot see with a dashboard they can. A second site asks them to remember it
exists. So the requirement is not "add an approval step": it is that a stakeholder should
be able to answer without learning anything, and that what they answer about should be the
thing itself rather than a description of it.

### What it must do

- **Show them the reviewed version, not tell them about it.** Someone asked to approve a
  version signs in as they always do and their dashboards are that version. They choose
  nothing and switch nothing on.
- **Ask in the words they already have.** Approve, reject, and a box to say why. No commit,
  no branch, no environment: those words appear nowhere a stakeholder can see.
- **Take anyone as an approver.** Who is competent to judge a metric depends on the metric,
  so the people are named per version out of everyone who has signed in — never a rank,
  never a group. This is a deliberate refusal: a standing "approvers" group would answer a
  different question and would be wrong as soon as the second metric came up.
- **End the review by answering it.** The next page they ask for is production again.
  Nothing to switch back, and nothing to leave someone stranded on a preview.
- **Let the version go live only once everyone asked has approved.** The page's deploy
  button is dead until then, and says why.
- **Cost nothing when nobody is reviewing.** No second stack, no copy of the data, no
  second set of dashboards, and no rule an author of a dashboard or a model has to follow.

### How it is arranged

A review is a `Deployment` with `environment = "preview"`: the same row, the same steps,
the same page. SQLMesh plans it into an environment of its own, so **only what the commit
changes is built** and every other table stays production's, behind a view. That is what
makes a preview free, and it is SQLMesh's own mechanism rather than one built here.

The problem that mechanism creates is naming. A plan into an environment writes
`gold__preview`, and a dashboard writes `gold.issue_metrics` — literally, because a panel's
SQL is written by hand and "always leave the schema off" is a convention an author will
forget, silently, in a way that shows them production while they believe they are reviewing.

So the names move instead of the dashboards. A second database, `<PG_DATABASE>_preview`,
holds **nothing but foreign tables** over the first (`postgres_fdw`): one schema per
production schema, named the same, each pointing at the environment's copy where there is
one and at production where there is none. `refresh_preview()` in
`postgresql/initdb/gf_0009` does that in a loop over production's catalog, so a bronze
schema a new project adds is picked up without anything being told about it. A panel's SQL
is then byte-identical in both databases and correct in both.

Which of the two a person reads is one question, asked with every panel: does this person
owe a decision? `dashboards/service.py` names the environment to the dashboards service,
which reads that environment's checkout of the dashboards from its database -- production's
from the first, a review's from `<PG_DATABASE>_<environment>`. Nobody picks a database, no
dashboard carries a variable, and a reviewer reads the reviewed commit's dashboards as well
as its models.

The decision itself is an `Approval` row per person asked, undecided until they answer.
That row is the whole of the mechanism: it is what puts them on the preview, what the bar
draws its Approve/Reject on, what the "Model" stage reads to lead them to the
documentation of the version they are judging rather than to a notebook, and what the
deploy button waits for. There is no review state to keep in step with it.

### Several reviews at once

One at a time, deliberately — it is what lets a stakeholder be put on their review by
signing in, with nothing to choose. Everything underneath is already plural, so lifting it
is additive rather than a rewrite: `environment` is a field, `tree_of()` derives the
checkout and marker from it, `apply()` in `sqlmesh/entrypoint.sh` takes it as an argument,
the preview database is named after it, `refresh_preview()` takes it as an argument, and
the dashboards service connects to the database named after the environment it is asked
for. A second review is a second environment and a second database.

What is *not* free is the person: one session shows one version, so several reviews mean a
chooser in the bar, and with it the "you are simply looking at it" property this design
was built around.

### What it deliberately does not do

**It does not make approval mandatory.** The poll deploys every push to `main` within the
interval, by design, so a gate on the versions page would only be one a push walks around.
A version nobody was asked about therefore still deploys — after a prompt, which is the
difference between doing it and doing it by accident. Making approval binding means taking
the poll away, which is a decision about the whole system rather than about this feature.

**It does not review dashboards apart from the models.** They live in the repository
beside the models, in `dashboards/`, so a commit carries both and a reviewer sees both --
which is the reason the repository has a `sqlmesh/` subdirectory rather than being the
project itself.

**It does not keep a review after it is answered.** The rows stay, so who approved what and
why is on record, but the preview environment is overwritten by the next review. An audit
trail is what the rows are for; a reconstructable past version is what the git history is
for.
