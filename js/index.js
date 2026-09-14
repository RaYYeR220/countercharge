(function () {
  const full = "Every hospital bill gets a second reader.";
  const typed = document.getElementById("typed");
  const caret = document.getElementById("caret");
  const stamp = document.getElementById("stamp");

  function dropStamp() {
    setTimeout(() => stamp.classList.add("fall"), 120);
  }

  if (CC.reduceMotion()) {
    typed.textContent = full;
    caret.classList.add("done");
    stamp.classList.add("fall");
  } else {
    CC.typeInto(typed, full, { speed: 10 }).then(() => {
      caret.classList.add("done");
      dropStamp();
    });
  }

  // Proof numbers from the real engine scorecard.
  CC.fetchJSON("data/engine-scorecard.json")
    .then((sc) => {
      const set = (id, val) => {
        const el = document.getElementById(id);
        if (el) el.textContent = val;
      };
      set("stat-exact", `${sc.rules_exact_match_cases}/${sc.total_cases}`);
      set("stat-amount", `${(sc.exact_amount_match_rate * 100).toFixed(0)}%`);
      set("stat-fap", `${(sc.fap_tier_accuracy * 100).toFixed(0)}%`);
      set("stat-fp", `${sc.negative_control_false_positive_count}`);
    })
    .catch(() => {
      /* scorecard not built yet — leave placeholders */
    });
})();
