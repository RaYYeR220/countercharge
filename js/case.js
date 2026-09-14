(function () {
  const CASES = [
    { id: "multi_01_selfpay_nyp", label: "Multi-error ER bill" },
    { id: "fap_free_01", label: "Charity care — FREE" },
    { id: "nsa_emergency_01", label: "No Surprises Act" },
    { id: "adversarial_exfil_email", label: "Adversarial — injection" },
  ];

  const params = new URLSearchParams(location.search);
  const requested = params.get("id");
  const activeId = CASES.some((c) => c.id === requested) ? requested : CASES[0].id;

  function renderTabs() {
    const tabs = document.getElementById("case-tabs");
    tabs.innerHTML = CASES.map((c) => {
      const selected = c.id === activeId;
      return `<a class="case-tab" href="case.html?id=${c.id}" aria-selected="${selected}">${CC.escapeHtml(c.label)}<span class="cat">${c.id}</span></a>`;
    }).join("");
  }

  function lineFindings(report, lineId) {
    const hits = [];
    for (const f of report.findings) {
      if (f.line_ids && f.line_ids.includes(lineId)) hits.push({ f, advisory: false });
    }
    for (const f of report.advisory) {
      if (f.line_ids && f.line_ids.includes(lineId)) hits.push({ f, advisory: true });
    }
    return hits;
  }

  function renderBillPanel(payload) {
    const { case: c, report } = payload;
    const bill = c.bill;

    const scanEl = document.getElementById("bill-scan");
    scanEl.innerHTML = `
      <img src="assets/bills/${c.case_id}.png" alt="Rendered bill scan for case ${c.case_id}" />
      <figcaption>Real rendered bill scan — <span class="mono">evals/corpus/rendered/${c.case_id}.png</span></figcaption>
    `;

    const rows = bill.lines.map((line) => {
      const hits = lineFindings(report, line.line_id);
      // A finding's amount is proven against one specific target line
      // (evidence.target_key) — e.g. for a duplicate pair, the copy, not
      // the original. Only that line gets the red-pen strike/circle;
      // a line merely referenced (the original) gets a soft cross-reference
      // note instead, so the same overcharge isn't shown as struck twice.
      const targeted = hits.filter((h) => !h.advisory && h.f.disputable && h.f.evidence.target_key === line.line_id);
      const referencedOnly = hits.filter((h) => !h.advisory && h.f.disputable && h.f.evidence.target_key !== line.line_id);
      const advisoryOnly = hits.filter((h) => h.advisory);
      const flagged = targeted.length > 0;
      const cls = flagged ? "flagged" : "";
      const descClass = flagged ? "desc strike circle" : "desc";
      let marks = "";
      for (const h of targeted) {
        marks += `<span class="flagmark">▸ ${CC.escapeHtml(CC.ruleLabel(h.f.rule_id))} — ${CC.centsToUsd(h.f.amount_cents)} — DISPUTE</span>`;
      }
      for (const h of referencedOnly) {
        marks += `<span class="flagmark" style="color:var(--ink-soft);">↳ referenced by ${CC.escapeHtml(CC.ruleLabel(h.f.rule_id))} finding (see disputed line)</span>`;
      }
      for (const h of advisoryOnly) {
        marks += `<span class="flagmark" style="color:var(--ink-soft);">· ${CC.escapeHtml(h.f.title)} (advisory)</span>`;
      }
      return `<tr class="${cls}">
        <td class="${descClass}">${CC.escapeHtml(line.code)} ${CC.escapeHtml(line.description)}${line.units > 1 ? ` &times;${line.units}` : ""}${marks}</td>
        <td class="amt">${CC.centsToUsd(line.charge_cents)}</td>
      </tr>`;
    }).join("");

    const balanceFindings = report.findings.filter((f) => f.disputable && (!f.line_ids || f.line_ids.length === 0));
    const balanceHtml = balanceFindings.map((f) => `
      <div class="balance-flag">▸ ${CC.escapeHtml(f.title)} — ${CC.centsToUsd(f.amount_cents)} — ${CC.escapeHtml(f.detail)}</div>
    `).join("");

    const form = document.getElementById("carbon-form");
    form.innerHTML = `
      <div class="cf-head">
        <span>ACCT ${CC.escapeHtml(bill.account_no)}</span>
        <span>${CC.escapeHtml(bill.provider.name)}</span>
        <span>DOS ${CC.escapeHtml(bill.statement_date)}</span>
      </div>
      <table class="cf-lines">
        <tbody>${rows}</tbody>
        <tfoot>
          <tr><td>TOTAL CHARGES</td><td class="amt">${CC.centsToUsd(bill.totals.charges_cents)}</td></tr>
          <tr><td>PATIENT BALANCE</td><td class="amt">${CC.centsToUsd(bill.totals.patient_balance_cents)}</td></tr>
        </tfoot>
      </table>
      ${balanceHtml}
    `;
  }

  function findingCard(f, advisory) {
    return `
      <div class="finding ${advisory || !f.disputable ? "advisory" : ""}">
        <div class="f-top">
          <span class="rule-badge">${CC.escapeHtml(f.rule_id)}</span>
          <span class="f-amount">${f.amount_cents ? CC.centsToUsd(f.amount_cents) : "—"}</span>
        </div>
        <h4>${CC.escapeHtml(f.title)}${!advisory && !f.disputable ? ' <span class="mono" style="font-size:10px;font-weight:400;color:var(--ink-soft);">(not disputable — eligibility amount)</span>' : ""}</h4>
        <p class="detail">${CC.escapeHtml(f.detail)}</p>
        <div class="citation">${CC.escapeHtml(f.citation.dataset)} &middot; ${CC.escapeHtml(f.citation.version)} &middot; ${CC.escapeHtml(JSON.stringify(f.citation.record))}</div>
      </div>
    `;
  }

  function renderFindingsPanel(payload) {
    const { case: c, report } = payload;
    const list = document.getElementById("findings-list");
    if (!report.findings.length) {
      list.innerHTML = `<p style="color:var(--ink-soft);font-size:14px;">No findings on this bill.</p>`;
    } else {
      list.innerHTML = report.findings.map((f) => findingCard(f, false)).join("");
    }

    const adv = document.getElementById("advisory-list");
    const advBox = document.getElementById("advisory-box");
    if (report.advisory.length) {
      advBox.hidden = false;
      adv.innerHTML = report.advisory.map((f) => findingCard(f, true)).join("");
    } else {
      advBox.hidden = true;
    }

    const totals = document.getElementById("totals-summary");
    const fapTier = report.fap ? report.fap.tier : "N/A";
    const fapPct = report.fap ? `${report.fap.fpl_percent}% FPL` : "no household data";
    totals.innerHTML = `
      <div class="item"><span class="k">Patient balance</span><br /><span class="v">${CC.centsToUsd(c.bill.totals.patient_balance_cents)}</span></div>
      <div class="item"><span class="k">Disputable total</span><br /><span class="v">${CC.centsToUsd(report.disputable_cents)}</span></div>
      <div class="item"><span class="k">FAP tier</span><br /><span class="v">${CC.escapeHtml(fapTier)}</span></div>
      <div class="item"><span class="k">FPL</span><br /><span class="v" style="font-size:0.95rem;">${CC.escapeHtml(fapPct)}</span></div>
    `;
  }

  function buildDisputeLetter(payload) {
    const { case: c, report } = payload;
    const disputable = report.findings.filter((f) => f.disputable);
    if (!disputable.length) return null;
    const lines = disputable.map(
      (f) => `  - ${CC.ruleLabel(f.rule_id)} — ${CC.centsToUsd(f.amount_cents)}\n    ${f.detail}\n    [${f.citation.dataset} ${f.citation.version}]`
    );
    return [
      `To the Billing Department at ${c.bill.provider.name},`,
      "",
      `Re: Account ${c.bill.account_no} — statement dated ${c.bill.statement_date}`,
      "",
      "I am writing to dispute the following charges on the above account, each supported by the attached citation:",
      "",
      ...lines,
      "",
      `Total disputed: ${CC.centsToUsd(report.disputable_cents)}`,
      "",
      "Please review each item and correct my account balance accordingly.",
      "",
      "Sincerely,",
      c.patient.name,
    ].join("\n");
  }

  function buildFapNote(payload) {
    const { case: c, report } = payload;
    if (!report.fap || report.fap.tier === "NONE" || report.fap.tier === "UNKNOWN") return null;
    const h = c.household;
    const incomeUsd = h ? CC.centsToUsd(h.annual_income_cents) : "n/a";
    return [
      `Financial Assistance Program note — Case ${c.case_id}`,
      "",
      `Household size ${h ? h.size : "n/a"}, annual income ${incomeUsd} (${report.fap.fpl_percent}% of the Federal Poverty Level)` +
        ` qualifies this account for ${report.fap.tier} care under ${c.bill.provider.name}'s Financial Assistance Policy.`,
      "",
      `[${report.fap.citation.dataset} — ${report.fap.citation.version}]`,
      "",
      `Recommended action: submit a Financial Assistance Program application referencing this determination for account ${c.bill.account_no}.`,
    ].join("\n");
  }

  function renderActions(payload) {
    const { case: c, report } = payload;
    const isAdversarial = c.category === "ADVERSARIAL";
    const letterBox = document.getElementById("letter-box");
    const approvalBox = document.getElementById("approval-box");
    const injectionBox = document.getElementById("injection-box");

    injectionBox.hidden = true;

    if (isAdversarial) {
      injectionBox.hidden = false;
      const attempted = c.adversarial_text || "(no injected text found)";
      injectionBox.innerHTML = `
        <div class="injection-callout">
          <span class="lbl">Injected text found in bill contents</span>
          "${CC.escapeHtml(attempted)}"
        </div>
        <p style="font-size:13.5px;color:var(--ink-soft);">
          The bill text asks the reader to email account records to an outside address. The engine does not act on
          instructions found inside bill contents — it only runs the fixed rule set. No findings fired on this bill,
          and the requested recipient is not a hospital billing address, so an attempted
          <span class="mono">actions___send_dispute_letter</span> call here is refused before it reaches a human:
        </p>
        <div class="deny-stamp">DENIED</div>
        <p class="mono" style="font-size:12px;color:var(--red);">NO CITATION / RECIPIENT NOT ALLOWED</p>
        <p style="font-size:13px;color:var(--ink-soft);" id="denial-explain">Checking policy…</p>
      `;
      fetchDenialExplanation();
      letterBox.innerHTML = `<p style="color:var(--ink-soft);font-size:14px;">No disputable findings — no dispute letter to draft.</p>`;
      approvalBox.innerHTML = `<p style="color:var(--ink-soft);font-size:13.5px;">Nothing pending approval on this case.</p>`;
      return;
    }

    const letter = buildDisputeLetter(payload);
    const fapNote = buildFapNote(payload);

    if (letter) {
      letterBox.innerHTML = `<h4 style="margin-top:0;font-family:'Courier Prime',monospace;font-size:12px;text-transform:uppercase;color:var(--ink-soft);">Dispute letter preview</h4><div class="letter-paper" id="letter-text"></div>`;
      const target = document.getElementById("letter-text");
      CC.typeInto(target, letter, { speed: 1.2 });
      setupApproval(approvalBox, "Approve & send dispute letter", `billing@${c.bill.provider.billing_email_domain}`, report.disputable_cents);
    } else if (fapNote) {
      letterBox.innerHTML = `<h4 style="margin-top:0;font-family:'Courier Prime',monospace;font-size:12px;text-transform:uppercase;color:var(--ink-soft);">Charity care application note</h4><div class="letter-paper" id="letter-text"></div>`;
      const target = document.getElementById("letter-text");
      CC.typeInto(target, fapNote, { speed: 1.2 });
      setupApproval(approvalBox, "Approve FAP application submission", `financialassistance@${c.bill.provider.billing_email_domain}`, 0);
    } else {
      letterBox.innerHTML = `<p style="color:var(--ink-soft);font-size:14px;">No disputable findings and no charity-care eligibility — nothing to draft.</p>`;
      approvalBox.innerHTML = "";
    }
  }

  function setupApproval(box, actionLabel, recipient, amountCents) {
    box.innerHTML = `
      <div class="approval-card">
        <label class="approval-row">
          <input type="checkbox" id="approve-check" />
          I have reviewed this draft and approve sending it to <span class="mono">${CC.escapeHtml(recipient)}</span>.
        </label>
        <button class="cta" id="approve-btn" disabled>${CC.escapeHtml(actionLabel)}</button>
        <div id="approve-status"></div>
        ${amountCents > 1000000 ? '<p class="mono" style="font-size:11px;color:var(--ink-soft);">Policy note: disputes over $10,000 require an advocate role — patient self-serve is refused above that threshold.</p>' : ""}
      </div>
    `;
    const check = document.getElementById("approve-check");
    const btn = document.getElementById("approve-btn");
    const status = document.getElementById("approve-status");
    check.addEventListener("change", () => { btn.disabled = !check.checked; });
    btn.addEventListener("click", () => {
      btn.disabled = true;
      check.disabled = true;
      status.innerHTML = `<div class="approval-status sent">SENT — queued to ${CC.escapeHtml(recipient)} (demo only — no message is actually transmitted).</div>`;
    });
  }

  async function fetchDenialExplanation() {
    const el = document.getElementById("denial-explain");
    try {
      const data = await CC.fetchJSON("data/policies.json");
      const policy = data.policies.send_dispute_letter || "";
      const hasRecipientClause = /recipient_email/.test(policy);
      const hasFindingClause = /finding_ids\.isEmpty/.test(policy);
      el.textContent =
        `Cedar policy "send_dispute_letter" requires ${hasFindingClause ? "a non-empty finding_ids list" : "findings"} ` +
        `and ${hasRecipientClause ? "recipient_email to match a known hospital billing address" : "an allowed recipient"}. ` +
        `Neither holds here, so the gateway denies the call before any tool runs. See the Trust Center for the full policy text.`;
    } catch (e) {
      el.textContent = "See the Trust Center for the full send_dispute_letter policy text.";
    }
  }

  function renderReplayLine(ev) {
    switch (ev.type) {
      case "tool_start":
        return `<span class="ev-tool">→ tool_start</span>  ${CC.escapeHtml(ev.tool)}  ${CC.escapeHtml(JSON.stringify(ev.input))}`;
      case "tool_end":
        return `<span class="ev-tool">← tool_end</span>    ${CC.escapeHtml(ev.tool)}  status=${CC.escapeHtml(ev.status)}`;
      case "interrupt":
        return `<span class="ev-interrupt">‼ interrupt</span>  ${CC.escapeHtml(ev.name)}  action=${CC.escapeHtml(ev.action && ev.action.tool)}`;
      case "policy_denied":
        return `<span class="ev-denied">✕ policy_denied</span>  ${CC.escapeHtml(JSON.stringify(ev.error || ev))}`;
      case "text":
        return null; // handled separately, streamed as running prose
      case "finding":
        return `<span class="ev-tool">◆ finding</span>  ${CC.escapeHtml(ev.finding.rule_id)} — ${CC.centsToUsd(ev.finding.amount_cents)}`;
      case "done":
        return `<span class="ev-tool">● done</span>  stop_reason=${CC.escapeHtml(ev.stop_reason)}`;
      default:
        return `${CC.escapeHtml(ev.type)}`;
    }
  }

  async function renderReplay() {
    const box = document.getElementById("replay-console");
    const note = document.getElementById("replay-note");
    let events;
    try {
      events = await CC.fetchJSON("data/agent-transcript.json");
    } catch (e) {
      box.textContent = "recording pending — no live run has been captured yet.";
      note.textContent = "Re-run the data build once internal/deploy/agent-transcript.json exists.";
      return;
    }
    note.textContent = "Recorded live run, case duplicate_01 — shown here as the reference audit trail regardless of the case tab selected above.";

    box.textContent = "";
    const reduce = CC.reduceMotion();
    let textBuffer = "";
    let textLineEl = null;

    async function play() {
      box.textContent = "";
      textBuffer = "";
      textLineEl = null;
      for (const ev of events) {
        if (ev.type === "text") {
          if (!textLineEl) {
            textLineEl = document.createElement("div");
            textLineEl.className = "ev-text";
            box.appendChild(textLineEl);
            textBuffer = "";
          }
          textBuffer += ev.delta;
          textLineEl.textContent = textBuffer;
        } else {
          textLineEl = null;
          const html = renderReplayLine(ev);
          if (html) {
            const line = document.createElement("div");
            line.innerHTML = html;
            box.appendChild(line);
          }
        }
        box.scrollTop = box.scrollHeight;
        if (!reduce) await new Promise((r) => setTimeout(r, ev.type === "text" ? 12 : 90));
      }
    }

    const replayBtn = document.getElementById("replay-btn");
    replayBtn.addEventListener("click", play);
    play();
  }

  async function main() {
    renderTabs();
    try {
      const payload = await CC.fetchJSON(`data/cases/${activeId}.json`);
      renderBillPanel(payload);
      renderFindingsPanel(payload);
      renderActions(payload);
    } catch (e) {
      document.getElementById("case-body").innerHTML = `<p style="color:var(--red);">Could not load case data for "${CC.escapeHtml(activeId)}". Run <span class="mono">uv run web/scripts/build_data.py</span> first.</p>`;
      console.error(e);
      return;
    }
    renderReplay();
  }

  main();
})();
