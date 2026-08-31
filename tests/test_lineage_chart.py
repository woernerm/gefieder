"""The ECharts bundle in the crudman image still draws the lineage graph.

The library is fetched unpinned at build time, and every other test around the graph
asserts the *option* Python builds, so an ECharts release that renamed a sankey option
would leave the documentation page blank with every test green.

This renders for real: the bundle out of the image, the option out of the same lineage.py
the page uses, drawn by node in ECharts' server-side mode. What comes back is asserted to
be the graph and not an empty canvas -- a bar per model, a ribbon per dependency, and the
labels the formatter shortens.
"""
import json
import re
import subprocess

import pytest

from conftest import podman

# Where the Dockerfile puts the downloaded bundle. The source tree copy: collectstatic
# hashes the staticfiles/ name, so only this path is stable.
BUNDLE = "/crudman/app/docs/static/docs/echarts.min.js"

# The shape of a real lineage: two tenants' bronze models feeding one silver model, which
# feeds gold. Several parents is why the graph is a sankey; three columns is what makes a
# dropped layer visible.
MODELS = [
    {"name": "bronze_project_a.issues", "depends_on": [], "layer": "bronze"},
    {"name": "bronze_project_b.issues", "depends_on": [], "layer": "bronze"},
    {
        "name": "silver.issues",
        "depends_on": ["bronze_project_a.issues", "bronze_project_b.issues"],
        "layer": "silver",
    },
    {"name": "gold.throughput", "depends_on": ["silver.issues"], "layer": "gold"},
]
LINKS = 3  # bronze_a -> silver, bronze_b -> silver, silver -> gold

# lineage.NODE_WIDTH, which lives in the crudman image rather than on this host. Only the
# column check reads it, and a disagreement fails that check rather than passing quietly.
NODE_WIDTH = 18

# What the page's own script does to the option before handing it to ECharts: resolve the
# theme colours and shorten each node's name. Repeated here because a browser normally
# does it; keep it in step with templates/docs/index.html.
PAINT = """
const s = option.series[0];
delete option.themeColors;
s.itemStyle = { color: "#7c3aed" };
s.label.color = "#334155";
s.label.fontFamily = "sans-serif";
s.lineStyle.color = "#94a3b8";
s.lineStyle.opacity = 0.3;
s.emphasis.itemStyle = { color: "#a78bfa" };
s.emphasis.lineStyle = { color: "#94a3b8", opacity: 0.55 };
s.label.formatter = function (params) {
    const [schema, table] = params.name.split(".");
    return schema.startsWith("bronze_")
        ? table + "  (" + schema.replace(/^bronze_/, "") + ")"
        : table;
};
"""

# The template's box is 520 px tall and as wide as the page; a width is required, there
# being no DOM to measure.
RENDER = """
const fs = require("fs");
const echarts = require(process.argv[2]);
const option = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
%s
const chart = echarts.init(null, null, {
    renderer: "svg", ssr: true, width: 960, height: 520,
});
chart.setOption(option);
console.log(JSON.stringify({ version: echarts.version, svg: chart.renderToSVGString() }));
// ECharts leaves a timer behind, which would hold node open until the test timed out.
process.exit(0);
"""


@pytest.fixture(scope="module")
def rendered(tmp_path_factory):
    """The lineage graph of MODELS, drawn by the ECharts the image actually ships."""
    work = tmp_path_factory.mktemp("lineage")
    bundle = work / "echarts.min.js"
    subprocess.run(
        ["podman", "cp", f"crudman:{BUNDLE}", str(bundle)], check=True,
    )

    # The option comes from the container too, so nothing here can drift from it.
    script = (
        "import json, sys; sys.path.insert(0, '/crudman/app');"
        "from docs import lineage;"
        "print(json.dumps(lineage.chart(json.loads(sys.argv[1]), lambda m: '/docs/')))"
    )
    option = podman(
        "exec", "crudman", "uv", "run", "--project", "/crudman",
        "python", "-c", script, json.dumps(MODELS),
    )
    (work / "option.json").write_text(option)

    renderer = work / "render.js"
    renderer.write_text(RENDER % PAINT)
    result = subprocess.run(
        ["node", str(renderer), str(bundle), str(work / "option.json")],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, (
        f"the shipped ECharts did not render the lineage graph:\n{result.stderr}"
    )
    return json.loads(result.stdout)


def test_the_shipped_bundle_shall_render_an_svg(rendered):
    svg = rendered["svg"]
    assert svg.startswith("<svg"), f"not an SVG: {svg[:80]!r}"
    # An ECharts that draws nothing still returns the empty frame, so every failure below
    # names the version that was tested.
    assert rendered["version"], "the bundle reports no version"


def test_every_model_shall_get_a_bar(rendered):
    # Nodes and ribbons are both <path>, and together they are the whole graph.
    paths = rendered["svg"].count("<path")
    assert paths == len(MODELS) + LINKS, (
        f"ECharts {rendered['version']} drew {paths} shapes, "
        f"expected {len(MODELS)} nodes and {LINKS} links"
    )


def test_every_model_shall_be_labelled(rendered):
    # Finding the shortened labels proves they were laid out rather than dropped for
    # want of room.
    svg = rendered["svg"]
    for label in ("issues  (project_a)", "issues  (project_b)", "throughput"):
        assert label in svg, (
            f"ECharts {rendered['version']} did not draw the label {label!r}"
        )


def test_the_layers_shall_come_out_as_columns(rendered):
    # Left to right, a model right of everything it reads. A node is the rectangle
    # "M<x> <y>l<nodeWidth> 0l0 <height>l-<nodeWidth> 0Z", which a ribbon's curves tell it
    # apart from, and its x is its column. Three distinct ones means three layers.
    columns = {
        float(x)
        for x in re.findall(
            rf'<path d="M([\d.]+) [\d.]+l{NODE_WIDTH} 0l0 ', rendered["svg"]
        )
    }
    assert len(columns) == 3, (
        f"ECharts {rendered['version']} laid the graph out in {len(columns)} columns, "
        "expected one per medallion layer"
    )
