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

  const cssVar = (name, fallback) => {
    const value = getComputedStyle(document.documentElement)
      .getPropertyValue(name)
      .trim();
    return value || fallback;
  };

  const isDark = () =>
    document.querySelector("html").classList.contains("dark");

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
      xAxis: {
        axisLine: { lineStyle: { color: line } },
        axisLabel: { color: text },
        splitLine: { lineStyle: { color: line } },
      },
      yAxis: {
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

  // The axis defaults have to reach each entry of an axis array too, which a plain merge
  // of object over array would drop.
  const applyTheme = (options) => {
    const theme = themeOptions();
    const merged = merge(theme, options);
    for (const axis of ["xAxis", "yAxis"]) {
      if (Array.isArray(options[axis])) {
        merged[axis] = options[axis].map((entry) => merge(theme[axis], entry));
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

  document.addEventListener("DOMContentLoaded", () => renderAll());

  // Each panel swaps in on its own, so only the fragment just delivered is scanned.
  document.body.addEventListener("htmx:afterSwap", (event) => renderAll(event.target));

  // Unfold toggles the theme by putting the class on <html>; there is no event for it.
  new MutationObserver(retheme).observe(document.querySelector("html"), {
    attributes: true,
    attributeFilter: ["class"],
  });

  window.addEventListener("resize", () => {
    for (const chart of instances.values()) {
      chart.resize();
    }
  });
})();
