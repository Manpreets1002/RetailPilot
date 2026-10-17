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
  if (!chartData || !window.Plotly) return;
  const charts = JSON.parse(chartData.textContent);
  Object.entries(charts).forEach(([name, figure]) => {
    const node = document.getElementById("chart-" + name);
    if (node) Plotly.newPlot(node, figure.data, figure.layout, { responsive: true, displayModeBar: false });
  });
})();
