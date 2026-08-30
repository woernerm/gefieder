"""The lineage graph, as an ECharts configuration.

SQLMesh renders a graph of its own (``sqlmesh dag``), but its markup names the
vis-network files on unpkg.com and would have to be rewritten to reach a copy in the
image. The same graph is already in ``depends_on``, and ECharts is already the charting
library of this system, so the configuration is written here and the drawing left to the
same library the Grafana panels use.

The layout is the one a medallion architecture wants anyway -- a column per level, a
model to the right of everything it reads -- so bronze, silver and gold fall into their
own columns without being told to, and the arrows all point the same way.
"""

COLUMN_GAP = 700
"""Between columns.

Wide enough that every edge arrives at a shallow angle. ECharts stops an edge a fixed
distance short of the node centre -- half the symbol, as though it were round -- so on a
node four times wider than tall an edge coming in steeply stops well inside the box and
the arrow appears to touch the label rather than the node. Spreading the columns flattens
the edges until that distance lands on the node's edge, give or take a pixel or two.
"""

ROW_GAP = 110
"""Between the nodes of one column, measured centre to centre.

Nearly three times the node height. The graph is scaled down to fit its box, and the gap
shrinks with everything else, so a clearance that looks generous in these units comes out
much tighter on the page.
"""

NODE_SIZE = [180, 40]
"""Width and height of a node, in the same units as the positions above.

Narrower than the longest model name on purpose: the label is truncated rather than the
graph made wider, since every extra unit of width costs scale once the whole thing is
fitted into its box, and the full name is a hover away.
"""

CORNER_RADIUS = 10
"""The corner radius of a node, in pixels once drawn."""


def _rounded_rect(width: int, height: int, radius: int) -> str:
    """A rounded rectangle whose corners stay circular on a wide node.

    The built-in "roundRect" cannot be used here. ECharts draws a symbol in a normalised
    -1..1 box and then scales it to symbolSize, so on a node five times wider than it is
    tall every corner arc is stretched five times as wide as it is high. Giving the arcs
    radii that differ per axis by exactly that ratio cancels the scaling, and the corners
    come out round.

    Args:
        width: The node's width, as passed to symbolSize.
        height: The node's height.
        radius: The corner radius wanted after scaling, in pixels.

    Returns:
        The path, in the "path://" form ECharts takes as a symbol.
    """
    # The normalised box is 2 units across, so a coordinate is scaled by half the size.
    horizontal = radius / (width / 2)
    vertical = radius / (height / 2)
    return (
        f"path://M {-1 + horizontal} -1 H {1 - horizontal} "
        f"A {horizontal} {vertical} 0 0 1 1 {-1 + vertical} "
        f"V {1 - vertical} A {horizontal} {vertical} 0 0 1 {1 - horizontal} 1 "
        f"H {-1 + horizontal} A {horizontal} {vertical} 0 0 1 -1 {1 - vertical} "
        f"V {-1 + vertical} A {horizontal} {vertical} 0 0 1 {-1 + horizontal} -1 Z"
    )

THEME_COLORS = {
    "fill": "--color-primary-500",
    "line": "--color-base-400",
    "label": "--color-base-50",
    "highlight": "--color-primary-400",
}
"""Which Unfold colour each part of the graph takes, as a custom property name.

Named rather than resolved: the values live once in UNFOLD["COLORS"], which the theme
writes into the page as these custom properties, and the template reads them back from
there. So a palette change reaches the graph on its own and no colour is written twice.
The names travel in the option, so this is the only place they are listed.
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
        The positions are fixed rather than left to a force layout, the medallion levels
        being the whole point of the picture. The colours are left out and filled in by
        the template from the theme; see THEME_COLORS.
    """
    columns = levels(models)
    if not columns:
        return None

    tallest = max(len(column) for column in columns)
    nodes = []
    for column_index, column in enumerate(columns):
        # Centred against the tallest column, so a short one does not sit at the top.
        offset = (tallest - len(column)) * ROW_GAP / 2
        for row_index, model in enumerate(column):
            nodes.append(
                {
                    "id": model["name"],
                    # The schema repeats down a whole column, so the table alone tells
                    # the nodes apart; the full name is in the tooltip and the link.
                    "name": model["name"].split(".")[-1],
                    "x": column_index * COLUMN_GAP,
                    "y": offset + row_index * ROW_GAP,
                    "value": model["name"],
                    "url": url_for(model),
                }
            )

    known = {model["name"] for model in models}
    links = [
        {"source": parent, "target": model["name"]}
        for model in models
        for parent in model["depends_on"]
        if parent in known
    ]

    return {
        # Not an ECharts option: the template reads it, resolves each custom property
        # against the page and paints the series with the result.
        "themeColors": THEME_COLORS,
        "tooltip": {"formatter": "{c}", "confine": True},
        "series": [
            {
                "type": "graph",
                "layout": "none",
                "roam": True,
                "draggable": True,
                "symbol": _rounded_rect(*NODE_SIZE, CORNER_RADIUS),
                "symbolSize": NODE_SIZE,
                "label": {"show": True, "fontSize": 12, "overflow": "truncate"},
                "edgeSymbol": ["none", "arrow"],
                "edgeSymbolSize": 10,
                "lineStyle": {"width": 2, "curveness": 0.1},
                # The hovered node keeps its shape and only lightens, which is what the
                # highlight colour is for; ECharts would otherwise blank the fill.
                "emphasis": {"focus": "adjacency", "scale": False},
                "data": nodes,
                "links": links,
            }
        ],
    }
