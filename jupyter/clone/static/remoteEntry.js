// "Clone this repository": the address a person clones the models repository from on their
// own machine, offered in the File menu and on the file browser's toolbar.
//
// Written by hand rather than built: JupyterLab loads an extension by running this file
// and calling init() and get() on what it puts under window._JUPYTERLAB, which is the whole
// of the federated-module contract a plugin needs when it imports nothing. Building one
// would bring node and an npm registry into the image for this one file. Where the entries
// appear is declared in schemas/, the way every other extension declares menus and
// toolbars, so overrides.json can still take them out.
//
// The address and the SSH keys page come from the page configuration, which
// jupyter_server_config.py fills from the spawn's environment -- crudman derives both.
(function () {
    const COMMAND = "models-clone:open";

    // The cloud with a downward arrow the git extension's own clone button used, which
    // this replaces (overrides.json hides that one).
    const ICON =
        "url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' " +
        "viewBox='0 0 24 24'%3E%3Cpath d='M19.35 10.04A7.49 7.49 0 0 0 12 4C9.11 4 6.6 " +
        "5.64 5.35 8.04A5.99 5.99 0 0 0 0 14c0 3.31 2.69 6 6 6h13c2.76 0 5-2.24 " +
        "5-5 0-2.64-2.05-4.78-4.65-4.96zM17 13l-5 5-5-5h3V9h4v4h3z'/%3E%3C/svg%3E\")";

    // The icon is a mask over the colour Lab's own toolbar icons are filled with, so it
    // follows the theme like they do. The dialog borrows Lab's jp-Dialog classes and only
    // lays out its body here.
    const STYLES = `
        .app-CloneIcon {
            background-color: var(--jp-inverse-layout-color3);
            -webkit-mask: ${ICON} center / 16px no-repeat;
            mask: ${ICON} center / 16px no-repeat;
        }
        .app-Clone-body {
            display: flex;
            flex-direction: column;
            gap: 8px;
            width: 420px;
            max-width: 100%;
        }
        .app-Clone-row {
            display: flex;
            gap: 4px;
        }
        .app-Clone-row input {
            flex: 1;
            font-family: var(--jp-code-font-family);
        }
        .app-Clone-hint {
            color: var(--jp-ui-font-color2);
        }
        .app-Clone-hint a {
            color: var(--jp-content-link-color);
        }
    `;

    function config(name) {
        const element = document.getElementById("jupyter-config-data");
        return element ? JSON.parse(element.textContent)[name] || "" : "";
    }

    function make(tag, attributes, ...children) {
        const element = document.createElement(tag);
        Object.assign(element, attributes);
        element.append(...children);
        return element;
    }

    async function copy(input, button) {
        // The clipboard API exists only on a secure origin; a development stack on plain
        // HTTP falls back to the selection.
        try {
            await navigator.clipboard.writeText(input.value);
        } catch {
            input.select();
            document.execCommand("copy");
        }
        button.textContent = "Copied";
        setTimeout(() => (button.textContent = "Copy"), 1500);
    }

    function open() {
        // One at a time: a second click while it is open brings the first back to focus.
        const existing = document.querySelector(".app-Clone");
        if (existing) {
            existing.focus();
            return;
        }
        const url = config("modelsCloneUrl");
        const keysUrl = config("modelsSshKeysUrl");

        const input = make("input", {
            className: "jp-mod-styled",
            readOnly: true,
            value: url,
            ariaLabel: "Clone address",
        });
        input.addEventListener("focus", () => input.select());
        const copyButton = make("button", {
            className: "jp-Dialog-button jp-mod-reject jp-mod-styled",
            textContent: "Copy",
        });
        copyButton.addEventListener("click", () => copy(input, copyButton));

        // _top: the admin page is a page of the shell's, not of this frame.
        const hint = keysUrl
            ? make(
                  "p",
                  { className: "app-Clone-hint" },
                  "Git signs in with your SSH key. Add it on the ",
                  make("a", { href: keysUrl, target: "_top", textContent: "SSH keys page" }),
                  " first. Pushing to main puts that version into production.",
              )
            : make("p", {
                  className: "app-Clone-hint",
                  textContent: "Pushing to main puts that version into production.",
              });

        const body = url
            ? make("div", { className: "app-Clone-body" }, make("div", { className: "app-Clone-row" }, input, copyButton), hint)
            : make("div", {
                  className: "app-Clone-body",
                  textContent: "This server was started without a clone address. Restart it from the hub.",
              });

        const close = make("button", {
            className: "jp-Dialog-button jp-mod-accept jp-mod-styled",
            textContent: "Close",
        });
        const dialog = make(
            "div",
            // jp-ThemedContainer too: Lab's overlay rule names both classes, and without it
            // the dialog is laid out below the page rather than over it.
            { className: "jp-Dialog jp-ThemedContainer app-Clone", tabIndex: -1 },
            make(
                "div",
                { className: "jp-Dialog-content", role: "dialog", ariaModal: "true", ariaLabel: "Clone this repository" },
                make("div", { className: "jp-Dialog-header" }, "Clone this repository"),
                make("div", { className: "jp-Dialog-body" }, body),
                make("div", { className: "jp-Dialog-footer" }, close),
            ),
        );

        const dismiss = () => dialog.remove();
        close.addEventListener("click", dismiss);
        // A click on the backdrop closes it, as one on Lab's own dialogs does.
        dialog.addEventListener("click", (event) => event.target === dialog && dismiss());
        dialog.addEventListener("keydown", (event) => event.key === "Escape" && dismiss());

        document.body.appendChild(dialog);
        (url ? input : close).focus();
    }

    const plugin = {
        id: "models-clone:plugin",
        description: "Shows the address the models repository is cloned from.",
        autoStart: true,
        activate(app) {
            document.head.appendChild(make("style", { textContent: STYLES }));
            app.commands.addCommand(COMMAND, {
                // The toolbar's buttons are icons alone; the menu entry is words alone,
                // like its neighbours. Lab passes {toolbar: true} to a toolbar's command.
                label: (args) => (args.toolbar ? "" : "Clone this repository…"),
                caption: "Clone this repository",
                iconClass: (args) => (args.toolbar ? "app-CloneIcon" : ""),
                execute: open,
            });
        },
    };

    window._JUPYTERLAB = window._JUPYTERLAB || {};
    window._JUPYTERLAB["models-clone"] = {
        init: () => {},
        get: () => () => ({ __esModule: true, default: plugin }),
    };
})();
