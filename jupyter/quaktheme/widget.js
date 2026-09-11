// quak builds its table in a shadow root: the theme's variables reach inside, because the
// CSS `all` shorthand its :host rule uses excludes custom properties, but no ordinary
// selector does. Two things need a rule rather than a variable, so they are set here on
// the same root: the scroll height, an inline style on an element in there, and the row
// under the pointer.
//
// This wraps quak's own module rather than replacing it: startup.py prepends the bundle
// quak ships and appends this, so quak's render() does all the work and an upgrade changes
// the table without touching this file. Prepended rather than imported because anywidget
// serves a module from a blob URL, against which no relative path resolves -- and it
// inlines the bundle either way, so this adds a wrapper and not a second copy.
//
// The bundle's own export statement is rewritten to bind its factory to quakFactory, so
// nothing here depends on the minified name, which changes with every quak release.

// quak sizes the scroll area to 11.5 rows and writes that as an inline max-height, which
// only a rule marked important can displace. Resizable as well as taller: a preview and a
// full result want different heights, and the handle costs one declaration.
//
// The row under the pointer is an inline background-color quak writes as
// var(--light-silver) -- the class its stylesheet defines for this is never applied, so
// the variable is the only handle. That same variable is every border in the table, so it
// becomes the hover colour here and the borders are given back their own.
const STYLES = `
  .table-container {
    max-height: var(--app-quak-height, 32rem) !important;
    resize: vertical;
  }

  :host {
    --light-silver: var(--app-quak-hover);
  }

  .quak,
  td {
    border-color: var(--app-quak-border);
  }

  th {
    border-bottom-color: var(--app-quak-border);
    border-left-color: var(--app-quak-border);
  }

  td:nth-last-child(2),
  th:nth-last-child(2) {
    border-right-color: var(--app-quak-border);
  }

  /* The label that follows the pointer along a chart's axis. Its box is a rect quak fills
     with a hardcoded "white", and the text on it is the axis's own colour -- so on a dark
     theme it is white on white. Both are named here, the text explicitly because it would
     otherwise keep inheriting the axis colour. */
  .tick rect {
    fill: var(--app-quak-tooltip-background) !important;
  }

  .tick text {
    fill: var(--app-quak-tooltip-color);
    font-family: var(--sans-serif);
  }

  /* Hovering a bar drops every other foreground bar to opacity 0.3, which on a dark ground
     leaves it barely distinguishable from the background bar behind it. Raised so the
     unhovered bars stay readable as bars. */
  rect[opacity="0.3"] {
    opacity: var(--app-quak-dimmed) !important;
  }

  /* A categorical column's bars are divs, and quak writes their label colour and their
     separator as a literal "white" inline. Lab puts its inverse font colour on this same
     brand blue -- the file browser's selected row -- so the label follows that, and the
     separator becomes the table's own background rather than a bright line. */
  .quak div[title] > span {
    color: var(--app-quak-on-primary) !important;
  }

  .quak div[title] {
    border-color: var(--app-quak-separator) !important;
  }
`;

/** Add the stylesheet to the shadow root quak rendered its table into, once. */
function applyStyles(el) {
    const host = el.querySelector("div");
    const root = host?.shadowRoot;
    if (!root || root.querySelector("style[data-app-theme]")) {
        return Boolean(root);
    }
    const style = document.createElement("style");
    style.dataset.appTheme = "";
    style.textContent = STYLES;
    root.appendChild(style);
    return true;
}

export default () => {
    const widget = quakFactory();
    return {
        ...widget,
        async render(props) {
            try {
                return await widget.render(props);
            } finally {
                // In a finally, because a table that failed to load is exactly when the
                // person is looking at it; and on a frame after, since quak attaches the
                // shadow root while rendering and may not have reached it yet.
                if (!applyStyles(props.el)) {
                    requestAnimationFrame(() => applyStyles(props.el));
                }
            }
        },
    };
};
