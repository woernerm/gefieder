// Draws a panel from the payload the dashboards service (sqlmesh/dashboards.py) makes of
// it. The one drawing there is: a dashboard page and a notebook cell both load this file,
// so a chart looks in a notebook exactly as it will on the dashboard.
//
// Colours come from the page -- the palette as dashboards.css maps it for light and dark --
// so nothing here names one, and a theme switch recolours every chart for the other mode.
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

  // Every chart drawn, so a theme switch can recolour it; one that has left the page -- a
  // panel drawn anew -- is let go of then.
  const charts = new Set();

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
    const chart = echarts.init(box, theme());
    chart.setOption(option);
    // Asks the page to set a filter; one without that filter -- a notebook -- ignores it.
    if (payload.click) {
      chart.on("click", (event) => box.dispatchEvent(new CustomEvent("dashboards:pick", {
        bubbles: true, detail: { filter: payload.click, value: event.name },
      })));
    }
    charts.add(chart);
    new ResizeObserver(() => chart.resize()).observe(box);
  }

  // How a column filter compares, by the kind of column; "any of" is the list of values
  // beside it, which every column has.
  const OPERATORS = {
    number: { "=": (a, b) => a === b, "≠": (a, b) => a !== b, ">": (a, b) => a > b,
      "≥": (a, b) => a >= b, "<": (a, b) => a < b, "≤": (a, b) => a <= b },
    text: { contains: (a, b) => a.includes(b), "=": (a, b) => a === b, "≠": (a, b) => a !== b,
      "starts with": (a, b) => a.startsWith(b), "ends with": (a, b) => a.endsWith(b) },
  };

  const make = (tag, properties = {}, ...children) => {
    const node = Object.assign(document.createElement(tag), properties);
    node.append(...children);
    return node;
  };

  // A column's filter menu, as Grafana's: a condition, and the values to keep, searchable.
  function filterMenu(filter, values, refresh) {
    const operator = make("select", {}, ...Object.keys(filter.operators).map((name) =>
      make("option", { value: name, textContent: name, selected: name === filter.operator })));
    const operand = make("input", { placeholder: "Value", value: filter.operand });
    const search = make("input", { placeholder: "Search values" });
    const boxes = values.map((value) => make("input", {
      type: "checkbox", value, checked: !filter.anyOf || filter.anyOf.has(value),
    }));
    const list = make("div", { className: "dashboards-values" },
      ...boxes.map((box) => make("label", {}, box, box.value || "(empty)")));
    const every = make("button", { type: "button", textContent: "All / none" });
    const clear = make("button", { type: "button", textContent: "Clear" });
    const apply = () => {
      filter.operator = operator.value;
      filter.operand = operand.value;
      const kept = boxes.filter((box) => box.checked).map((box) => box.value);
      filter.anyOf = kept.length === boxes.length ? null : new Set(kept);
      refresh();
    };
    operator.addEventListener("change", apply);
    operand.addEventListener("input", apply);
    list.addEventListener("change", apply);
    search.addEventListener("input", () => boxes.forEach((box) => {
      box.parentElement.hidden = !box.value.toLowerCase().includes(search.value.toLowerCase());
    }));
    every.addEventListener("click", () => {
      const all = !boxes.every((box) => box.checked);
      boxes.forEach((box) => { box.checked = all; });
      apply();
    });
    clear.addEventListener("click", () => {
      operand.value = "";
      boxes.forEach((box) => { box.checked = true; });
      apply();
    });
    return make("div", { className: "dashboards-filter" },
      make("div", {}, operator, operand), search, list, make("div", {}, every, clear));
  }

  // Tabulator: sorted by a click on a heading, filtered through the menu beside it. Fields
  // are named by position, a column's own name possibly holding a dot, which Tabulator
  // reads as a path.
  function table(element, { columns, rows, total, unit }) {
    const format = formatter(unit);
    const numeric = columns.map((_, i) => rows.some((row) => typeof row[i] === "number"));
    const filters = columns.map((_, i) => {
      const operators = OPERATORS[numeric[i] ? "number" : "text"];
      return { operators, operator: Object.keys(operators)[0], operand: "", anyOf: null };
    });
    const text = (value) => String(value ?? "");
    const keeps = (data) => filters.every((filter, i) => {
      const value = data[`c${i}`];
      if (filter.anyOf && !filter.anyOf.has(text(value))) return false;
      if (filter.operand === "") return true;
      return numeric[i]
        ? value !== null && filter.operators[filter.operator](value, Number(filter.operand))
        : filter.operators[filter.operator](text(value).toLowerCase(), filter.operand.toLowerCase());
    });
    const grid = new Tabulator(element.appendChild(document.createElement("div")), {
      data: rows.map((row) => Object.fromEntries(row.map((value, i) => [`c${i}`, value]))),
      columns: columns.map((title, i) => ({
        title,
        field: `c${i}`,
        headerPopup: () => filterMenu(filters[i], [...new Set(rows.map((row) => text(row[i])))].sort(
          (a, b) => a.localeCompare(b, undefined, { numeric: true })), () => {
          grid.setFilter(keeps);
          grid.getColumn(`c${i}`).getElement().classList.toggle("filtered",
            filters[i].operand !== "" || filters[i].anyOf !== null);
        }),
        headerPopupIcon: '<span class="material-symbols-outlined md-18">filter_list</span>',
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
      panel.className = `dashboards-panel${payload.wide ? " wide" : ""}`;
      panel.dataset.kind = payload.kind;
      if (payload.title) panel.appendChild(document.createElement("h2")).textContent = payload.title;
      draw(panel.appendChild(document.createElement("div")), payload);
    }
  }

  // A theme switch: the admin panel's class on <html>, JupyterLab's attribute on <body>.
  const recolour = () => {
    for (const chart of charts) {
      if (chart.getDom().isConnected) chart.setTheme(theme());
      else { chart.dispose(); charts.delete(chart); }
    }
  };
  const observer = new MutationObserver(recolour);
  observer.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
  observer.observe(document.body, { attributes: true, attributeFilter: ["data-jp-theme-light"] });

  // --- a dashboard page -------------------------------------------------------------
  // Each panel arrives from the server as its payload in a JSON script (views.PanelView), and is
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

    // The time range, as Grafana's picker offers it: a quick choice, or from and to typed
    // or picked from a calendar. Panels reading it draw again on "dashboards:time".
    const field = (name, kind = "name") => form.querySelector(`[${kind}="${name}"]`);
    const label = () => {
      const [from, to] = [field("from").value, field("to").value];
      const quick = to === "now" && field(from, "data-range");
      field("", "data-time-label").textContent = quick ? quick.textContent : `${from} to ${to}`;
    };
    const setTime = (from, to) => {
      field("from").value = from || "now-6h";
      field("to").value = to || "now";
      label();
      form.dispatchEvent(new Event("change"));
      document.body.dispatchEvent(new Event("dashboards:time"));
    };
    if (field("from")) {
      label();
      form.addEventListener("click", (event) => {
        const quick = event.target.closest("[data-range]");
        if (quick) setTime(quick.dataset.range, "now");
        if (event.target.closest("[data-apply]")) {
          setTime(field("from", "data-edit").value.trim(), field("to", "data-edit").value.trim());
        }
        const calendar = event.target.closest("[data-calendar]");
        if (calendar) field(calendar.dataset.calendar, "data-picker").showPicker();
      });
      form.addEventListener("change", (event) => {
        const picker = event.target.dataset?.picker;
        if (picker) field(picker, "data-edit").value = event.target.value.replace("T", " ");
      });
      field("from", "data-edit").value = field("from").value;
      field("to", "data-edit").value = field("to").value;
    }

    // Every panel draws again at the interval picked, a relative time range moving with it.
    const seconds = { s: 1, m: 60, h: 3600 };
    let timer;
    const schedule = () => {
      clearInterval(timer);
      const [, count, unit] = form.elements.refresh.value.match(/^(\d+)([smh])$/) || [];
      if (count) timer = setInterval(() => document.body.dispatchEvent(new Event("dashboards:refresh")),
        count * seconds[unit] * 1000);
    };
    form.elements.refresh.addEventListener("change", schedule);
    schedule();
  }

  // Panels glide to where a changed width puts them, rather than jumping there: each one's
  // last place is remembered, and a move is played back from it.
  const panels = document.querySelector(".dashboards-panels");
  if (panels) {
    const places = new Map();
    new ResizeObserver(() => {
      for (const panel of panels.querySelectorAll(".dashboards-panel")) {
        // Offsets rather than the viewport's coordinates, which scrolling changes too.
        const [x, y] = [panel.offsetLeft, panel.offsetTop];
        const [lastX, lastY] = places.get(panel) ?? [x, y];
        places.set(panel, [x, y]);
        if (lastX === x && lastY === y) continue;
        panel.animate([{ transform: `translate(${lastX - x}px, ${lastY - y}px)` }, { transform: "none" }],
          { duration: 250, easing: "ease-out" });
      }
    }).observe(panels);
  }

  return { draw, drawAll };
})();
