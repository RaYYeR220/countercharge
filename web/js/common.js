/* Shared helpers for the Countercharge claims-desk demo site. */

const CC = (() => {
  const reduceMotion = () =>
    window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function centsToUsd(cents) {
    const n = (cents || 0) / 100;
    return n.toLocaleString("en-US", { style: "currency", currency: "USD" });
  }

  function escapeHtml(str) {
    return String(str ?? "").replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));
  }

  async function fetchJSON(path) {
    const res = await fetch(path, { cache: "no-store" });
    if (!res.ok) throw new Error(`fetch failed: ${path} (${res.status})`);
    return res.json();
  }

  function ruleLabel(ruleId) {
    const labels = {
      DUPLICATE: "Duplicate charge",
      NCCI_PTP: "NCCI bundling edit",
      MUE: "Units over Medicare limit",
      ARITHMETIC: "Arithmetic error",
      EOB_BALANCE_BILLING: "Balance billing above EOB",
      NSA_EMERGENCY: "No Surprises Act — emergency",
      NSA_GFE: "No Surprises Act — estimate",
      CASH_PRICE: "Above hospital cash price",
      FAP_501R: "Charity care (501(r))",
      MEDICARE_BENCHMARK: "Medicare benchmark",
    };
    return labels[ruleId] || ruleId;
  }

  /** Type `text` into `el` character by character; resolves when done.
   * Respects prefers-reduced-motion by rendering instantly. */
  function typeInto(el, text, { speed = 14 } = {}) {
    return new Promise((resolve) => {
      if (reduceMotion()) {
        el.textContent = text;
        resolve();
        return;
      }
      let i = 0;
      (function tick() {
        if (i <= text.length) {
          el.textContent = text.slice(0, i);
          i++;
          setTimeout(tick, speed + Math.random() * speed);
        } else {
          resolve();
        }
      })();
    });
  }

  return { reduceMotion, centsToUsd, escapeHtml, fetchJSON, ruleLabel, typeInto };
})();
