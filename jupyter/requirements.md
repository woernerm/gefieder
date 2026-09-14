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

Lab's Contextual Help panel (Ctrl+I) is answered by the kernel, and IPython knows Python
names alone -- so over SQL it stays empty. The kernel looks the token up in the project
first: a model name shows that model's description and columns, a `@macro` its docstring,
and a column its type -- inferred by SQLMesh, so it is there whether or not anyone wrote a
description -- in every model the cell names. What an upstream file says about itself is
read where it is used, without opening it.

## What was taken out of JupyterLab

Each entry names its lever; undoing the lever brings it back. Plugins are switched off in
`labconfig/page_config.json` (delete the line), menu entries in `overrides/overrides.json`
(delete the item), export formats in `jupyter_server_config.py`. All three are baked into
the image, so a change is a rebuild, never an edit on the target machine.

- **Git menu** -- `jp-mainmenu-git` in overrides. Init, Clone, Merge, Push and Pull
  including *Force*, Reset to Remote, Manage Remotes, Open in Terminal, Simple staging,
  Open .gitignore, Help. The panel keeps commit, branch, push, pull, history and diff; the
  menu held what a basic workflow must not do. Simple staging: Settings Editor, Git.
- **Workspaces** -- `@jupyterlab/workspaces-extension` in page_config. File ▸ Workspaces
  (open, create, clone, rename, save, import, export, reset, delete), the running panel's
  Workspaces section, View ▸ Appearance ▸ Show Workspace Indicator. Layout snapshots for
  people juggling projects; here there is one. The layout is still restored between visits.
- **Export formats** -- `*Exporter.enabled` in jupyter_server_config.py. File ▸ Save and
  Export Notebook As keeps HTML, Markdown, PDF and Executable Script. AsciiDoc, LaTeX,
  reStructuredText, Reveal.js Slides, Qtpdf, Qtpng and Webpdf need pandoc, TeX or a
  browser the image lacks. PDF needs TeX too; the entry waits for it.
- **Console** -- `@jupyterlab/console-extension` and the `consoles`, `code-console` and
  `debug-console` plugins in page_config; the stragglers in overrides. File ▸ New ▸
  Console, the launcher card, New Console for Notebook or Editor, New Subshell Console,
  Run Selected Text in Console, Settings ▸ Console Run Keystroke. A bare query cell is the
  REPL already.
- **Tabs menu** -- `jp-mainmenu-tabs` in overrides. Activate Next, Previous and
  Previously Used Tab, the tab bars, the list of open tabs. The tab bar shows the same and
  the shortcuts still work.
- **Kernel menu** -- `jp-mainmenu-kernel` in overrides. Interrupt, Restart, Restart and
  Clear, Restart and Run up to Selected or All, Restart and Debug, Reconnect, Shut Down,
  Shut Down All, Change Kernel. The toolbar interrupts and restarts, Edit clears outputs,
  File ▸ Close and Shut Down stops, the Running panel stops all, reloading reconnects.
- **Kernel picker** -- `kernelName` under `notebook-extension:panel` in overrides. The
  toolbar's "SQLMesh" button, which opened a Select Kernel dialog with one entry. The
  status circle beside it stays.
- **Property Inspector** -- `application-extension:property-inspector` with the
  `notebook-extension:tools`, `active-cell-tool`, `metadata-editor`, `celltags-extension`
  and `metadataform-extension` plugins in page_config. The right sidebar's cell tags and
  metadata forms, View ▸ Property Inspector. A `.sql` or `.py` model has nowhere to keep
  what is typed there; it vanished on save.
- **Text Editor Syntax Highlighting** -- submenu `jp-mainmenu-view-codemirror-language` in
  overrides. View ▸ a list of 150 languages. Highlighting follows the file extension.
- **Extension Manager** -- `@jupyterlab/extensionmanager-extension` in page_config. Its
  sidebar tab, View ▸ Extension Manager, Settings ▸ Enable Extension Manager. Extensions
  are `JUPYTER_EXTENSIONS` in `buildtime.env`, installed at build; the environment is
  read-only for a person, so an install from the panel could only fail.
- **Settings menu entries** -- submenus by id under `jp-mainmenu-settings` in overrides,
  except where noted.
  - *Theme*: the theme list, Synchronize with System Settings, Theme Scrollbars, the font
    sizes. The theme is `DEFAULT_THEME` in `runtime.env`; a person changes theirs in the
    Settings Editor. Font size: the browser's zoom.
  - *Text Editor Theme*: two CodeMirror schemes, both recoloured by the palette anyway.
  - *Terminal Theme*: added by code, so hidden by `custom/custom.css` instead.
  - *Language*: also `translation-extension:plugin` in page_config. English, and "Install
    more languages…", which needs a package index the target machine does not have.
- **Edit menu entries** -- commands under `jp-mainmenu-edit` in overrides. Move Cell Up,
  Move Cell Down, Delete Cell: the same three buttons sit on every cell's own toolbar, and
  Delete Cell stays in the cell's context menu.
- **File menu entries** -- commands under `jp-mainmenu-file` in overrides. Close Tab,
  Close All Tabs, Close and Shut Down Notebook, Print: a tab closes from its cross or
  context menu, a kernel stops in the Running panel, the browser prints. *Jupytext*, which
  pairs a notebook with a second file, is hidden by `custom/custom.css`.
- **Git Clone button** -- `gitClone` under `filebrowser-extension:widget` in overrides.
  The file browser's toolbar button beside refresh and upload. The workspace is the clone,
  made at spawn; a second repository beside it would be shipped nowhere.
- **Hub menu** -- `hub-extension:menu` in page_config. File ▸ Hub Control Panel and Log
  Out. Log Out left the hub only and the admin session signed the person straight back
  in; sign-out is the shell bar's account menu. Stop My Server is still at
  `/<NOTEBOOK_PATH>/hub/home`.

Kept on purpose: the terminal, which is how the `sqlmesh` CLI is run, and the debugger,
for Python models.

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
