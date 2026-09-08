# jupyter — developing the analytics models in a notebook

## Why this exists

Writing a SQLMesh model means running it: seeing the rows it produces, the shape of a
distribution, whether a join doubled the count. Without a notebook that loop runs through a
laptop -- clone the repository, get a database password, install the engine, keep a Python
version matching production's -- which is a day's setup before the first query and a
recurring source of "it works on mine".

A notebook on the server removes all of it. What it must not remove is the discipline that
makes this system auditable: a model is a file in git, and what production computes
corresponds to a commit somebody can point at.

## What it must do

- **Be reachable at `/<NOTEBOOK_PATH>/` with the sign-in that already exists.** A person
  signed in to the admin panel arrives in their own JupyterLab without a second login, and a
  person who is not is sent to the admin's sign-in and back. crudman is the only place
  accounts exist; single sign-on, the ranks and offboarding are configured once, there.
- **Admit an editor and above who has a database account.** Writing models means writing
  the warehouse, which is the line the rest of this system draws between a viewer and an
  editor; the account is the role the notebook's credential is issued on. Both are
  required together, so the link the admin panel offers and the spawn that follows agree --
  a link that then fails is worse than no link. Anyone refused is told which of the two they
  are missing and what to do about it, never the rule that was broken.
- **Give each person their own everything**: Unix account, home, working tree, database
  login. Two people's work, credentials and kernels are separated by the kernel rather than
  by convention, and a query is traceable to a person in `pg_stat_activity` and the
  `server_stats` schema.
- **Never hold a credential of its own.** The hub asks crudman about the caller, presenting
  the caller's own session, so it cannot ask about anybody else. The database login it hands
  a server is rotated at every spawn and stored nowhere.
- **Connect as the person.** A notebook session assumes the person's own role, so
  `current_user` is them: a table a plan creates is owned by them exactly as if they had
  connected from a laptop, and their rank applies without being copied anywhere.
- **Leave the model files alone.** A `.sql` model opens as a notebook and saves as the same
  `.sql`. Opening a model and saving it produces no diff.
- **Ship a model the way everything else ships**: a commit pushed to `REPO_MODELS`, deployed
  by the poll crudman already runs. There is no path from a notebook to production that does
  not go through a commit.

## How it is arranged

One container. JupyterHub authenticates through `crudman/app/notebooks/`, spawns a
`LocalProcessSpawner` per person under a Unix account named exactly as crudman names their
database role -- which is what lets `sqlmesh/config.py` derive the connection with no
notebook-specific branch in it. Each person's working tree is a clone of the models
repository under `workspaces/` on the `models_data` volume, the directory
`crudman/app/system/requirements.md` reserved for it; the deployed tree beside it stays
crudman's alone and is never touched from here.

Everything a server needs is readable by every account: the interpreter uv downloads goes to
`/opt/python` rather than under `/root`, and the environment beside it is world-readable.
Otherwise a spawn fails on a path the error does not name.

**The model file is the notebook.** `sqlnotebook/` answers a `.sql` file requested as a
notebook with its cells and writes the cells back as SQL. It derives from jupytext's own
ContentsManager rather than sitting under it: jupytext builds its class from whichever
manager is configured, and its save refuses an extension it does not know, so a manager
underneath would never see a `.sql` save. On the outside, `.sql` is ours and everything
else falls through to jupytext untouched. Cells are separated by `-- %%`,
a SQL comment, so a file split into cells is still a file the engine parses; a model nobody
has split is one cell, which is the whole file. Prose written above the definition lands
where SQLMesh reads a model's description, so documentation written in the notebook reaches
the documentation pages.

The SQLMesh project is opened for every kernel, from the server's root rather than the
working directory: a notebook created from the launcher starts in the person's home, and
searching upward from there finds nothing. This is configured through IPython's
``exec_files`` -- a kernel never runs a profile's ``startup/`` files, so a loader dropped
there is silently ignored.

A cell is executed by what it starts with: a `MODEL` definition is validated, rendered and
previewed; a bare query is fetched into a dataframe; anything else is Python. So the SQLMesh
magics are there for whoever wants them, and nobody has to type one to work.

## What it deliberately does not do

**It does not keep a second copy of a model.** Notebooks beside the models would mean
generated SQL that must not be hand-edited, a sync step to keep them equal, and a reviewer
reading JSON. Committing the notebook instead would put a JSON blob where the audit trail
should be. One file, viewed two ways, has neither problem -- at the cost of not storing
outputs in a model file, which is what analysis notebooks under `notebooks/` are for.

**It does not rewrite a cell when it runs it.** SQLMesh's own `%%model` magic writes the
cell back to the model's file and reformats it. Here the editor has that file open, so
writing it from underneath would race the editor, and reformatting would produce a diff
nobody asked for. `%format` is there when it is wanted.

**It does not protect production from a notebook.** An editor may `sqlmesh plan prod`, for
the reason `dbusers/requirements.md` records: promoting is a view swap in the same schemas a
developer must write to in order to plan at all. The control is the same one -- production
is normally reached by pushing a commit, and a revert deploys just as fast.

**It does not give a notebook a login of its own.** A server connects as the person's own
role, so `current_user` is them without anything being assumed or granted between two
accounts. What makes that safe to put in a process environment is the expiry: the password
is rotated at every spawn and stops working by itself (`issue_db_user_password` in
`postgresql/initdb/gf_0003`). A session already open outlives its password, PostgreSQL
checking credentials only at connect time, so the bound is on how long a leaked one is
useful, not on the session it was made for.

**It does not add a link through Unfold's settings.** `SIDEBAR["navigation"]` replaces the
app list with whatever it is given, hiding every registered model, and `SITE_DROPDOWN`
renders a panel that opens only once somebody clicks the site name -- a link nobody finds.
So `crudman/app/notebooks/templates/unfold/helpers/navigation.html` overrides the sidebar,
renders the stock app list and appends one entry.

**It does not authenticate against the identity provider.** It could, with its own client
registration and its own claim mapping. Asking crudman instead means one registration, one
place where ranks are decided, and identical behaviour with single sign-on switched off.
