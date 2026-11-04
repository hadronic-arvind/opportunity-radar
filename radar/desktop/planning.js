"use strict";

(() => {
  const checklistLimit = 24;

  function renderApplicationPlan({el, button, call, notice, item}) {
    const body = el("div", {class:"form-row"});
    const section = el("details", {}, el("summary", {}, "Plan the application"), body);
    let loading = false;
    let loaded = false;

    async function load() {
      if (loading) return;
      loading = true;
      body.replaceChildren(el("p", {class:"caption", role:"status"}, "Loading your plan…"));
      try {
        const plan = await call("application_plan", {id:item.id});
        if (section.isConnected) render(plan);
        loaded = true;
      } catch (error) {
        body.replaceChildren(el("p", {class:"caption", role:"status"}, error.message),
          button("Retry", "secondary", load));
      } finally {
        loading = false;
      }
    }

    function render(plan) {
      let revision = plan.revision;
      const action = el("input", {type:"text", maxlength:300, value:plan.next_action,
        placeholder:"Request a reference, draft a statement, or check eligibility"});
      const due = el("input", {type:"date", value:plan.follow_up || "", max:"9999-12-31"});
      const checklist = el("div", {class:"form-row"});
      const message = el("p", {class:"caption", role:"status"});
      const reload = button("Discard draft and reload", "secondary", load, null, {hidden:""});
      const save = el("button", {type:"submit", class:"primary"}, "Save plan");
      const entries = [];
      const add = button("Add material", "secondary", () => append({label:"", done:false}, true));

      function refreshLabels() {
        entries.forEach(({label, done, remove}, index) => {
          const number = index + 1;
          label.setAttribute("aria-label", "Material " + number);
          done.setAttribute("aria-label", "Mark material " + number + " complete");
          remove.setAttribute("aria-label", "Remove material " + number);
        });
        add.disabled = entries.length >= checklistLimit;
      }

      function append(material, focus = false) {
        const done = el("input", {type:"checkbox", checked:material.done});
        const label = el("input", {type:"text", maxlength:140, required:"", value:material.label,
          placeholder:"Transcript, references, writing sample…"});
        const remove = button("Remove", "quiet", () => {
          entries.splice(entries.indexOf(entry), 1);
          row.remove();
          refreshLabels();
          add.focus();
        });
        const row = el("div", {class:"actions"},
          el("label", {class:"inline"}, done, "Complete"),
          el("div", {class:"field"}, label),
          remove);
        const entry = {label, done, remove};
        entries.push(entry);
        checklist.append(row);
        refreshLabels();
        if (focus) label.focus();
      }

      for (const material of plan.checklist) append(material);
      const form = el("form", {class:"form-row", onSubmit:async (event) => {
        event.preventDefault();
        save.disabled = true;
        reload.hidden = true;
        message.textContent = "Saving plan…";
        try {
          const result = await call("save_application_plan", {
            id:item.id, next_action:action.value, follow_up:due.value || null,
            checklist:entries.map(({label, done}) => ({label:label.value, done:done.checked})),
            expected_revision:revision
          });
          revision = result.revision;
          message.textContent = "Plan saved.";
          if (section.isConnected) notice("Application plan saved.");
        } catch (error) {
          message.textContent = error.message;
          reload.hidden = !error.message.includes("This plan changed");
        } finally {
          save.disabled = false;
        }
      }},
      el("p", {class:"caption"}, "Your next action and checklist are private. Follow-up dates appear in your agenda."),
      el("label", {class:"field"}, "Next action", action),
      el("label", {class:"field"}, "Follow-up date", due),
      el("div", {class:"form-row", role:"group", "aria-label":"Application materials"},
        el("h3", {}, "Materials checklist"), checklist, el("div", {}, add)),
      el("div", {class:"actions"}, save, reload), message);
      body.replaceChildren(form);
    }

    section.addEventListener("toggle", () => {
      if (section.open && !loaded) load();
    });
    return section;
  }

  function deadlineLabel(deadline) {
    if (deadline.state === "verify") return "Check publisher deadline";
    if (deadline.state === "inactive") return "Previously inactive";
    if (deadline.state === "directory") return "Directory reference";
    if (deadline.state === "open_until_filled") return "Publisher reported open until filled";
    if (deadline.state === "rolling") return "Publisher reported rolling review";
    if (!deadline.date) return "No recorded deadline";
    const date = deadline.timestamp ? new Date(deadline.timestamp).toLocaleString() + " local time" : deadline.date;
    return (deadline.state === "passed" ? "Recorded deadline passed " : "Recorded deadline ") + date;
  }

  function renderApplicationAgenda({el, button, call, notice, details}) {
    const controls = el("div", {class:"actions"});
    const summary = el("p", {class:"caption", role:"status"}, "Loading your agenda…");
    const results = el("div", {class:"stack"});
    const footer = el("div", {class:"actions"});
    const view = el("div", {class:"form-row"},
      el("h2", {}, "Application agenda"),
      el("p", {class:"muted"}, "See dates and next actions for saved, tracked, or planned opportunities."),
      controls,
      el("p", {class:"caption"}, "Calendar export is a snapshot of upcoming dates. Confirm publisher deadlines before acting."),
      summary, results, footer);
    const exportCalendar = button("Export calendar", "secondary", async () => {
      exportCalendar.disabled = true;
      try {
        const path = await call("pickPath", {mode:"save", title:"Export application calendar",
          extension:"ics", suggestedName:"opportunity-radar-agenda.ics"});
        if (!path) return;
        const result = await call("application_calendar", {path});
        notice("Exported " + result.event_count + (result.event_count === 1 ? " calendar event." : " calendar events."));
      } catch (error) {
        notice(error.message);
      } finally {
        exportCalendar.disabled = false;
      }
    }, null, {disabled:true});
    controls.append(exportCalendar);

    function row(item, today) {
      const followUp = item.follow_up &&
        ((item.follow_up < today ? "Follow-up overdue " : "Follow up ") + item.follow_up);
      const materials = item.materials_total &&
        (item.materials_done + " of " + item.materials_total + " materials ready");
      const candidateDates = item.deadline.state === "verify" && item.deadline.candidates.length
        ? item.deadline.candidates.join(" · ") + (item.deadline.more_candidates ? " · more dates in publisher sources" : "") : null;
      return el("article", {class:"panel"},
        el("h3", {}, button(item.title, "action", () => details(item.id), "arrow")),
        item.organization ? el("p", {class:"muted"}, item.organization) : null,
        el("p", {class:"caption"}, [deadlineLabel(item.deadline), followUp, materials,
          item.status === "applied" ? "Applied" : null].filter(Boolean).join(" · ")),
        candidateDates ? el("p", {class:"caption"}, candidateDates) : null,
        item.next_action ? el("p", {}, el("strong", {}, "Next action"), el("br"), item.next_action) : null);
    }

    async function page(offset) {
      footer.replaceChildren();
      try {
        const data = await call("application_agenda", {offset});
        if (!view.isConnected) return;
        if (!offset) results.replaceChildren();
        results.append(...data.items.map((item) => row(item, data.today)));
        const count = results.childElementCount;
        summary.textContent = data.total
          ? count + " of " + data.total + " opportunities · " +
            (data.calendar_events ? data.calendar_events + " upcoming calendar events" : "No upcoming calendar events")
          : "Save an opportunity or add a plan to start your agenda.";
        exportCalendar.disabled = !data.calendar_events;
        if (data.next_offset !== null) {
          footer.append(button("Show more", "secondary", () => page(data.next_offset)));
        }
      } catch (error) {
        summary.textContent = error.message;
        footer.append(button("Retry", "secondary", () => page(offset)));
      }
    }

    // Wait until the caller has attached the view before applying bridge results.
    queueMicrotask(() => page(0));
    return view;
  }

  window.renderApplicationPlan = renderApplicationPlan;
  window.renderApplicationAgenda = renderApplicationAgenda;
})();
