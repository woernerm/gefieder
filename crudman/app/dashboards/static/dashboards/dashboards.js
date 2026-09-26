// Draws a panel from the payload the dashboards service (sqlmesh/dashboards.py) makes of
// it. The one drawing there is: a dashboard page and a notebook cell both load this file,
// so a chart looks in a notebook exactly as it will on the dashboard.
//
// Colours come from the page -- the palette as dashboards.css maps it for light and dark --
// so nothing here names one, and a theme switch redraws every chart in the other mode.
const Dashboards = (() => {
  const style = (name) => getComputedStyle(document.body).getPropertyValue(name).trim();

  const number = (value) =>
    typeof value === "number" ? value.toLocaleString(undefined, { maximumFractionDigits: 2 }) : value ?? "–";

  const scaled = (value, base, units) => {
    let step = 0;
    while (Math.abs(value) >= base && step < units.length - 1) { value /= base; step++; }
    return `${number(value)} ${units[step]}`;
  };

  const bytes = (value) => scaled(value, 1024, ["B", "KiB", "MiB", "GiB", "TiB", "PiB"]);

  // Panel(unit=...) as the reader sees it: the known units scale, any other text follows
  // the number.
  const UNITS = {
    bytes,
    "bytes/s": (value) => `${bytes(value)}/s`,
    percent: (value) => `${number(value)} %`,
  };

  const formatter = (unit) =>
    !unit ? number : UNITS[unit] || ((value) => `${number(value)} ${unit}`);

  function theme() {
    const text = style("--dashboards-text");
    const muted = style("--dashboards-muted");
    const line = style("--dashboards-line");
    const axis = {
      axisLine: { lineStyle: { color: line } },
      axisTick: { lineStyle: { color: line } },
      axisLabel: { color: muted },
      splitLine: { lineStyle: { color: line } },
    };
    return {
      color: style("--app-series").split(",").map((color) => color.trim()),
      backgroundColor: "transparent",
      textStyle: { color: text, fontFamily: getComputedStyle(document.body).fontFamily },
      legend: { textStyle: { color: muted } },
      // ECharts outlines a label drawn outside its slice in the slice's colour, which on a
      // dark page reads as a smudge rather than a word.
      pie: { label: { color: text, textBorderWidth: 0 } },
      funnel: { label: { color: text, textBorderWidth: 0 } },
      categoryAxis: axis, valueAxis: axis, timeAxis: axis, logAxis: axis,
      tooltip: {
        backgroundColor: style("--dashboards-raised"),
        borderColor: line,
        textStyle: { color: text },
      },
    };
  }

  // Every chart drawn, by its box, so a theme switch can draw it again and a box that has
  // left the page -- a panel drawn anew -- lets go of its chart.
  const charts = new Map();

  function echart(element, payload) {
    const box = element.appendChild(document.createElement("div"));
    box.className = "dashboards-chart";
    const option = structuredClone(payload.option);
    if (payload.unit) {
      const format = formatter(payload.unit);
      for (const axis of [option.xAxis, option.yAxis].flat()) {
        if (axis && !["category", "time"].includes(axis.type) && !axis.axisLabel?.formatter) {
          axis.axisLabel = { ...axis.axisLabel, formatter: format };
        }
      }
      option.tooltip = { valueFormatter: format, ...option.tooltip };
    }
    const init = () => {
      echarts.getInstanceByDom(box)?.dispose();
      const chart = echarts.init(box, theme());
      chart.setOption(option);
      // Asks the page to set a filter; one without that filter -- a notebook -- ignores it.
      if (payload.click) {
        chart.on("click", (event) => box.dispatchEvent(new CustomEvent("dashboards:pick", {
          bubbles: true, detail: { filter: payload.click, value: event.name },
        })));
      }
    };
    init();
    charts.set(box, init);
    new ResizeObserver(() => echarts.getInstanceByDom(box)?.resize()).observe(box);
  }

  // Tabulator: sorted by a click on a heading, filtered by typing under it. Fields are named
  // by position, a column's own name possibly holding a dot, which Tabulator reads as a path.
  function table(element, { columns, rows, total, unit }) {
    const format = formatter(unit);
    const numeric = columns.map((_, i) => rows.some((row) => typeof row[i] === "number"));
    new Tabulator(element.appendChild(document.createElement("div")), {
      data: rows.map((row) => Object.fromEntries(row.map((value, i) => [`c${i}`, value]))),
      columns: columns.map((title, i) => ({
        title,
        field: `c${i}`,
        headerFilter: "input",
        headerFilterPlaceholder: "Filter",
        sorter: numeric[i] ? "number" : "alphanum",
        hozAlign: numeric[i] ? "right" : "left",
        // Text is set as text; only the number, formatted here, is written as markup.
        formatter: numeric[i] ? (cell) => format(cell.getValue()) : "plaintext",
        tooltip: true,
        // A long text -- a query, a description -- is cut at this and shown in full on
        // hover, rather than widening the table past the page.
        maxInitialWidth: 480,
      })),
      layout: "fitDataStretch",
      maxHeight: "26rem",
      placeholder: "No rows",
    });
    if (total > rows.length) {
      const note = element.appendChild(document.createElement("p"));
      note.className = "dashboards-note";
      note.textContent = `The first ${rows.length} of ${total} rows. The download holds them all.`;
    }
  }

  function stat(element, { value, unit }) {
    const big = element.appendChild(document.createElement("div"));
    big.className = "dashboards-stat";
    big.textContent = value === null ? "–" : formatter(unit)(value);
  }

  function text(element, payload) {
    for (const paragraph of payload.text.split(/\n\s*\n/)) {
      element.appendChild(document.createElement("p")).textContent = paragraph;
    }
  }

  // A file the browser plays itself, or a page made to embed one; any other scheme than
  // http(s) is not a video.
  function video(element, { url }) {
    if (!/^(https?:\/\/|\/)/.test(url)) return failed(element, { message: `Not a video address: ${url}` });
    const file = /\.(mp4|webm|ogg)(\?|#|$)/i.test(url);
    const player = element.appendChild(document.createElement(file ? "video" : "iframe"));
    player.className = "dashboards-video";
    player.src = url;
    if (file) player.controls = true;
    else player.allowFullscreen = true;
  }

  function failed(element, { message }) {
    const note = element.appendChild(document.createElement("p"));
    note.className = "dashboards-error";
    note.textContent = message;
  }

  const KINDS = { echarts: echart, table, stat, text, video, error: failed };

  function draw(element, payload) {
    element.replaceChildren();
    (KINDS[payload.kind] || failed)(element, payload);
  }

  // Panels with their titles, as a notebook shows a chart, a panel or a whole dashboard,
  // a run of numbers in a row of its own as the dashboard page lays them out.
  function drawAll(container, payloads) {
    container.className = "dashboards-panels";
    let stats = null;
    for (const payload of payloads) {
      if (payload.kind !== "stat") stats = null;
      else if (!stats) stats = container.appendChild(Object.assign(document.createElement("div"), { className: "dashboards-stats" }));
      const panel = (stats || container).appendChild(document.createElement("section"));
      panel.className = `dashboards-panel ${payload.kind}${payload.wide ? " wide" : ""}`;
      if (payload.title) panel.appendChild(document.createElement("h2")).textContent = payload.title;
      draw(panel.appendChild(document.createElement("div")), payload);
    }
  }

  // A theme switch: the admin panel's class on <html>, JupyterLab's attribute on <body>.
  const redraw = () => {
    for (const [box, init] of charts) box.isConnected ? init() : charts.delete(box);
  };
  const observer = new MutationObserver(redraw);
  observer.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
  observer.observe(document.body, { attributes: true, attributeFilter: ["data-jp-theme-light"] });

  // --- a dashboard page -------------------------------------------------------------
  // Each panel arrives from the server as its payload in a JSON script (panel.html), and is
  // drawn once it is in the page.
  document.body.addEventListener("htmx:afterSwap", (event) => {
    for (const data of event.detail.target.querySelectorAll("script[type='application/json']")) {
      draw(data.parentElement, JSON.parse(data.textContent));
    }
  });

  const form = document.getElementById("filters");
  if (form) {
    // The address bar says what is picked, so a link opens the same view. Replaced rather
    // than pushed: going back leaves the dashboard rather than undoing one filter.
    form.addEventListener("change", () => {
      const picked = [...new FormData(form)].filter(([, value]) => value !== "");
      history.replaceState(null, "", `?${new URLSearchParams(picked)}`);
    });

    // A click on a chart sets its filter; clicking what is already picked lets it go again.
    document.body.addEventListener("dashboards:pick", ({ detail }) => {
      const select = form.elements[detail.filter];
      if (!select) return;
      select.value = select.value === String(detail.value) ? "" : String(detail.value);
      select.dispatchEvent(new Event("change", { bubbles: true }));
    });

    // A download is of what the page shows, so it carries the filters picked.
    document.body.addEventListener("click", (event) => {
      const link = event.target.closest("a[data-download]");
      if (link) link.search = location.search;
    });
  }

  return { draw, drawAll };
})();
