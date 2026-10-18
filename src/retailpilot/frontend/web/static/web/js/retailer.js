// Retailer dashboard: tab switching and Plotly charts.
(function () {
  "use strict";

  const tabs = document.querySelectorAll("button.tab[data-tab]");
  const panels = document.querySelectorAll("[data-panel]");

  function showTab(name) {
    tabs.forEach((t) => {
      const active = t.dataset.tab === name;
      t.classList.toggle("active", active);
      t.setAttribute("aria-selected", String(active));
      t.tabIndex = active ? 0 : -1;
    });
    panels.forEach((p) => { p.hidden = p.dataset.panel !== name; });
    // Plotly sizes charts when they are created; charts in hidden tabs need a resize once shown.
    if (window.Plotly) {
      document.querySelectorAll(".chart").forEach((node) => {
        if (node.offsetParent !== null) Plotly.Plots.resize(node);
      });
    }
  }
  tabs.forEach((tab, index) => {
    tab.addEventListener("click", () => showTab(tab.dataset.tab));
    tab.addEventListener("keydown", (event) => {
      let next = index;
      if (event.key === "ArrowRight") next = (index + 1) % tabs.length;
      else if (event.key === "ArrowLeft") next = (index - 1 + tabs.length) % tabs.length;
      else if (event.key === "Home") next = 0;
      else if (event.key === "End") next = tabs.length - 1;
      else return;
      event.preventDefault();
      tabs[next].focus();
      showTab(tabs[next].dataset.tab);
    });
  });

  const chartData = document.getElementById("charts");
  const charts = chartData ? JSON.parse(chartData.textContent) : {};

  // Turns a chart's data into plain text so the assistant can explain the picture the reader is looking at.
  function describeChart(figure, title) {
    const lines = figure.data.map((trace) => {
      const horizontal = trace.orientation === "h";
      const xs = trace.x || [];
      const ys = trace.y || [];
      const rows = [];
      for (let i = 0; i < Math.min(Math.max(xs.length, ys.length), 15); i++) {
        const label = horizontal ? (ys[i] || trace.name || "") : xs[i];
        const value = horizontal ? xs[i] : ys[i];
        rows.push(`${label}: ${value}`);
      }
      return rows.join("; ");
    });
    return `Chart: ${title}\n${lines.join("\n")}`;
  }

  // "Ask RetailPilot about this graph" sends the chart's title and numbers to the chat panel.
  document.addEventListener("click", (event) => {
    const button = event.target.closest("[data-chart-ask]");
    if (!button || !charts[button.dataset.chartAsk]) return;
    const title = button.closest(".card").querySelector("h3").textContent;
    // The product and city go first, so the assistant knows which listing and market the numbers describe.
    const pageContext = document.querySelector("[data-page-context]").dataset.pageContext;
    document.dispatchEvent(new CustomEvent("rp:ask", {
      detail: {
        question: `Explain this graph: "${title}". What does it show, and what should I do about it?`,
        context: `${pageContext}\n\n${describeChart(charts[button.dataset.chartAsk], title)}`,
      },
    }));
  });

  if (!window.Plotly) return;
  Object.entries(charts).forEach(([name, figure]) => {
    const node = document.getElementById("chart-" + name);
    if (node) Plotly.newPlot(node, figure.data, figure.layout, { responsive: true, displayModeBar: false });
  });
})();
