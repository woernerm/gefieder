"""The lineage graph, as an ECharts configuration.

SQLMesh's own graph (``sqlmesh dag``) names the vis-network files on unpkg.com, while the
same graph is already in ``depends_on`` and ECharts is already this system's charting
library.

Drawn as a sankey, the one built-in series shaped like lineage: a directed acyclic graph
in columns, left to right. A model that reads several others keeps every edge, which a
tree series cannot express. The columns come out as the medallion layers on their own,
because a model is placed to the right of everything it reads.
"""

NODE_WIDTH = 18
"""How wide the bar standing for a model is, in pixels."""

NODE_GAP = 16
"""The least space between two bars in the same column, in pixels.

Sankey lays the columns out itself, so this and NODE_WIDTH are the whole geometry.
"""

THEME_COLORS = {
    "fill": "--color-primary-500",
    "line": "--color-base-400",
    "label": "--color-base-700",
    "highlight": "--color-primary-400",
}
"""Which Unfold colour each part of the graph takes, as a custom property name.

Named rather than resolved: the theme writes the values into the page as these custom
properties and the template reads them back, so a palette change reaches the graph on its
own. The label is a foreground colour, a sankey writing it beside the bar.
"""


def levels(models: list[dict]) -> list[list[dict]]:
    """The models grouped into columns, each after everything it depends on.

    Args:
        models: The exported models, each with a ``name`` and its ``depends_on``.

    Returns:
        One list per column, left to right. A dependency outside this set -- a source
        table the project reads but does not define -- is ignored, the graph being about
        the models themselves.
    """
    known = {model["name"] for model in models}
    by_name = {model["name"]: model for model in models}
    depths: dict[str, int] = {}

    def depth(name: str, seen: frozenset[str] = frozenset()) -> int:
        """How many models stand upstream of this one, at most."""
        if name in depths:
            return depths[name]
        # SQLMesh refuses to load a cycle, but looping forever on bad input is worse
        # than stopping.
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
        Sankey places the nodes itself, so no coordinates are given, and the template
        fills the colours in from the theme; see THEME_COLORS.
    """
    if not levels(models):
        return None

    known = {model["name"] for model in models}
    links = [
        {
            "source": parent,
            "target": model["name"],
            # Sankey insists on a value to size the ribbon by. Every dependency weighs
            # the same here, so only the connections say anything.
            "value": 1,
        }
        for model in models
        for parent in model["depends_on"]
        if parent in known
    ]

    # A model nothing reads sits in the last column, where a label to the right would
    # run off the edge.
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
        # Not an ECharts option: the template resolves these against the page and paints
        # the series with the result.
        "themeColors": THEME_COLORS,
        "tooltip": {"trigger": "item", "formatter": "{b}", "confine": True},
        "series": [
            {
                "type": "sankey",
                # Every model without dependencies starts hard against the left edge, so
                # the bronze layer reads as one column.
                "nodeAlign": "left",
                "nodeWidth": NODE_WIDTH,
                "nodeGap": NODE_GAP,
                "draggable": False,
                # Sankey animates by default, wiping in from the left and re-laying out
                # on every hover and theme change, which reads as a flicker.
                "animation": False,
                "label": {"show": True, "fontSize": 11},
                "lineStyle": {"curveness": 0.5},
                "emphasis": {"focus": "adjacency"},
                "data": nodes,
                "links": links,
            }
        ],
    }
