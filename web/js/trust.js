(function () {
  const DENY_MATRIX = [
    { scenario: "No approval token on the request", layer: "policy (Cedar)", result: "DENY", note: "send_dispute_letter / fap_and_itemized require context.input.approval_token != \"\"" },
    { scenario: "Empty finding_ids on a dispute letter", layer: "policy (Cedar)", result: "DENY", note: "send_dispute_letter / file_escalation require finding_ids to be non-empty" },
    { scenario: "Recipient is an attacker-controlled address", layer: "policy (Cedar)", result: "DENY", note: "recipient_email must match a registered hospital billing domain or demo address" },
    { scenario: "Amount changed after human approval", layer: "lambda (input_hash)", result: "DENY", note: "the approved input is hashed at approval time; a re-hash mismatch at send time is refused, not silently sent" },
    { scenario: "Integrator role attempts a write action", layer: "policy (Cedar)", result: "DENY", note: "forbid_integrator_writes blocks case/action tools whenever principal role == \"integrator\"" },
    { scenario: "Patient disputes more than $10,000 without an advocate", layer: "policy (Cedar)", result: "DENY", note: "send_dispute_letter caps patient-role sends at $10,000; above that, only role == \"advocate\" is permitted" },
  ];

  function renderDenyMatrix() {
    const tbody = document.getElementById("deny-matrix-body");
    tbody.innerHTML = DENY_MATRIX.map((row) => `
      <tr class="result-${row.result.toLowerCase()}">
        <td>${CC.escapeHtml(row.scenario)}</td>
        <td>${CC.escapeHtml(row.layer)}</td>
        <td class="result">${CC.escapeHtml(row.result)}</td>
        <td>${CC.escapeHtml(row.note)}</td>
      </tr>
    `).join("");
  }

  async function renderPolicies() {
    const box = document.getElementById("policy-blocks");
    try {
      const data = await CC.fetchJSON("data/policies.json");
      const arnNote = document.getElementById("gateway-arn");
      arnNote.textContent = data.gateway_arn;
      box.innerHTML = Object.entries(data.policies).map(([name, text]) => `
        <div class="policy-block">
          <h4>${CC.escapeHtml(name)}.cedar</h4>
          <pre class="cedar">${CC.escapeHtml(text)}</pre>
        </div>
      `).join("");
    } catch (e) {
      box.innerHTML = `<p style="color:var(--red);">Policies not built yet — run <span class="mono">uv run web/scripts/build_data.py</span>.</p>`;
    }
  }

  async function renderScorecard() {
    const box = document.getElementById("scorecard-body");
    try {
      const sc = await CC.fetchJSON("data/engine-scorecard.json");
      document.getElementById("scorecard-summary").innerHTML = `
        ${sc.rules_exact_match_cases}/${sc.total_cases} cases with an exact rule-set match &middot;
        ${(sc.exact_amount_match_rate * 100).toFixed(0)}% exact disputable-dollar match &middot;
        ${(sc.fap_tier_accuracy * 100).toFixed(0)}% FAP tier accuracy &middot;
        ${sc.negative_control_false_positive_count} false positives on clean bills
      `;
      const rows = Object.entries(sc.per_rule).sort(([a], [b]) => a.localeCompare(b));
      box.innerHTML = rows.map(([rule, m]) => `
        <tr>
          <td class="mono">${CC.escapeHtml(rule)}</td>
          <td>${m.tp}</td>
          <td>${m.fp}</td>
          <td>${m.fn}</td>
          <td>${(m.precision * 100).toFixed(0)}%</td>
          <td>${(m.recall * 100).toFixed(0)}%</td>
        </tr>
      `).join("");
    } catch (e) {
      box.innerHTML = `<tr><td colspan="6">Scorecard not built yet.</td></tr>`;
    }
  }

  async function renderRefdata() {
    const box = document.getElementById("refdata-body");
    try {
      const rows = await CC.fetchJSON("data/refdata.json");
      box.innerHTML = rows.map((r) => `
        <tr>
          <td class="mono">${CC.escapeHtml(r.dataset)}</td>
          <td>${CC.escapeHtml(r.version)}</td>
          <td>${CC.escapeHtml(r.rows)}</td>
          <td>${CC.escapeHtml(r.retrieved)}</td>
          <td><a href="${CC.escapeHtml(r.url)}">${CC.escapeHtml(r.url)}</a></td>
        </tr>
      `).join("");
    } catch (e) {
      box.innerHTML = `<tr><td colspan="5">Refdata versions not built yet.</td></tr>`;
    }
  }

  renderDenyMatrix();
  renderPolicies();
  renderScorecard();
  renderRefdata();
})();
