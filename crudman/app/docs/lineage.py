"""The lineage graph, as an ECharts configuration.

SQLMesh renders a graph of its own (``sqlmesh dag``), but its markup names the
vis-network files on unpkg.com and would have to be rewritten to reach a copy in the
image. The same graph is already in ``depends_on``, and ECharts is already the charting
library of this system, so the configuration is written here and the drawing left to the
same library the Grafana panels use.

Drawn as a sankey, which is the one built-in series shaped like lineage: a directed
acyclic graph laid out in columns, left to right. A model that reads several others --
``silver.issues`` unions one bronze model per tenant -- keeps every one of those edges,
which a tree series cannot express, since a tree allows a node only one parent. The
columns come out as the medallion layers without being told to, because a model is
placed to the right of everything it reads.
"""

NODE_WIDTH = 18
"""How wide the bar standing for a model is, in pixels."""

NODE_GAP = 16
"""The least space between two bars in the same column, in pixels.

Sankey lays the columns out itself and only needs to be told how far apart to keep
them, so this and NODE_WIDTH are the whole geometry.
"""

THEME_COLORS = {
    "fill": "--color-primary-500",
    "line": "--color-base-400",
    "label": "--color-base-700",
    "highlight": "--color-primary-400",
}
"""Which Unfold colour each part of the graph takes, as a custom property name.

Named rather than resolved: the values live once in UNFOLD["COLORS"], which the theme
writes into the page as these custom properties, and the template reads them back from
there. So a palette change reaches the graph on its own and no colour is written twice.
The names travel in the option, so this is the only place they are listed.

The label is a foreground colour because a sankey writes it beside the bar, on the page
rather than on the fill.
"""


def levels(models: list[dict]) -> list[list[dict]]:
    """The models grouped into columns, each after everything it depends on.

    Args:
        models: The exported models, each with a ``name`` and its ``depends_on``.

    Returns:
        One list per column, left to right. A dependency on something outside this set --
        a source table the project reads but does not define -- is ignored rather than
        drawn, the graph being about the models themselves.
    """
    known = {model["name"] for model in models}
    by_name = {model["name"]: model for model in models}
    depths: dict[str, int] = {}

    def depth(name: str, seen: frozenset[str] = frozenset()) -> int:
        """How many models stand upstream of this one, at most."""
        if name in depths:
            return depths[name]
        # A cycle cannot occur in a SQLMesh project -- it refuses to load one -- but a
        # layout routine that loops forever on bad input is worse than one that stops.
        if name in seen:
            return 0
        parents = [dep for dep in by_name[name]["depends_on"] if dep in known]
        result = 1 + max(
            (depth(parent, seen | {name}) for parent in parents), default=-1
        )
        depths[name] = result
        return result

    for model in models:
        depth(model["name"])

    columns: list[list[dict]] = [[] for _ in range(max(depths.values(), default=-1) + 1)]
    for model in sorted(models, key=lambda entry: entry["name"]):
        columns[depths[model["name"]]].append(model)
    return columns


def chart(models: list[dict], url_for) -> dict | None:
    """The lineage as an ECharts option object.

    Args:
        models: The exported models to draw.
        url_for: Called with a model to get the address its node links to.

    Returns:
        The option ECharts is initialised with, or None when there is nothing to draw.
        Sankey places the nodes itself, so no coordinates are given; the colours are
        left out and filled in by the template from the theme, see THEME_COLORS.
    """
    if not levels(models):
        return None

    known = {model["name"] for model in models}
    links = [
        {
            "source": parent,
            "target": model["name"],
            # Sankey sizes a ribbon by its value and insists on one. Every dependency
            # weighs the same here -- a model either reads another or does not -- so
            # the widths say nothing and only the connections do.
            "value": 1,
        }
        for model in models
        for parent in model["depends_on"]
        if parent in known
    ]

    # A model nothing reads sits in the last column, where a label to the right would
    # run off the edge; its own side is free instead.
    feeds_something = {link["source"] for link in links}
    nodes = [
        {
            "name": model["name"],
            "url": url_for(model),
            **(
                {}
                if model["name"] in feeds_something
                else {"label": {"position": "left"}}
            ),
        }
        for model in sorted(models, key=lambda entry: entry["name"])
    ]

    return {
        # Not an ECharts option: the template reads it, resolves each custom property
        # against the page and paints the series with the result.
        "themeColors": THEME_COLORS,
        "tooltip": {"trigger": "item", "formatter": "{b}", "confine": True},
        "series": [
            {
                "type": "sankey",
                # Every model without dependencies starts hard against the left edge,
                # so the bronze layer reads as one column rather than being spread to
                # fill the width.
                "nodeAlign": "left",
                "nodeWidth": NODE_WIDTH,
                "nodeGap": NODE_GAP,
                "draggable": False,
                # Sankey animates by default: it wipes the diagram in from the left
                # over a second on the way in, and animates a re-layout on every hover
                # and theme change, which reads as a flicker. The layout is fixed here,
                # so there is nothing worth animating either time.
                "animation": False,
                "label": {"show": True, "fontSize": 11},
                "lineStyle": {"curveness": 0.5},
                "emphasis": {"focus": "adjacency"},
                "data": nodes,
                "links": links,
            }
        ],
    }
