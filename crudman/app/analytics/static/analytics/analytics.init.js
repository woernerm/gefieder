/*
 * Turns the divs the panel fragments deliver into ECharts instances.
 *
 * The fragments arrive over HTMX after the page has loaded, so initialisation hangs off
 * htmx:afterSwap rather than DOMContentLoaded alone. Colours follow Unfold's own
 * approach: they are read from the CSS custom properties, so a chart matches the admin
 * in either theme and is redrawn when the theme changes.
 */
(() => {
  const instances = new Map();
  const observers = new Map();

  const cssVar = (name, fallback) => {
    const value = getComputedStyle(document.documentElement)
      .getPropertyValue(name)
      .trim();
    return value || fallback;
  };

  const isDark = () => document.documentElement.classList.contains("dark");

  // Unfold's palette carries no categorical scale, so the series colours are a fixed
  // set chosen to stay legible on both backgrounds.
  const PALETTE = [
    "#0ea5e9", "#f97316", "#10b981", "#8b5cf6",
    "#ec4899", "#eab308", "#14b8a6", "#ef4444",
  ];

  const themeOptions = () => {
    const dark = isDark();
    const text = dark ? cssVar("--color-base-300", "#d4d4d8") : cssVar("--color-base-600", "#52525b");
    const line = dark ? cssVar("--color-base-700", "#3f3f46") : cssVar("--color-base-200", "#e4e4e7");

    return {
      color: PALETTE,
      textStyle: { color: text, fontFamily: "inherit" },
      legend: { textStyle: { color: text } },
      // Axis styling is deliberately not here: it is applied in applyTheme only to the
      // axes a chart actually declares. Merged in unconditionally it would give a pie
      // -- which has no axes at all -- an empty pair of default ones.
      axis: {
        axisLine: { lineStyle: { color: line } },
        axisLabel: { color: text },
        splitLine: { lineStyle: { color: line } },
      },
    };
  };

  const merge = (base, override) => {
    const result = { ...base };
    for (const [key, value] of Object.entries(override || {})) {
      const current = result[key];
      const mergeable =
        value && typeof value === "object" && !Array.isArray(value) &&
        current && typeof current === "object" && !Array.isArray(current);
      result[key] = mergeable ? merge(current, value) : value;
    }
    return result;
  };

  const applyTheme = (options) => {
    const { axis, ...theme } = themeOptions();
    const merged = merge(theme, options);

    // Only the axes the chart brought itself are styled, and an axis given as an array
    // is styled entry by entry -- a plain merge of object over array would drop it.
    for (const name of ["xAxis", "yAxis"]) {
      if (Array.isArray(options[name])) {
        merged[name] = options[name].map((entry) => merge(axis, entry));
      } else if (options[name]) {
        merged[name] = merge(axis, options[name]);
      }
    }
    return merged;
  };

  const render = (element) => {
    const raw = element.dataset.options;
    if (!raw) {
      return;
    }

    // A redrawn placeholder hands back a detached instance, so the old one is disposed
    // rather than left holding the previous canvas.
    const existing = instances.get(element);
    if (existing) {
      existing.dispose();
      observers.get(element)?.disconnect();
      observers.delete(element);
    }

    let options;
    try {
      options = JSON.parse(raw);
    } catch (error) {
      console.error("panels: could not parse the chart options", error);
      return;
    }

    const chart = echarts.init(element, null, { renderer: "canvas" });
    chart.setOption(applyTheme(options));
    instances.set(element, chart);

    // ECharts measures the container once, at init, and keeps drawing to that size. The
    // sidebar toggles by changing classes rather than by resizing the window, so a chart
    // laid out beside it would stay at its old width and sit off-centre. Observing the
    // element itself catches every layout change, whatever caused it.
    if (typeof ResizeObserver === "function") {
      const observer = new ResizeObserver(() => chart.resize());
      observer.observe(element);
      observers.set(element, observer);
    }
  };

  const renderAll = (root) => {
    for (const element of (root || document).querySelectorAll(".echarts-panel")) {
      render(element);
    }
  };

  const retheme = () => {
    for (const [element, chart] of instances) {
      const raw = element.dataset.options;
      if (raw) {
        chart.setOption(applyTheme(JSON.parse(raw)), true);
      }
    }
  };

  // Unfold emits UNFOLD["SCRIPTS"] into the head without defer, so this runs before
  // there is a body to listen on. Everything touching the document therefore waits for
  // DOMContentLoaded; reaching for document.body at head time would throw and take the
  // whole script -- charts included -- down with it.
  const wire = () => {
    renderAll();

    // Each panel swaps in on its own, so only the fragment just delivered is scanned.
    document.body.addEventListener("htmx:afterSwap", (event) => renderAll(event.target));

    // Unfold toggles the theme by putting the class on <html>; there is no event for it.
    new MutationObserver(retheme).observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["class"],
    });
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", wire);
  } else {
    // Already parsed: a deferred or late-injected script would never see the event.
    wire();
  }

  window.addEventListener("resize", () => {
    for (const chart of instances.values()) {
      chart.resize();
    }
  });
})();
