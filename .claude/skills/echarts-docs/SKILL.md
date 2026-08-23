---
name: echarts-docs
description: Apache ECharts documentation to consult before answering questions about ECharts or implementing charts with it.
---

# Apache ECharts documentation

ECharts changes quickly and v6 shifted defaults, so check current documentation rather
than answering from memory. **Fetch the pages that cover the question before answering
or writing chart code** — the summaries below exist so you don't have to fetch all of
them. Each entry gives the full URL and what you will find there.

Two references live outside this handbook and are worth fetching for exact option
names: the option manual `https://echarts.apache.org/en/option.html` and the API
manual `https://echarts.apache.org/en/api.html`.

## Getting started and installation

- https://apache.github.io/echarts-handbook/en/get-started — The five-minute
  introduction. A complete HTML page: a sized `<div id="main">`, a `<script>` tag
  pulling `dist/echarts.js` from jsDelivr, then `echarts.init(dom)` followed by
  `setOption()`. The example option object shows `title`, `tooltip`, `legend`, `xAxis`,
  `yAxis` and a `bar` series. Start here only for the overall shape of an ECharts
  program; every detail lives on a later page.
- https://apache.github.io/echarts-handbook/en/basics/download — Four ways to obtain
  ECharts: `npm install echarts`; the jsDelivr, unpkg and cdnjs CDNs; release tarballs
  from the GitHub repository (`dist/echarts.js` is the full build); and the online
  builder that emits a custom bundle containing only the modules you pick. Also
  describes the directory layout of the distributed package.
- https://apache.github.io/echarts-handbook/en/basics/import — **Read before writing
  imports in a bundled project.** Contrasts the full import (`import * as echarts from
  'echarts'`) with the tree-shakeable one: pull `echarts/core`, charts (`BarChart`,
  `LineChart`), components (`TitleComponent`, `TooltipComponent`, `GridComponent`,
  `DatasetComponent`), features (`LabelLayout`, `UniversalTransition`) and a renderer
  (`CanvasRenderer` or `SVGRenderer` — one is mandatory), then register them with
  `echarts.use([...])`. Ends with the TypeScript `ComposeOption` pattern that narrows
  `EChartsOption` to the components actually registered.
- https://apache.github.io/echarts-handbook/en/basics/help — Where to ask when stuck:
  search the API and option manuals, handbook and FAQ first, then GitHub issues. Build
  a minimal reproduction in the official editor, CodePen, CodeSandbox or JSFiddle.
  Bugs and feature requests go to GitHub issues via the template; how-to questions go
  to Stack Overflow; non-technical mail to dev@echarts.apache.org. No API content.

## Release notes and upgrade guides

- https://apache.github.io/echarts-handbook/en/basics/release-note/v6-feature — The
  twelve headline changes in ECharts 6, grouped as visual presentation, data
  expression and composition. New design-token default theme, runtime theme switching
  without disposing the chart, automatic dark mode via `matchMedia`. New chord and
  beeswarm charts, scatter `jitter`/`jitterOverlap`, broken axis, better candlestick
  support. A matrix coordinate system, registerable custom series published to npm
  (violin, contour, sleep stage, segmented doughnut, bar range, line range) from the
  `apache/echarts-custom-series` repo, and axis labels that no longer overflow.
- https://apache.github.io/echarts-handbook/en/basics/release-note/v6-upgrade-guide —
  **Read this when a chart looks different after moving to v6.** Upgrade is
  `npm install echarts@6` and usually needs nothing else. Three visible breaks: the
  new default theme (restore the old look with `echarts/theme/v5.js`); axis label
  overflow and overlap prevention now on by default (disable with
  `grid.outerBoundsMode: 'none'` and `xAxis/yAxis.nameMoveOverlap: false`); and rich
  text now inheriting the parent label's style (`richInheritPlainLabel: false`).
- https://apache.github.io/echarts-handbook/en/basics/release-note/5-6-0 — Region
  styles embedded in GeoJSON via `features[].properties.echartsStyle` (itemStyle,
  label, tooltip); tooltips on axis labels through `axis.tooltip`; sunburst
  `emphasis.focus: 'relative'` highlighting ancestors and descendants together;
  Swedish and Persian locales (22 total); a line-chart memory-growth fix.
- https://apache.github.io/echarts-handbook/en/basics/release-note/5-5-0 — Proper ESM:
  `"type": "module"` and an `exports` map, fixing Node, vitest/jest and bundler
  resolution. A 4 KB (1 KB gzipped) client runtime that hydrates server-rendered SVG.
  Drilldown animation via `childGroupId` alongside `groupId`. Pie `padAngle` and
  `endAngle`, polar `endAngle`, pictorialBar `clip`, line `sampling: 'min-max'`,
  `tooltip.appendTo`, `axisLabel.alignMinLabel`/`alignMaxLabel`, `dataIndex` passed to
  `valueFormatter`, Arabic and Dutch locales.
- https://apache.github.io/echarts-handbook/en/basics/release-note/5-4-0 — Intelligent
  pointer snapping for small targets on touch devices; pie charts placeable in
  cartesian, calendar and geo coordinate systems (and the Baidu/Gaode map extensions);
  gauge `axisLabel.rotate` accepting `'tangential'`, `'radial'` or -90..90; Ukrainian
  locale (17 total) registered with `echarts.registerLocale()` and selected through
  `opts.locale`.
- https://apache.github.io/echarts-handbook/en/basics/release-note/5-3-0 — Keyframe
  animations for custom series and the graphic component. The SVG renderer rewritten
  on a virtual DOM for 2–10× faster updates, which also enables zero-dependency
  server-side rendering (no node-canvas or JSDOM). Custom map projections through
  `project`/`unproject`. Multi-axis `alignTicks: true`. `tooltip.valueFormatter`,
  `transition`/`enterFrom`/`leaveTo` on graphics, `selectedMode: 'series'`,
  `emphasis.disabled`, per-corner pie `borderRadius`. Breaking: `registerMap`/`getMap`
  now need `GeoComponent` or `MapChart` imported.
- https://apache.github.io/echarts-handbook/en/basics/release-note/5-2-0 — Universal
  transition (`universalTransition: true` plus matching series `id`) morphing between
  *different* chart types, with `groupId`/`dataGroupId` expressing many-to-many
  drilldown and aggregation. `colorBy: 'series' | 'data'` controlling palette
  granularity. Labels on polar bar charts. Empty-pie placeholder
  (`emptyCircleStyle`, `showEmptyCircle`). Big speedup for datasets over 100
  dimensions. Better TypeScript inference inside `renderItem`.
- https://apache.github.io/echarts-handbook/en/basics/release-note/v5-feature — What
  ECharts 5 introduced under the theme "show, do not tell": bar/line racing, custom
  series morphing, redesigned light and dark themes, label overlap hiding and
  truncation, a smarter time axis, tooltip restyling and value sorting, gauge
  upgrades, rounded corners on pie/sunburst/treemap, the blur and select states,
  dirty-rectangle rendering, dataset filter/sort/aggregate/cluster, `registerLocale`,
  `ComposeOption`, high-contrast themes and decal patterns.
- https://apache.github.io/echarts-handbook/en/basics/release-note/v5-upgrade-guide —
  Migrating v4 → v5. Breaking: no default export, so `import * as echarts from
  'echarts'`; `echarts/src` becomes `echarts/lib`; canvas renderer, grid and aria are
  no longer implicit; bundled GeoJSON removed; IE8/VML dropped; y-axis line and ticks
  hidden by default; visualMap now loses to `itemStyle`; rich-text padding follows CSS
  order. Deprecated but working: `position`/`scale`/`origin`, `textFill` → `fill`,
  per-series select actions, `clockWise` → `clockwise`. echarts-gl, wordcloud and
  liquidfill need matching versions.

## Core concepts

- https://apache.github.io/echarts-handbook/en/concepts/chart-size — Container and
  sizing rules. `echarts.init()` normally takes its size from the DOM node's CSS
  width/height; when the node has none, pass `opts.width`/`opts.height`. Keep charts
  fluid by calling `echartsInstance.resize()` from a window `resize` handler or a
  `ResizeObserver`. Call `dispose()` before removing the node and re-init after
  re-inserting it, otherwise the instance leaks.
- https://apache.github.io/echarts-handbook/en/concepts/style — **Start here for "how
  do I change how this looks".** Four escalating levers: a theme
  (`echarts.init(dom, 'dark')`, custom themes registered with
  `echarts.registerTheme()` after fetching their JSON); a color palette (the `color`
  array, globally or per series); explicit `itemStyle`, `lineStyle`, `areaStyle` and
  `label` blocks, including gradients and shadows; and `visualMap` for data-driven
  encoding. Also covers the `emphasis` block for hover state and warns that the v3
  `normal`/`emphasis` nesting is deprecated.
- https://apache.github.io/echarts-handbook/en/concepts/dataset — **The recommended
  way to supply data.** Put the data in `dataset.source` instead of `series.data` so
  it can be shared by several series and swapped without touching the rest of the
  option. Accepts 2-D arrays and arrays of objects, row- or column-oriented.
  Covers `dataset.dimensions` with names and types (`'number'`, `'ordinal'`, `'time'`,
  `'float'`, `'int'`), `series.encode` mapping dimensions to x/y/tooltip channels,
  `series.seriesLayoutBy: 'column' | 'row'`, `series.datasetIndex` for multiple
  datasets, the default mapping rules per chart type, and an FAQ.
- https://apache.github.io/echarts-handbook/en/concepts/data-transform — Deriving a
  dataset from another declaratively: `outData = f(inputData)`. A dataset with
  `transform` plus `fromDatasetIndex`/`fromDatasetId` (and `fromTransformResult` when
  a transform emits several outputs). The built-in `filter` transform supports `>`,
  `>=`, `<`, `<=`, `=`, `!=`, `reg`, the `and`/`or`/`not` combinators and the `time`,
  `trim` and `number` parsers; the built-in `sort` transform orders by one or more
  dimensions. Also piping transforms, `transform.print` for debugging in dev builds,
  and registering external transforms such as `ecStat:regression`.
- https://apache.github.io/echarts-handbook/en/concepts/axis — The cartesian axes.
  How `xAxis` and `yAxis` decompose into `axisLine` (with optional arrow symbols),
  `axisTick`, `axisLabel` (formatter, alignment, rotation) and the axis name. Multiple
  x or y axes on one grid, their `position` (top/bottom, left/right) and the `offset`
  that keeps parallel axes apart. Ends with `dataZoom` for showing a window onto a
  long axis, illustrated by a dual-axis temperature/precipitation chart.
- https://apache.github.io/echarts-handbook/en/concepts/visual-map — Mapping data
  values onto visual channels — colour, symbol, symbol size, opacity — with the
  `visualMap` component, of which several may coexist. Covers `visualMap.type`,
  `dimension` (which dimension of a multi-dimensional item to read), `seriesIndex`,
  and the `inRange`/`outOfRange` style blocks. Two flavours: continuous, interpolating
  between `min` and `max`, and piecewise, split either automatically by `splitNumber`,
  explicitly by `pieces`, or by discrete `categories`.
- https://apache.github.io/echarts-handbook/en/concepts/legend — Placing and styling
  the legend, and when to use one. `legend.orient` for horizontal versus vertical,
  moving it to the bottom when width is tight, and `legend.type: 'scroll'` for many
  entries. Styling the background, text and `legend.icon`; on dark backgrounds use a
  light translucent layer. `legend.selected` holds the per-series visibility that
  clicking toggles. Advises distinct legend styles for mixed-type dual-axis charts,
  and dropping the legend for single-category data.
- https://apache.github.io/echarts-handbook/en/concepts/event — **Read before wiring
  up interactivity.** `myChart.on(name, handler)` and the filtered form
  `myChart.on(name, query, handler)`, where the query is a string like `'series'`,
  `'series.line'`, `'dataZoom'` or an object of `${mainType}Index`/`${mainType}Name`,
  `seriesIndex`, `name`, `dataType`, `element`. Mouse events: `click`, `dblclick`,
  `mousedown`, `mousemove`, `mouseup`, `mouseover`, `mouseout`, `globalout`,
  `contextmenu`. The handler's params carry `componentType`, `seriesType`,
  `seriesIndex`, `seriesName`, `name`, `dataIndex`, `data`, `dataType`, `value`,
  `color`. Component events, `myChart.dispatchAction({ type })` to trigger behaviour
  programmatically, and `myChart.getZr().on()` for clicks on blank canvas.

## Chart types — bar

- https://apache.github.io/echarts-handbook/en/how-to/chart-types/bar/basic-bar —
  `series.type: 'bar'` against a category axis. Progresses from one series to several,
  then to styling: `itemStyle` for colour, border, shadow and opacity; `barWidth`,
  `barMaxWidth` and `barMinHeight`; `barGap` between series and `barCategoryGap`
  between categories; `showBackground` with `backgroundStyle`. Note that the gap
  options are shared by all bar series on a grid, so set them on the last one.
- https://apache.github.io/echarts-handbook/en/how-to/chart-types/bar/stacked-bar —
  Stacking is one property: give the series that belong together the same `stack`
  string, and the bars in each category pile up so the total height reads as the sum.
  Recommends meaningful stack names (`'male'`, `'female'`) over generic ones once
  there are several independent stacks in the same chart.
- https://apache.github.io/echarts-handbook/en/how-to/chart-types/bar/bar-race — The
  animated ranking bar chart, as a checklist of ten settings: `realtimeSort: true`,
  `yAxis.inverse` to put the longest bar on top, `yAxis.animationDuration` (~300) and
  `animationDurationUpdate`, `yAxis.max` to cap how many bars are visible,
  `xAxis.max: 'dataMax'`, `series.label.valueAnimation`, series `animationDuration: 0`
  to skip the grow-from-zero opening, `animationDurationUpdate` ~3000, and a
  `setInterval` that feeds new numbers through `setOption()`.
- https://apache.github.io/echarts-handbook/en/how-to/chart-types/bar/waterfall — There
  is no waterfall series; simulate it with three bar series sharing `stack: 'all'`. A
  transparent helper series lifts each bar to its starting height, a second series
  carries the positive deltas and a third the negative ones in a contrasting colour.
  The page walks through the loop that turns raw increments into the three arrays,
  including the cumulative sums and the negative-value offsets.

## Chart types — line

- https://apache.github.io/echarts-handbook/en/how-to/chart-types/line/basic-line —
  `series.type: 'line'` on a category axis, then the same on two value axes by giving
  each datum a `[x, y]` pair. Styling through `lineStyle` (colour, width, dash,
  opacity), `itemStyle` for the symbols, and `label` with position and text style
  (`emphasis.label.show` reveals labels on hover only). Missing points are written as
  the string `'-'`, which breaks the line rather than drawing a zero.
- https://apache.github.io/echarts-handbook/en/how-to/chart-types/line/stacked-line —
  Same `stack` property as stacked bars, applied to line series. Because stacked lines
  are easy to mistake for ordinary overlapping lines, the page recommends adding
  `areaStyle: {}` so the bands between the lines are filled and the stacking is
  visible. Shows the plain and the filled version of the same data.
- https://apache.github.io/echarts-handbook/en/how-to/chart-types/line/area-line —
  Area charts are line series with `areaStyle`. An empty `areaStyle: {}` fills with a
  translucent version of the series colour; properties inside it override colour,
  gradient and opacity per series. Notes that areas read well only for a handful of
  series before they obscure each other.
- https://apache.github.io/echarts-handbook/en/how-to/chart-types/line/smooth-line —
  A one-property page: `smooth: true` on a line series replaces the straight segments
  with an interpolated curve. Everything else in the option stays as it is.
- https://apache.github.io/echarts-handbook/en/how-to/chart-types/line/step-line —
  Step (square-wave) lines, which connect points with horizontal and vertical
  segments only and so make abrupt changes obvious. The `step` property chooses where
  the corner falls between two points: `'start'`, `'end'` or `'middle'`. The example
  draws all three variants on one grid with a legend.

## Chart types — pie and scatter

- https://apache.github.io/echarts-handbook/en/how-to/chart-types/pie/basic-pie —
  `series.type: 'pie'` with data items of `{ value, name }`; the angles follow from
  the values. Covers `series.radius` as a percentage of the container's shorter side
  or as pixels, `series.stillShowZeroSum` (whether an all-zero dataset renders as
  equal slices or nothing) and `series.label.show` to suppress labels independently.
- https://apache.github.io/echarts-handbook/en/how-to/chart-types/pie/doughnut — A
  doughnut is a pie whose `radius` is `[inner, outer]`; a pie is the special case of
  inner radius 0. Covers `label` and `labelLine`, the `emphasis` block, and
  `avoidLabelOverlap` (default true). Second half builds the common pattern of showing
  the hovered sector's name and value in the hole, by toggling label visibility from
  the emphasis state.
- https://apache.github.io/echarts-handbook/en/how-to/chart-types/pie/rose — The rose
  (Nightingale) chart: a pie series with `roseType: 'area'`, so sectors share the same
  angle and encode their value in the radius instead. Every other pie option applies
  unchanged.
- https://apache.github.io/echarts-handbook/en/how-to/chart-types/scatter/basic-scatter
  — `series.type: 'scatter'`, first against a category axis, then in the usual form
  with two value axes and `[x, y]` data pairs. Customisation covers `symbol` (built-in
  shapes like `'circle'`, `'rect'`, `'triangle'`, an image URL, or an SVG path) and
  `symbolSize` as a number, a `[width, height]` array, or a callback that scales each
  point from its own value.

## Advanced how-tos

- https://apache.github.io/echarts-handbook/en/how-to/custom-series — Drawing shapes
  ECharts has no series for. Two routes: since v6 you can install a *registerable*
  custom series from npm (`apache/echarts-custom-series`) and use it by name, e.g.
  `type: 'custom'` with `renderItem: 'barRange'`; or write your own `renderItem`
  function that returns element descriptions. Covers `itemPayload` for passing
  configuration into a registered series and `encode` for mapping data dimensions to
  position and tooltip.
- https://apache.github.io/echarts-handbook/en/how-to/component-types/geo/svg-base-map
  — Using an SVG file instead of GeoJSON as a base map (v5.1.0+), registered with
  `echarts.registerMap(name, { svg })` and referenced from `geo.map` or
  `series-map.map`. Elements carrying a `name` attribute become interactive: styling
  through `geo.regions`, selection, emphasis and focus-blur, tooltips, labels, and
  events queried as `{ geoIndex: 0, name }`. Also `geo.roam` for zoom and pan,
  `layoutCenter`/`layoutSize`/`boundingCoords` for placement, how to put scatter,
  effectScatter, lines and custom series on top, and which SVG features are unsupported.
- https://apache.github.io/echarts-handbook/en/how-to/cross-platform/server —
  **Read before rendering charts outside a browser.** Server-side SVG:
  `echarts.init(null, null, { renderer: 'svg', ssr: true, width, height })` then
  `chart.renderToSVGString()`, which yields a small string with CSS-based entry
  animation. Server-side canvas: `echarts.setCanvasCreator()` with node-canvas, then
  `canvas.toBuffer('image/png')`. Adds `ssrClient.hydrate(container, options)`, the
  tiny runtime that restores basic interaction on server-rendered SVG, and a table
  comparing six rendering strategies against typical scenarios.
- https://apache.github.io/echarts-handbook/en/how-to/data/dynamic-data — Filling a
  chart after it exists. Async loading, either by fetching first and initialising once
  the data arrives, or by rendering empty styled axes immediately and calling
  `setOption()` when it lands. `showLoading()`/`hideLoading()` around the request. For
  live updates, call `setOption()` repeatedly — merging is by position, so give series
  a stable `name` so updates hit the intended one.
- https://apache.github.io/echarts-handbook/en/how-to/label/rich-text — Styled labels
  (v3.7+). Distinguishes the text block from text fragments and lists the properties
  available to both: `fontStyle`, `fontWeight`, `fontSize`, `fontFamily`, `color`,
  `textBorderColor`/`Width`, the `textShadow*` group, `lineHeight`, `width`, `height`,
  `padding`, `align`, `verticalAlign`, `backgroundColor`, `borderColor`/`Width`/
  `Radius`, the `shadow*` group, `position`, `distance` and `rotate`. The markup is
  `formatter: '{styleName|text}'` with the styles defined under `rich`; fragments lay
  out like CSS inline-blocks, which is how the page builds icons, rules, title blocks
  and small tables.
- https://apache.github.io/echarts-handbook/en/how-to/animation/transition — What
  happens on `setOption()`. ECharts diffs new data against old — matching on the item
  `name` — and animates position, scale and shape for added, updated and removed
  items. Entry is tuned with `animationDuration`, `animationEasing` and
  `animationDelay`; updates with the matching `*Update` options; a callback such as
  `animationDelay: idx => idx * 10` staggers items. Global `animation: false` and
  `animationThreshold` turn animation off for large data; `chart.on('rendered', ...)`
  reports completion.
- https://apache.github.io/echarts-handbook/en/how-to/interaction/drag — A worked
  example of dragging data points. Overlays circles from the `graphic` component onto
  a line chart's symbols, positioned with `convertToPixel()`; the `ondrag` handler
  reads the new pixel position back through `convertFromPixel()`, writes it into the
  data array and re-renders with `setOption()`. Adds `onmousemove`/`onmouseout`
  handlers that show coordinates through `dispatchAction` `showTip`/`hideTip` on a
  `triggerOn: 'none'` tooltip, plus a resize handler that recomputes the positions.
  Uses `echarts.util.map()` and `echarts.util.curry()`.
- https://apache.github.io/echarts-handbook/en/how-to/interaction/coarse-pointer —
  Intelligent pointer snapping (5.4.0+), on by default for mobile and off elsewhere.
  `opts.useCoarsePointer` forces it either way and `opts.pointerSize` sets the search
  radius (default 44 px, from the W3C touch-target guidance). Explains the two-stage
  algorithm — exact hit test first, then a spiral over angles and radii for the
  nearest intersecting element — and the AABB prefiltering that keeps it cheap. It is
  disabled for series in `large: true` mode.

## Best practices

- https://apache.github.io/echarts-handbook/en/best-practices/canvas-vs-svg —
  Choosing the renderer, passed as `{ renderer: 'canvas' | 'svg' }` to `init` (the
  full build registers both; tree-shaken builds must `echarts.use([CanvasRenderer])`
  or `SVGRenderer`). Canvas suits many elements and heavy effects — heat maps,
  large scatter and line plots, roughly beyond a thousand points — and is the only
  option for trail effects and heatmap blending. SVG suits memory-constrained mobile
  devices, many chart instances on a page, and zooming without blur. Since 5.3.0 the
  SVG renderer is 2–10× faster than before.
- https://apache.github.io/echarts-handbook/en/best-practices/aria — Accessibility.
  From v5 the `AriaComponent` must be imported explicitly. With `aria.show: true`
  ECharts generates a screen-reader description from the title, chart type, series
  names and values; the templates are `aria.general.withTitle` and
  `withoutTitle`, overridden per chart by `aria.description` or refined with
  `aria.label`. `aria.decal.show` and `aria.decal.decals` add pattern fills so series
  remain distinguishable without colour — for colour-blind readers and print.
- https://apache.github.io/echarts-handbook/en/best-practices/security — **Read this
  whenever chart content comes from user or database input.** ECharts does not
  sanitise anything. XSS reaches the page through `tooltip.formatter` and other
  HTML-rendering formatters, `toolbox.feature.dataView`, `tooltip.extraCssText`, and
  `javascript:`/`data:` URLs in `title.link`/`sublink`; `dataset.transform` regex
  filters open a ReDoS hole; `toolbox.feature.saveAsImage.name` becomes a filename.
  Mitigations: escape `<`, `>`, `&`, `"`, `'`; run untrusted HTML/CSS through a
  maintained sanitiser; sandbox in a restricted iframe; whitelist URL protocols; cap
  regex length and complexity.
- https://apache.github.io/echarts-handbook/en/meta/edit-guide — How to contribute to
  the handbook itself: markdown under `contents/en/` or `contents/zh/` plus the YAML
  nav files, Prettier formatting, live-preview code blocks and their layout
  directives, the `${optionPath}`, `${apiPath}` and `${lang}` link variables, heading
  IDs and cross-references. Documentation authoring only — no ECharts API content.
