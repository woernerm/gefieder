"""The ECharts bundle in the crudman image still draws the lineage graph.

The library is fetched unpinned at build time, so nothing else in this repository says
which version shipped. Every other test around the graph asserts the *option* Python
builds -- none of them runs a line of ECharts, so a release that renamed a sankey option
or dropped a default would leave the documentation page blank with every test green.

So this renders for real: the bundle out of the image, the option out of the same
lineage.py the page uses, drawn by node in ECharts' server-side mode (init(null, ...,
{ssr: true}) plus renderToSVGString(), the SVG renderer the template already asks for).
What comes back is asserted to be the graph and not an empty canvas -- a bar per model, a
ribbon per dependency, and the labels the formatter shortens.
"""
import json
import re
import subprocess

import pytest

from conftest import podman

# Where the Dockerfile puts the downloaded bundle. The source tree copy, not the one under
# staticfiles/: collectstatic hashes that filename, so only this path is stable.
BUNDLE = "/crudman/app/docs/static/docs/echarts.min.js"

# A lineage the shape of a real one: two tenants' bronze models feeding one silver model,
# which feeds gold. The several-parents case is the reason the graph is a sankey at all,
# and three columns is what makes a dropped layer visible.
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

# lineage.NODE_WIDTH, which cannot be imported here: it lives in the crudman image, not on
# the host the suite runs on. Only the column check below reads it, and a disagreement
# fails that check rather than passing quietly.
NODE_WIDTH = 18

# What the page's own script does to the option before handing it to ECharts: the theme
# colours it resolves from the stylesheet, and the formatter that shortens a node's name.
# Repeated here because a browser is what normally does it -- keeping it close to
# templates/docs/index.html is what makes the render the page's render.
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

# The box the template gives the graph is 520 px tall and as wide as the page; a width is
# required because there is no DOM to measure.
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

    # The option comes from the container too, so the test draws what lineage.py builds
    # rather than a copy of it that could drift.
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
    # An ECharts that draws nothing still returns the empty frame, so the version is
    # reported alongside every failure below to say what was actually tested.
    assert rendered["version"], "the bundle reports no version"


def test_every_model_shall_get_a_bar(rendered):
    # A sankey node is a <path>, as is every ribbon; together they are the whole graph, so
    # counting them is what separates a drawn graph from an empty frame.
    paths = rendered["svg"].count("<path")
    assert paths == len(MODELS) + LINKS, (
        f"ECharts {rendered['version']} drew {paths} shapes, "
        f"expected {len(MODELS)} nodes and {LINKS} links"
    )


def test_every_model_shall_be_labelled(rendered):
    # The formatter shortens "bronze_project_a.issues" to "issues  (project_a)" and
    # "gold.throughput" to "throughput"; finding those is what proves the labels were laid
    # out rather than dropped for want of room.
    svg = rendered["svg"]
    for label in ("issues  (project_a)", "issues  (project_b)", "throughput"):
        assert label in svg, (
            f"ECharts {rendered['version']} did not draw the label {label!r}"
        )


def test_the_layers_shall_come_out_as_columns(rendered):
    # Left to right, a model to the right of everything it reads. A node is drawn as the
    # rectangle "M<x> <y>l<nodeWidth> 0l0 <height>l-<nodeWidth> 0Z", which is what tells it
    # apart from a ribbon (curves, so a "C"), and its x is the column it stands in. Three
    # distinct ones means bronze, silver and gold did not collapse into each other.
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
