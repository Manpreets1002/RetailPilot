// Retailer dashboard: tab switching and Plotly charts.
(function () {
  "use strict";

  const tabs = document.querySelectorAll(".tab");
  const panels = document.querySelectorAll("[data-panel]");

  function showTab(name) {
    tabs.forEach((t) => t.classList.toggle("active", t.dataset.tab === name));
    panels.forEach((p) => { p.hidden = p.dataset.panel !== name; });
    // Plotly sizes charts when they are created; charts in hidden tabs need a resize once shown.
    if (window.Plotly) {
      document.querySelectorAll(".chart").forEach((node) => {
        if (node.offsetParent !== null) Plotly.Plots.resize(node);
      });
    }
  }
  tabs.forEach((tab) => tab.addEventListener("click", () => showTab(tab.dataset.tab)));

  const chartData = document.getElementById("charts");
  if (!chartData || !window.Plotly) return;
  const charts = JSON.parse(chartData.textContent);
  Object.entries(charts).forEach(([name, figure]) => {
    const node = document.getElementById("chart-" + name);
    if (node) Plotly.newPlot(node, figure.data, figure.layout, { responsive: true, displayModeBar: false });
  });
})();
