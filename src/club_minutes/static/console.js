// Leader console: one agenda step at a time; every change autosaves the draft minutes.
(() => {
  const D = window.CONSOLE;
  const M = D.meeting;
  const S = Object.assign({ done: [], skipped: [], current: null, reports: null, others: [], step: 0 }, D.state);
  const people = Object.fromEntries(D.members.map((m) => [m.id, m]));
  const STEPS = ["Attendance", ...D.sections, "Review"];  // the club's agenda (club.yaml meeting: sections:)
  const EMPTY_NOTE = "Leave it empty if there's nothing this week; empty sections are left out of the minutes.";
  const HINTS = {
    "Old Business": "Every business item still open, carried from meeting to meeting until it's marked done. Update the text, give it an owner or a due date, or mark it done; done items show as done in tonight's minutes, then drop off.",
    "New Business": "Each item you add here is tracked: next week it's in Old Business, and it stays there until it's marked done.",
    "Upcoming Events": "Past dates drop off by themselves; a dated event goes on the calendar beside it (add an end for a range).",
    "Upcoming Chow": "This section comes from the chow schedule and is updated when you submit. To change who brings chow, use the Chow page.",
  };
  Object.assign(HINTS, D.hints);  // the club's own sections' hints
  const shint = (...t) => (t[0] ? h("p", { class: "hint shint" }, ...t) : null);  // a section's hint, in its box under the heading
  if (D.events_public) HINTS["Upcoming Events"] += " Dated events also go on the public calendar, so keep anything members-only out.";
  const RECENT_GUESTS = 8; // quick-pick buttons; older guests are in the name field's autocomplete
  // A guest on their Nth visit or later gets a reminder to invite them to apply for membership.
  const visitNo = (g) => { const p = D.guest_history[key(g.callsign)] || D.guest_history[key(g.name)]; return p && !p.member ? p.visits + 1 : 1; };
  const inviteDue = (g) => visitNo(g) >= D.invite_after;
  const INVITE = "Invite them to apply for membership";
  const ordinal = (n) => n + (["th", "st", "nd", "rd"][(n % 100 - 20) % 10] || ["th", "st", "nd", "rd"][n % 100] || "th");
  const panel = document.getElementById("panel");
  const saved = document.getElementById("saved");

  // ---------- helpers ----------
  const h = (tag, attrs = {}, ...kids) => {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
      else if (k === "class") el.className = v;
      else if (v !== false && v != null) el.setAttribute(k, v === true ? "" : v);
    }
    for (const kid of kids.flat(Infinity)) if (kid != null && kid !== false) el.append(kid);
    return el;
  };
  const key = (s) => (s || "").toLowerCase().replace(/[^a-z0-9]/g, "");
  const label = (k) => k.startsWith("g:") ? k.slice(2) : (people[k]?.short || k);
  const fullName = (k) => k.startsWith("g:") ? k.slice(2) + " (guest)" : (people[k]?.name || k);
  const grow = (ta) => { ta.style.height = "auto"; ta.style.height = ta.scrollHeight + 2 + "px"; };

  // ---------- pictures with a report: uploaded at once (/api/pictures), kept with the report when it's saved ----------
  async function addPicture(k, file) {
    const fd = new FormData(); fd.append("file", file);
    const r = await fetch("/api/pictures", { method: "POST", body: fd });
    const j = await r.json().catch(() => ({}));
    if (!r.ok || !j.id) { alert(j.error || "Couldn't add that picture."); return; }
    (S.pics[k] ||= []).push(j.id); syncReports(); save(); render();
  }
  function picRow(k) {
    const have = S.pics[k] || [];
    return h("div", { class: "pics" },
      have.map((p, i) => h("span", { class: "pic" },
        h("img", { src: `/pictures/${p}-s.jpg`, alt: `Picture ${i + 1} with the report` }),
        h("button", { type: "button", class: "picx", title: "Remove this picture", "aria-label": `Remove picture ${i + 1}`, onclick: () => {
          if (!confirm("Remove this picture?")) return;
          S.pics[k] = have.filter((x) => x !== p); syncReports(); save(); render(); } }, "×"))),
      have.length < 4 ? h("label", { class: "btn small ghost picfield" }, "+ Picture",
        h("input", { type: "file", "data-picker": "report", accept: "image/jpeg,image/png,image/webp", class: "sr", onchange: (e) => e.target.files[0] && addPicture(k, e.target.files[0]) })) : null);
  }

  // ---------- reports model ----------
  if (!S.reports) { // first load of this draft (or reopened): rebuild from saved minutes
    S.reports = {};
    for (const it of M.sections["Community Reports"] || []) {
      if (it.member) { S.reports[it.member] = it.text; if (!S.done.includes(it.member)) S.done.push(it.member); }
      else { S.others.push(it.text); (S.otherPics ||= [])[S.others.length - 1] = it.pictures || []; }
    }
  }
  S.otherPics ||= [];  // Other reports' pictures, by row (a note read out keeps its pictures)
  if (!S.pics) { // pictures with each member's report (pictures.py ids), rebuilt from the saved minutes
    S.pics = {};
    for (const it of M.sections["Community Reports"] || []) if (it.member && it.pictures?.length) S.pics[it.member] = it.pictures;
  }
  const present = () => [...M.remote, ...M.in_person, ...M.guests.map((g) => "g:" + g.name)];
  const remaining = () => present().filter((k) => !S.done.includes(k) && !S.skipped.includes(k) && k !== S.current);

  function reportText(k) {
    let t = (S.reports[k] || "").trim();
    if (!t) return "";
    const first = k.startsWith("g:") ? k.slice(2).split(" ")[0] : people[k]?.name.split(" ")[0];
    const lead = t.toLowerCase();
    if (first && !lead.startsWith(first.toLowerCase()) && !lead.startsWith(label(k).toLowerCase())) t = `${label(k)}${/^(?:[-*+]|\d+\.)\s/.test(t) ? "\n" : " "}${t}`;  // a list starts under the name
    return t;
  }
  function syncReports() {
    M.sections["Community Reports"] = [
      ...S.done.map((k) => ({ text: reportText(k), member: k.startsWith("g:") ? null : k, pictures: S.pics[k] || [] })).filter((i) => i.text),
      ...S.others.map((t, i) => ({ text: t, member: null, pictures: S.otherPics[i] || [] })).filter((i) => i.text.trim()),
    ];
  }

  // ---------- one console at a time (console.hold): this tab keeps the minutes, or watches read-only ----------
  // A new id each time the page loads, so a duplicated tab (which copies sessionStorage) never shares one; the id
  // before a reload goes with the first check-in, so a reloaded console keeps its hold (console.hold's prev).
  let PREV = sessionStorage.getItem("console-tab");
  const TAB = crypto.randomUUID ? crypto.randomUUID() : String(Math.random()).slice(2) + String(Math.random()).slice(2);
  sessionStorage.setItem("console-tab", TAB);
  let readOnly = false, held = false;  // held: this tab has had the minutes (so a take-over can cost it unsaved typing)
  const holdbar = document.getElementById("holdbar");
  const post = (what, extra = {}) => fetch(`/api/meetings/${M.slug}/${what}`, { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ tab: TAB, ...extra }) });
  const clock = (t) => new Date(t * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  // a dialog that has to be answered, for news that stops this console (the bar keeps saying it after)
  const exit = () => h("a", { class: "btn small ghost", href: "/" }, "Exit");
  function notice(title, text, button) {
    const d = h("dialog", { class: "modal", "aria-labelledby": "notice-title" }, h("div", { class: "body" },
      h("h2", { id: "notice-title" }, title), h("p", {}, text),
      h("p", { class: "row" }, button, h("button", { class: "btn small ghost", onclick: () => d.close() }, "Keep reading here"), exit())));
    document.body.append(d); d.showModal();
  }
  function lock(who, lost) {  // someone else is taking the minutes: read-only, with Take over
    readOnly = true;
    // the steps and Back/Next still move around (to look); nothing in a step can be changed
    for (const id of ["panel", "leader"]) { const el = document.getElementById(id); if (el) el.inert = true; }
    document.getElementById("endforms").hidden = true;  // Submit and Discard are for whoever holds the minutes
    saved.textContent = "Read-only"; saved.className = "saved dirty";
    const take = who.you ? "Take over here" : "Take over";
    holdbar.replaceChildren(who.you ? `You're taking these minutes in another window or device (since ${clock(who.since)}).`
        : `${who.holder} is taking these minutes (since ${clock(who.since)}). This page is read-only, so nothing gets overwritten; it shows the minutes as they were when you opened it.`,
      lost ? " Changes made here after the last save weren't saved." : "", " ",
      h("a", { href: `/meetings/${M.slug}`, target: "_blank", rel: "noopener" }, "Follow the live view"), " ",
      h("button", { class: "btn small", onclick: async () => {
        if (!who.you && !confirm(`Take over from ${who.holder}? Their console becomes read-only, and you're recorded as taking the minutes from here.`)) return;
        const r = await post("hold", { take: true });
        if (r.ok) location.reload(); else checkIn(); } }, take), " ", exit());
    holdbar.hidden = false;
    if (lost) notice(`${who.holder} took over`, `${who.holder} is taking these minutes now, so this page is read-only. ` +
      "Anything typed here in the last few seconds wasn't saved: copy it if you need it.", h("a", { class: "btn small", href: `/meetings/${M.slug}`, target: "_blank", rel: "noopener" }, "Follow the live view ↗"));
  }
  function closed(how, by) {  // discarded or submitted in another console: nothing more to do here
    readOnly = true; closedHow = how;
    // still readable: the steps and Back/Next move around (as they were when this page was opened); nothing changes
    for (const id of ["panel", "leader"]) { const el = document.getElementById(id); if (el) el.inert = true; }
    document.getElementById("endforms").hidden = true;  // Submit and Discard are for whoever holds the minutes
    saved.textContent = how === "submitted" ? "Submitted" : "Discarded"; saved.className = "saved dirty";
    const who = by || "Someone";
    holdbar.replaceChildren(`${who} ${how} these minutes from another console. `,
      how === "submitted" ? h("a", { class: "btn small", href: `/meetings/${M.slug}` }, "Read the minutes") : h("a", { class: "btn small", href: "/console" }, "Start a meeting"),
      " ", exit());
    holdbar.hidden = false;
    notice(how === "submitted" ? `Minutes submitted${by ? " by " + by : ""}` : `Minutes discarded${by ? " by " + by : ""}`,
      how === "submitted" ? `${who} submitted these minutes from another console, so they're finished. This page is read-only now; the finished minutes have everything.`
        : `${who} discarded these minutes from another console, so they're gone. This page still shows them as they were when you opened it (read-only): copy anything you need before leaving.`,
      how === "submitted" ? h("a", { class: "btn small", href: `/meetings/${M.slug}` }, "Read the minutes") : h("a", { class: "btn small", href: "/console" }, "Start a meeting"));
  }
  let closedHow = null;
  async function checkIn() {
    if (closedHow) return;
    const r = await post("hold", readOnly ? { peek: true } : PREV ? { prev: PREV } : {}).catch(() => null);
    if (!r) return;
    PREV = null;  // only the first check-in carries on from before a reload
    if (r.status === 409) { const j = await r.json(); if (j.closed) closed(j.closed, j.by); else if (j.holder) lock(j, held && !readOnly); return; }
    if (!readOnly && r.ok) held = true;
    if (readOnly && r.ok) {  // they've closed it: carry on (a reload brings their last changes)
      holdbar.replaceChildren("They've closed the console. ", h("button", { class: "btn small", onclick: () => location.reload() }, "Continue the minutes"));
    }
  }
  checkIn();
  setInterval(checkIn, 5000);
  // leaving lets go at once; coming back (Back/Forward restores the page without rerunning it, or the tab comes to
  // the front) checks in at once, rather than at the next tick, so two consoles are never both editable for long
  window.addEventListener("pagehide", () => { if (!readOnly) navigator.sendBeacon(`/api/meetings/${M.slug}/release`, new Blob([JSON.stringify({ tab: TAB })], { type: "application/json" })); });
  window.addEventListener("pageshow", (e) => { if (e.persisted) checkIn(); });
  document.addEventListener("visibilitychange", () => { if (!document.hidden) checkIn(); });

  // ---------- saving ----------
  let timer = null;
  const waiters = []; // every caller of save() is resolved when the next actual save finishes
  function save(now = false) {
    if (readOnly) return Promise.resolve();
    saved.textContent = "Saving…"; saved.className = "saved dirty";
    clearTimeout(timer);
    const done = new Promise((resolve) => waiters.push(resolve));
    timer = setTimeout(async () => {
      syncReports();
      try {
        const r = await fetch(`/api/meetings/${M.slug}`, {
          method: "PUT", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ meeting: M, state: S, tab: TAB }),
        });
        if (r.status === 409 || r.status === 404) { await checkIn(); if (readOnly) return; }  // taken over, or discarded elsewhere
        if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
        saved.replaceChildren("Saved ", emo("✅", "check")); saved.className = "saved";
      } catch (e) { saved.textContent = "Not saved: " + e.message; saved.className = "saved err"; }
      waiters.splice(0).forEach((r) => r());
    }, now ? 0 : 700);
    return done;
  }
  document.getElementById("leader").addEventListener("change", (e) => { M.leader = e.target.value; save(); });
  window.addEventListener("beforeunload", (e) => { if (saved.classList.contains("dirty")) e.preventDefault(); });

  // ---------- steps ----------
  const STEP_LABEL = { "Upcoming Chow": "Chow" };  // the minutes' section keeps its full name
  function go(i) { S.step = Math.max(0, Math.min(STEPS.length - 1, i)); render(); save(); window.scrollTo(0, 0); }
  document.getElementById("prev").onclick = () => go(S.step - 1);
  document.getElementById("next").onclick = () => go(S.step + 1);

  function render() {
    const nav = document.getElementById("steps");
    nav.replaceChildren(...STEPS.map((s, i) => h("button", { class: i === S.step ? "on" : "", onclick: () => go(i) }, `${i + 1}. ${STEP_LABEL[s] || s}`)));
    document.getElementById("next").hidden = S.step === STEPS.length - 1;  // Review, the last step: nothing after it
    const step = STEPS[S.step];
    panel.replaceChildren(
      step === "Attendance" ? attendance() :
      step === "Community Reports" ? reports() :
      step === "Review" ? review() : listSection(step));
    panel.querySelectorAll("textarea").forEach(grow);
  }

  // ---------- quorum (the club's rule, if any; guests never count) ----------
  function quorum() {
    const q = D.quorum;
    if (!q) return null;
    const here = [...M.in_person, ...M.remote], count = here.filter((x) => q.active.includes(x)).length;
    const off = here.filter((x) => q.officer_ids.includes(x)).length;
    const short = [];
    if (q.needed != null && count < q.needed) short.push(`${q.needed - count} short`);
    if (q.officers != null && off < q.officers) short.push(`${q.officers - off} more officer${q.officers - off === 1 ? "" : "s"} needed`);
    const have = [q.needed != null ? `${count} present, ${q.needed} needed` : null,
      q.officers != null ? `${off} officer${off === 1 ? "" : "s"} present, ${q.officers} needed` : null].filter(Boolean).join("; ");
    return { ok: !short.length, text: short.length ? short.join(", ") : have + " ✓", majority: Math.floor(count / 2) + 1 };
  }

  // ---------- attendance ----------
  function attendance() {
    const where = (id) => M.in_person.includes(id) ? "inperson" : M.remote.includes(id) ? "online" : "";
    const cycle = (id) => {
      const w = where(id);
      M.in_person = M.in_person.filter((x) => x !== id); M.remote = M.remote.filter((x) => x !== id);
      if (w === "") M.in_person.push(id); else if (w === "inperson") M.remote.push(id);
      render(); save();
    };
    // Guests: tap a recent guest, or type a name (autocomplete covers everyone who has visited).
    const same = (a, b) => (a.callsign && b.callsign && key(a.callsign) === key(b.callsign)) || key(a.name) === key(b.name);
    const here = (g) => M.guests.some((x) => same(x, g));
    const visitNote = (g) => `${ordinal(g.visits + 1)} visit`;
    // Recent guests cycle like members: absent → in person → online → absent.
    const toggleGuest = (g) => {
      const x = M.guests.find((y) => same(y, g));
      if (!x) M.guests.push({ name: g.name, callsign: g.callsign || "", notes: visitNote(g) });
      else if (!x.online) x.online = true;
      else M.guests = M.guests.filter((y) => y !== x);
      render(); save();
    };
    const gwhere = (g) => { const x = M.guests.find((y) => same(y, g)); return !x ? "" : x.online ? "online" : "inperson"; };
    const recent = D.past_guests.slice(0, RECENT_GUESTS);
    const datalist = h("datalist", { id: "pastguests" },
      D.past_guests.map((g) => h("option", { value: g.name }, [g.callsign, `${g.visits} visit${g.visits > 1 ? "s" : ""}`].filter(Boolean).join(" · "))));
    const guestInfo = h("div", { class: "ghint" });
    const gname = h("input", { type: "text", placeholder: "Guest name", list: "pastguests", autocomplete: "off", oninput: () => {
      const g = D.guest_history[key(gname.value)];
      guestInfo.textContent = !g ? "" : g.member ? `${g.name} is a member now. Mark them above instead.`
        : `${g.name}${g.callsign ? " " + g.callsign : ""}: ${ordinal(g.visits + 1)} visit (last ${g.last})` + (g.visits + 1 >= D.invite_after ? `. ${INVITE}.` : "");
      if (g && g.callsign && !gcall.value) gcall.value = g.callsign;
    } });
    const gcall = h("input", { type: "text", placeholder: "Callsign", size: 9 });
    const gnote = h("input", { type: "text", placeholder: "Note (optional)" });
    const add = () => {
      if (!gname.value.trim()) return;
      const prev = D.guest_history[key(gname.value)];
      const g = { name: gname.value.trim(), callsign: gcall.value.trim().toUpperCase(), notes: gnote.value.trim() };
      if (prev && !prev.member && !g.notes) g.notes = visitNote(prev);
      if (!here(g)) M.guests.push(g);
      render(); save();
    };
    const n = (w) => w === "inperson" ? M.in_person.length : M.remote.length;
    return h("div", {},
      D.celebrations.length ? h("p", { class: "msg ok" }, emo("🎉", "star"), " ", D.celebrations.join(" · ")) : null,
      (() => { const q = quorum(); return q ? h("p", { class: "msg " + (q.ok ? "ok" : "warn"), role: "status" }, "Quorum: " + q.text) : null; })(),
      h("h2", {}, `Attendance: ${M.in_person.length} in person, ${M.remote.length} online, ${M.guests.length + M.visitors} guest${M.guests.length + M.visitors === 1 ? "" : "s"}`),
      h("p", { class: "legend" }, "Tap a name to cycle: ", h("span", { class: "mbtn" }, "absent"), "→",
        h("span", { class: "mbtn inperson" }, `in person (${n("inperson")})`), "→", h("span", { class: "mbtn online" }, `online (${n("online")})`)),
      h("div", { class: "grid" }, D.members.map((m) => h("button", { class: "mbtn " + where(m.id), onclick: () => cycle(m.id) },
        m.name, h("small", {}, [m.callsign, where(m.id) === "inperson" ? "in person" : where(m.id) === "online" ? "online" : ""].filter(Boolean).join(" · "))))),
      h("h2", { style: "margin-top:1.4rem" }, "Guests"),
      recent.length ? [h("p", { class: "hint" }, "Recent guests: tap to cycle absent → in person → online. Type below for anyone else."),
        h("div", { class: "grid" }, recent.map((g) => h("button", { class: "mbtn " + gwhere(g), onclick: () => toggleGuest(g) },
          g.name, h("small", {}, [g.callsign, `${g.visits} visit${g.visits > 1 ? "s" : ""}`, { inperson: "in person", online: "online" }[gwhere(g)]].filter(Boolean).join(" · ")),
          g.visits + 1 >= D.invite_after ? h("span", { class: "invite" }, "invite to join") : null)))] : null,
      datalist,
      M.guests.map((g, i) => h("div", { class: "row" }, h("span", { style: "flex:1" }, `${g.name} ${D.ham ? g.callsign || "" : ""} ${g.notes ? "(" + g.notes + ")" : ""}`,
          inviteDue(g) ? h("span", { class: "invite" }, "invite to join") : null),
        h("button", { class: "btn small ghost", title: "Switch between in person and online",
          onclick: () => { if (g.online) delete g.online; else g.online = true; render(); save(); } }, g.online ? "Online" : "In person"),
        h("button", { class: "btn small ghost", onclick: () => { M.guests.splice(i, 1); render(); save(); } }, "Remove"))),
      h("div", { class: "row" }, gname, D.ham ? gcall : null, gnote, h("button", { class: "btn small", onclick: add }, "Add guest")),
      guestInfo,
      h("p", { class: "counter" }, "Visitors whose names aren't recorded (e.g. family): ",
        h("button", { class: "btn small ghost", onclick: () => { M.visitors = Math.max(0, M.visitors - 1); render(); save(); } }, "−"),
        h("b", {}, String(M.visitors)),
        h("button", { class: "btn small ghost", onclick: () => { M.visitors++; render(); save(); } }, "+")));
  }

  // ---------- community reports ----------
  let otherOpen = false;  // Other reports' card: collapsed until opened or it has something in it
  function call(k) { if (S.current && !S.done.includes(S.current)) S.skipped.push(S.current); S.skipped = S.skipped.filter((x) => x !== k); S.current = k; render(); save(); }
  function finish(nothing) {
    const k = S.current; if (!k) return;
    if (nothing && !(S.reports[k] || "").trim()) S.reports[k] = "nothing new";
    if (!S.done.includes(k)) S.done.push(k);
    S.current = null; render(); save();
  }
  async function noteStatus(n, status) {
    const r = await fetch(`/business/notes/${n.id}/${status}`, { method: "POST", headers: { Accept: "application/json" },
      body: new URLSearchParams({ meeting: M.slug }) });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) { alert(j.error || j.detail || "Couldn't save"); return; }
    D.business = j; render(); save();
  }
  // What to ask a member about: what they said in their last few reports.
  function followUp(k, past) {
    if (k.startsWith("g:")) return h("p", { class: "hint" }, D.ham ? "Guest: ask what brought them in, and their license and callsign." : "Guest: ask what brought them in.");
    if (!past.length) return h("p", { class: "hint" }, "No earlier reports on record.");
    const said = past.map(([d, t]) => h("div", { class: "past" }, h("b", {}, d + ": "), t));
    return h("div", {}, h("div", { class: "hint" }, "Follow up on what they said before:"), said);
  }
  function reports() {
    const rem = remaining();
    const groups = [["Online", rem.filter((k) => M.remote.includes(k))], ["In person", rem.filter((k) => M.in_person.includes(k))],
      ["Guests", rem.filter((k) => k.startsWith("g:"))]];
    const suggest = () => { const g = groups.find(([, ks]) => ks.length); if (g) call(g[1][Math.floor(Math.random() * g[1].length)]); };
    const li = (k, btn) => h("li", { class: k === S.current ? "cur" : "" }, h("span", {}, fullName(k)), btn);
    const queue = h("div", { class: "queue" },
      h("button", { class: "btn", onclick: suggest, disabled: !rem.length }, "Suggest next"),
      h("p", { class: "hint" }, "Online members first, then in person. Or click any name."),
      groups.map(([title, ks]) => ks.length ? [h("h3", {}, `${title} (${ks.length})`),
        h("ul", {}, ks.map((k) => li(k, h("button", { class: "btn small ghost", onclick: () => call(k) }, "Call on"))))] : null),
      S.skipped.length ? [h("h3", {}, "Come back to"), h("ul", {}, S.skipped.map((k) => li(k, h("button", { class: "btn small ghost", onclick: () => call(k) }, "Call on"))))] : null,
      S.done.length ? [h("h3", {}, `Done (${S.done.length})`), h("ul", {}, S.done.map((k) => li(k, h("button", { class: "btn small ghost", onclick: () => { S.done = S.done.filter((x) => x !== k); S.current = k; render(); save(); } }, "Edit"))))] : null,
      present().length ? null : h("p", { class: "hint" }, "Mark attendance first."));
    let floor;
    if (S.current) {
      const k = S.current;
      const past = k.startsWith("g:") ? [] : (D.history[k] || []);
      const ta = h("textarea", { rows: 4, "data-format": true, "data-pic": "report", placeholder: "What are they working on? (their name is added automatically)", oninput: (e) => { S.reports[k] = e.target.value; none.hidden = !!e.target.value.trim(); grow(e.target); save(); } });
      ta.value = S.reports[k] || "";
      // "Nothing new" means nothing to report: only while the box is empty
      const none = h("button", { class: "btn ghost", hidden: !!ta.value.trim(), onclick: () => finish(true) }, "Nothing new");
      floor = h("div", { class: "floor" },
        h("h3", {}, `${fullName(k)} has the floor`),
        k.startsWith("g:") && inviteDue(M.guests.find((g) => "g:" + g.name === k) || { name: k.slice(2) })
          ? h("div", { class: "invitebox" }, `This is ${k.slice(2).split(" ")[0]}'s ${ordinal(visitNo(M.guests.find((g) => "g:" + g.name === k)))} visit. ${INVITE}.`) : null,
        (D.business.notes || []).filter((n) => n.by === k).map((n) => h("div", { class: "bizcomment" }, h("b", {}, "They sent a note: "), n.text)),
        followUp(k, past),
        ta,
        picRow(k),
        h("div", { class: "row" },
          h("button", { class: "btn", onclick: () => finish(false) }, "Done"),
          none,
          h("button", { class: "btn ghost", onclick: () => { S.skipped.push(k); S.current = null; render(); save(); } }, "Come back later")));
      setTimeout(() => ta.focus(), 0);
    } else {
      floor = h("div", { class: "floor" }, h("h3", {}, rem.length ? "Who's next?" : present().length ? "Everyone has reported" : "Who's next?"),
        h("p", { class: "hint" }, rem.length || !present().length ? "Press “Suggest next” or call on someone." : "Use “Edit” to change a report, or move on to Old Business."));
    }
    // Notes members sent in for tonight (Business page). Someone who's here anyway gives their own report.
    const here = (id) => M.in_person.includes(id) || M.remote.includes(id);
    const noteCard = (n) => h("div", { class: "notecard" },
      h("div", {}, h("b", {}, n.name), here(n.by) ? h("span", { class: "badge" }, "here tonight") : null),
      h("div", { class: "notetext" }, n.text),
      (n.pictures || []).length ? h("div", { class: "pics" }, n.pictures.map((p, i) => h("img", { src: `/pictures/${p}-s.jpg`, alt: `Picture ${i + 1} with ${n.name}'s note` }))) : null,
      h("div", { class: "notebtns" },
        here(n.by)
          ? h("button", { class: "btn small", onclick: async () => {
              S.reports[n.by] = [S.reports[n.by], n.text].filter(Boolean).join(" ");
              S.pics[n.by] = [...(S.pics[n.by] || []), ...(n.pictures || [])].slice(0, 4);
              await noteStatus(n, "read"); } }, "Use it in their report")
          : h("button", { class: "btn small", onclick: async () => {
              S.others.push(`${n.name} sent a note: ${n.text}`); S.otherPics[S.others.length - 1] = n.pictures || [];
              await noteStatus(n, "read"); } }, "Read it out, add it to the minutes"),
        h("button", { class: "btn small ghost", onclick: () => noteStatus(n, "dismissed") }, "Skip")));
    const notes = (D.business.notes || []).length ? h("div", { class: "notesbox" },
      h("h3", {}, `Notes sent in (${D.business.notes.length})`),
      h("p", { class: "notelead" }, "Members sent these for tonight. Read each one out, then add it to the minutes. " +
        "If they came after all, let them give their report and skip the note (or use it in their report)."),
      D.business.notes.map(noteCard)) : null;
    const others = h("details", { class: "card bizcard othercard", open: otherOpen || S.others.some((t) => t.trim()), ontoggle: (e) => { otherOpen = e.target.open; } },
      h("summary", {}, h("h3", {}, "Other reports")),
      h("p", { class: "hint" }, "For news passed along by someone who isn't present, e.g. “Jane Doe sent a message recommending…”."),
      itemRows(S.others, (arr) => { S.others = arr; syncReports(); }, S.otherPics, true));
    return h("div", {}, h("h2", {}, "Community Reports"), notes, h("div", { class: "cols2" }, queue, h("div", {}, floor, others)));
  }

  // ---------- plain list sections ----------
  function itemRows(arr, setArr, pics, counted) {  // counted: show "n / 5,000 characters" (as format.js does for data-count boxes); pics: a list kept in step with arr (Other reports' pictures), shown as a count
    const wrap = h("div", {});
    const draw = () => wrap.replaceChildren(
      ...(arr.length ? arr : arr.concat("")).map((t, i) => {  // an empty section: one box ready to type in (a blank is never saved)
        const cnt = counted ? h("small", { class: "fmtcount" }) : null;
        const show = () => { if (cnt) cnt.textContent = `${ta.value.length.toLocaleString()} / ${ta.maxLength.toLocaleString()} characters`; };
        const ta = h("textarea", { rows: 1, "data-format": true, ...(counted && { maxlength: 5000 }), oninput: (e) => { arr[i] = e.target.value; setArr(arr); grow(e.target); show(); save(); } });
        ta.value = t; show();
        const n = pics?.[i]?.length;
        return h("div", { class: "row" }, cnt ? h("div", { style: "flex:1 1 12rem" }, ta, cnt) : ta, n ? h("span", { class: "hint" }, `${n} picture${n === 1 ? "" : "s"}`) : null,
          h("button", { class: "btn small ghost", title: "Remove", onclick: () => { arr.splice(i, 1); pics?.splice(i, 1); setArr(arr); draw(); save(); } }, emo("✖️", "x")));
      }),
      h("button", { class: "btn small ghost", onclick: () => { arr.push(""); setArr(arr); draw(); wrap.querySelectorAll("textarea")[arr.length - 1].focus(); } }, "+ Add item"));
    draw();
    setTimeout(() => wrap.querySelectorAll("textarea").forEach(grow), 0);
    return wrap;
  }
  function listSection(sec) {
    if (sec === "Upcoming Chow") return chowSection();
    if (sec === "Old Business" || sec === "New Business") return businessSection(sec);
    if (sec === "Upcoming Events") return eventsSection();
    if (sec === "Membership Applications") return applicationsSection();
    const arr = (M.sections[sec] || []).map((i) => i.text);
    const set = (a) => { M.sections[sec] = a.map((t) => ({ text: t, member: null })); };
    const rows = itemRows(arr, set);
    const hint = [HINTS[sec], EMPTY_NOTE].filter(Boolean).join(" ");
    // Bills: what the Treasurer recorded as paid since the last meeting, to read out; added to the minutes only on a tap
    const paid = sec === "Bills and Communications" ? (D.paid_since || []).filter((t) => !arr.includes(t)) : [];
    const treasurer = paid.length ? h("div", { class: "card" }, h("p", { class: "hint" }, "The Treasurer recorded these as paid since the last meeting:"),
      h("ul", {}, paid.map((t) => h("li", {}, t))),
      h("button", { class: "btn small ghost", onclick: () => { set(arr.concat(paid)); render(); save(); } }, "Add to tonight's bills")) : null;
    return h("div", {}, h("h2", {}, sec), shint(hint), treasurer, rows);
  }

  // ---------- membership applications: the candidates, and the vote from the paper ballot (applications.py) ----------
  function applicationsSection() {
    const sec = "Membership Applications", box = h("div", {});
    const send = async (id, data) => {
      const r = await fetch(`/api/meetings/${M.slug}/vote`, { method: "POST", headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({ id, ...data }) });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) { alert(j.error || j.detail || "Couldn't save"); return; }
      D.applicants = j; draw(); save();
    };
    const draw = () => {
      const A = D.applicants, here = M.in_person.length + M.remote.length;
      M.sections[sec] = A.lines.map((t) => ({ text: t, member: null }));
      box.replaceChildren(...A.people.map((p, i) => {
        const b = p.ballot;
        const yes = h("input", { type: "number", min: 0, max: 9999, value: b && !b.accepted ? b.yes : "", size: 4, id: `yes${i}` });
        const no = h("input", { type: "number", min: 0, max: 9999, value: b && !b.accepted ? b.no : "", size: 4, id: `no${i}` });
        const count = () => ({ yes: yes.value === "" ? null : +yes.value, no: no.value === "" ? null : +no.value });
        const status = b ? h("p", { class: "msg ok", role: "status" }, b.accepted ? "Accepted." : `${b.yes}\u2013${b.no}: ${b.passed ? "passed" : "not passed"}. The minutes say so; submitting them ${b.passed ? "records it" : "turns the application down"}.`) : null;
        return h("div", { class: "card" }, h("h3", {}, p.name), h("p", { class: "hint" }, `Sponsored by ${p.sponsor}.`),
          !p.can_vote ? h("p", { class: "hint" }, `Not up for a vote yet: it can be recorded from ${p.due}.`)
            : A.vote ? h("div", { class: "row" },
              h("label", { for: `yes${i}` }, "Yes"), yes, h("label", { for: `no${i}` }, "No"), no,
              h("span", { class: "hint" }, `${here} members present. Passes under: ${A.rule}.`),
              h("button", { class: "btn small", onclick: () => send(p.id, count()) }, b ? "Replace the count" : "Record the vote"))
              : h("div", { class: "row" }, h("button", { class: "btn small", onclick: () => send(p.id, {}) }, b ? "Accepted" : "Accept them")),
          status);
      }));
    };
    draw();
    return h("div", {}, h("h2", {}, sec), shint("Each candidate is listed in the minutes until the vote. Only the counts go in the minutes, never who voted how."), box);
  }

  // ---------- upcoming events: text plus a date and optional time ("when"), for the club calendar ----------
  function eventsSection() {
    const sec = "Upcoming Events";
    const evs = M.sections[sec] = (M.sections[sec] || []).map((e) => ({ text: e.text, member: null, when: e.when || null, until: e.until || null, cal: e.cal || null }));
    const wrap = h("div", {});
    const cals = h("div", {});  // the calendar list below, refreshed with the events (one removed shows there again)
    const draw = () => {
      if (!evs.length) evs.push({ text: "", member: null, when: null });  // one box ready to type in (a blank is never saved)
      wrap.replaceChildren(...evs.map((e, i) => {
        const [d, t] = (e.when || "").split("T"), [ed, et] = (e.until || "").split("T");
        const date = h("input", { type: "date", value: d || "", "aria-label": "Starts" });
        const time = h("input", { type: "time", value: t || "", "aria-label": "Start time (optional)" });
        const endDate = h("input", { type: "date", value: ed || "", "aria-label": "Last day (optional)" });
        const endTime = h("input", { type: "time", value: et || "", "aria-label": "End time (optional)" });
        // An end time alone means the same day; the server drops an end before the start.
        const setWhen = () => {
          e.when = date.value ? date.value + (time.value ? "T" + time.value : "") : null;
          const day = endDate.value || (endTime.value ? date.value : "");
          e.until = e.when && day ? day + (endTime.value ? "T" + endTime.value : "") : null;
          nodate.hidden = !!e.when;
          save();
        };
        [date, time, endDate, endTime].forEach((x) => x.onchange = setWhen);
        const twin = e.when && (D.calendar || []).find((c) => c.start === e.when.slice(0, 10) && !same(e, c) && similar(c.title, e.text));
        const nodate = h("span", { class: "hint" }, "no date: not on the calendar");
        nodate.hidden = !!e.when;
        const cal = h("select", { "aria-label": "Calendar", title: "Which calendar it goes on", onchange: (ev) => { e.cal = ev.target.value === "events" ? null : ev.target.value; save(); } },
          [["events", "Club events"], ["community", "Community"], ["planning", "Planning"]].map(([v, t]) => h("option", { value: v, selected: v === (e.cal || "events") }, t)));
        const ta = h("textarea", { rows: 1, "data-format": true, placeholder: "What, and where", "aria-label": "Event",
          oninput: (ev) => { e.text = ev.target.value; grow(ev.target); save(); } });
        ta.value = e.text;
        return h("div", { class: "bizitem event" }, h("div", { class: "row" }, ta,
            h("button", { class: "btn small ghost", title: "Remove", onclick: () => { evs.splice(i, 1); draw(); save(); } }, emo("✖️", "x"))),
          h("div", { class: "when" }, h("span", { class: "evlabel" }, "Starts"), date, h("span", { class: "hint" }, "at"), time,
            h("span", { class: "evlabel" }, "Ends"), endDate, h("span", { class: "hint" }, "at"), endTime),
          h("p", { class: "hint" }, "The time and the end are optional."),
          h("div", { class: "row meta" }, cal, nodate),
          twin ? h("p", { class: "duphint" }, `Possible duplicate: "${twin.title}" is on the calendar that day. If it's the same event, `,
            h("button", { class: "btn small ghost", title: "Replace this entry with the calendar's: its title, place, date, time and calendar", onclick: () => {
              Object.assign(e, { text: twin.title + (twin.where ? `, ${twin.where}` : ""), when: twin.when, until: twin.until, cal: twin.cal === "events" ? null : twin.cal });
              draw(); save(); } }, "use the calendar's"), " (it stays in the minutes, and on the calendar once) or remove this one.") : null);
      }), h("button", { class: "btn small ghost", onclick: () => { evs.push({ text: "", member: null, when: null }); draw();
        wrap.querySelectorAll("textarea")[evs.length - 1].focus(); } }, "+ Add event"));
      setTimeout(() => wrap.querySelectorAll("textarea").forEach(grow), 0);
      cals.replaceChildren(calendarCard(evs, draw));
    };
    draw();
    return h("div", {}, h("h2", {}, sec), shint(HINTS[sec]), cals, wrap);  // what's on the calendar first, to read out (Jim, 2026-11-20)
  }

  // Two titles that look like the same event: half the shorter one's words in the other (calview.similar)
  const SMALL = new Set(["the", "and", "for", "with", "at", "on", "in", "of", "an", "to"]);
  const words = (t) => new Set((t.toLowerCase().match(/[a-z0-9]+/g) || []).filter((w) => w.length > 1 && !SMALL.has(w)));
  const similar = (a, b) => { const x = words(a), y = words(b); return x.size > 0 && y.size > 0 && [...x].filter((w) => y.has(w)).length * 2 >= Math.min(x.size, y.size); };
  const same = (e, c) => e.text.trim().toLowerCase().startsWith(c.title.toLowerCase());  // added from the list: one event

  // What's on the calendar (console.calendar_ahead), to read out; added to the minutes only on a tap.
  let ahead = "next", once = true;  // once: a repeating event (a weekly net) shows only its next date
  function calendarCard(evs, redraw) {
    const card = h("div", { class: "card" });
    const day = (d) => new Date(d + "T12:00").toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
    const plus = (n) => { const d = new Date(M.date + "T12:00"); d.setDate(d.getDate() + n); return d.toISOString().slice(0, 10); };
    const ends = { next: D.next_meeting, "14": plus(14), "31": plus(31), "92": plus(92) };
    const pick = h("select", { "aria-label": "How far ahead", onchange: (e) => { ahead = e.target.value; fill(); } },
      [["next", "until the next meeting"], ["14", "the next 2 weeks"], ["31", "the next month"], ["92", "the next 3 months"]]
        .map(([v, t]) => h("option", { value: v, selected: v === ahead }, t)));
    const onceBox = h("label", { class: "check small" }, h("input", { type: "checkbox", checked: once, onchange: (e) => { once = e.target.checked; fill(); } }),
      " Show a repeating event once (its next date)");
    const fill = () => {
      // already in the minutes: same day, and the text starts with its title ("Fox hunt, the park")
      const have = (c) => evs.some((e) => (e.when || "").slice(0, 10) === c.start && same(e, c));
      const alike = (c) => evs.find((e) => (e.when || "").slice(0, 10) === c.start && e.text.trim() && similar(c.title, e.text));
      const seen = new Set();  // the list is in date order: a series' first entry is its next date
      const list = (D.calendar || []).filter((c) => c.start <= ends[ahead] && !have(c)
        && !(once && c.series && (seen.has(c.series) || !seen.add(c.series))));
      card.replaceChildren(h("p", { class: "row calhead" }, h("strong", {}, "From the calendar"), pick, onceBox),
        list.length ? h("ul", {}, list.map((c) => [c, alike(c)]).map(([c, dup]) => h("li", { class: dup ? "dup" : null },
          h("strong", {}, day(c.start), c.time ? " " + c.time : "", c.end !== c.start ? " – " + day(c.end) : ""), " ",
          c.title, c.where ? ` (${c.where})` : "", " ", h("span", { class: "muted small" }, c.kind), " ",
          dup ? h("span", { class: "duphint" }, `possibly the same as "${dup.text}" above `) : null,
          h("button", { class: "btn small ghost", title: "Add to tonight's Upcoming Events", onclick: () => {
            evs.push({ text: c.title + (c.where ? `, ${c.where}` : ""), member: null, when: c.when, until: c.until, cal: c.cal === "events" ? null : c.cal }); redraw(); save(); } }, "+ Add"))))
          : h("p", { class: "hint" }, "Nothing on the calendar for then."),
        h("p", { class: "hint" }, "Club events, community events, Planning, holidays and shared birthdays, to read out. + Add puts one in the minutes (it stays on the calendar once, not twice). An event can be left out from its page on the calendar."));
    };
    fill();
    return card;
  }

  // ---------- business: tracked items (business.py); every change is saved on the server at once ----------
  function businessSection(sec) {
    const isNew = sec === "New Business";
    const box = h("div", {});
    const send = async (url, data) => {
      const r = await fetch(url, { method: "POST", headers: { Accept: "application/json" },
        body: new URLSearchParams([["meeting", M.slug], ...Object.entries(data).flatMap(([k, v]) => (Array.isArray(v) ? v.map((x) => [k, x]) : [[k, v]]))]) });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) { alert(j.error || j.detail || "Couldn't save"); return false; }
      D.business = j; saved.replaceChildren("Saved ", emo("✅", "check")); saved.className = "saved"; draw();
      return true;
    };
    const fmtDate = (d) => new Date(d + "T12:00").toLocaleDateString(undefined, { month: "short", day: "numeric" });
    const row = (x) => {
      const done = x.status === "done";
      const ta = h("textarea", { rows: 1, "data-format": true, oninput: (e) => grow(e.target),
        onchange: (e) => send(`/business/${x.id}/edit`, { text: e.target.value }) });
      ta.value = x.text;
      // Type a name (the list filters as you type); the server matches it to a member.
      const owner = makeCombo(h("input", { placeholder: "Owner", title: "Owner",
        value: x.owner ? (people[x.owner]?.name || "") : "", class: "ownerbox",
        onchange: (e) => send(`/business/${x.id}/edit`, { owner: e.target.value.trim() }) }),
        D.members.map((m) => ({ value: m.name, note: m.callsign || "" })), { empty: "(no owner)" });
      // An empty date box looks like nothing at all on some phones, so until there's a date it's a button.
      const dateBox = h("input", { type: "date", value: x.due || "", "aria-label": "Due date",
        onchange: (e) => send(`/business/${x.id}/edit`, { due: e.target.value }) });
      const due = x.due ? h("label", { class: "due" }, "Due ", dateBox) : h("button", { class: "btn small ghost", type: "button",
        onclick: (e) => { const l = h("label", { class: "due" }, "Due ", dateBox); e.target.replaceWith(l); dateBox.focus();
          try { dateBox.showPicker(); } catch (_) { /* older browsers: the focused box is enough */ } } }, "+ Due date");
      return h("div", { class: "bizitem" + (done ? " done" : "") + (x.overdue ? " overdue" : "") },
        h("div", { class: "row" }, ta,
          h("button", { class: "btn small" + (done ? "" : " ghost"), title: done ? "Not done after all" : "Mark done",
            onclick: () => send(`/business/${x.id}/done`, { done: done ? 0 : 1 }) }, done ? [emo("✅", "check"), " Done"] : "Done"),
          isNew ? h("button", { class: "btn small ghost", title: "Delete (added by mistake)",
            onclick: () => { if (confirm("Delete this item?")) send(`/business/${x.id}/delete`, {}); } }, emo("✖️", "x")) : null),
        h("div", { class: "row meta" }, owner, due,
          x.overdue ? h("span", { class: "badge warn" }, `overdue since ${fmtDate(x.due)}`) : null,
          !isNew ? h("span", { class: "hint" }, `first mentioned ${fmtDate(x.opened)}`) : null),
        // comments since the last meeting, each with when it was written ("Tue 7:41 PM"); the leader picks which go in
        // the minutes. Said at this meeting: in them already (✖ takes back a mistyped one).
        (x.new_comments || []).map((c) => {
          const said = c.meeting === M.slug, picked = said || c.minutes === M.slug;
          return h("div", { class: "bizcomment" + (picked ? " inmin" : "") }, h("b", {}, c.name), " ",
            h("span", { class: "hint" }, said ? "at the meeting" : new Date(c.at * 1000).toLocaleString([], { weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })),
            " ",
            said ? h("button", { class: "btn small ghost", title: "Delete (recorded by mistake)",
              onclick: () => { if (confirm("Delete what was recorded?")) send(`/business/${x.id}/comments/${c.id}/delete`, {}); } }, emo("✖️", "x"))
              : h("button", { class: "btn small" + (picked ? "" : " ghost"), title: picked ? "Take it out of the minutes" : "Put it in tonight's minutes, under this item",
                onclick: () => send(`/business/${x.id}/comments/${c.id}/minutes`, { on: picked ? 0 : 1 }) }, picked ? [emo("✅", "check"), " In the minutes"] : "Add to the minutes"),
            commentBody(c));
        }),
        saidForm(x));
    };
    // A comment's formatted text (server-rendered, safe HTML), clamped to two lines; the button shows only when there's more, and opens it in place.
    const commentBody = (c) => {
      const body = h("div", { class: "ctext clamp" });
      body.innerHTML = c.html;
      if ((c.pictures || []).length) body.append(h("div", { class: "pics" }, c.pictures.map((p, i) => h("img", { src: `/pictures/${p}-s.jpg`, alt: `Picture ${i + 1} with ${c.name}'s comment` }))));
      const btn = h("button", { class: "btn small ghost", type: "button", hidden: true, "aria-expanded": "false",
        onclick: () => { const open = body.classList.toggle("clamp") === false; btn.setAttribute("aria-expanded", open); btn.textContent = open ? "Show less" : "Show more"; } }, "Show more");
      // measured once the box is on screen (and again if it resizes); a clamped box that overflows has more to show
      new ResizeObserver(() => { if (body.classList.contains("clamp")) btn.hidden = body.scrollHeight <= body.clientHeight + 1; }).observe(body);
      return h("div", {}, body, btn);
    };
    // What someone says about an item at the meeting: their comment on it, and in the minutes under it
    const saidForm = (x) => {
      const who = makeCombo(h("input", { placeholder: "Who said it", "aria-label": "Who said it", class: "ownerbox" }),
        [...D.members.map((m) => ({ value: m.name, note: m.callsign || "" })), ...M.guests.map((g) => ({ value: g.name, note: "guest" }))], { empty: "(or type a name)" });
      const ta = h("textarea", { rows: 1, "data-format": true, maxlength: 5000, placeholder: "What they said", "aria-label": "What they said", oninput: (e) => grow(e.target) });
      const go = () => {
        const name = who.querySelector("input").value.trim();
        if (!name || !ta.value.trim()) { alert("Pick who said it and type what they said."); return; }
        send(`/business/${x.id}/said`, { who: name, text: ta.value });
      };
      return h("details", { class: "said" }, h("summary", {}, "Record what's said"), h("div", { class: "row" }, who), ta,
        h("button", { class: "btn small", onclick: go }, "Add to the item and the minutes"));
    };
    const add = () => {
      const ta = h("textarea", { rows: 1, "data-format": true, placeholder: "New item…", "aria-label": "New item", oninput: (e) => grow(e.target) });
      const go = async () => { if (ta.value.trim() && await send("/business/add", { text: ta.value })) { ta.value = ""; grow(ta); } };
      return h("div", { class: "row" }, ta, h("button", { class: "btn small", onclick: go }, "+ Add item"));
    };
    // A motion: who moved it, what, who seconded, and how it went (no default: the leader picks the result).
    const motionForm = () => {
      const person = (label, exclude) => makeCombo(h("input", { placeholder: label, "aria-label": label, class: "ownerbox" }),
        D.members.map((m) => ({ value: m.name, note: m.callsign || "" })), { empty: "(nobody)", exclude });
      const mover = person("Moved by", () => val(second)), second = person("Seconded by", () => val(mover));
      const what = h("textarea", { rows: 1, "data-format": true, "data-pic": "motion", placeholder: "buy a new antenna for the repeater", "aria-label": "Moved to", oninput: (e) => grow(e.target) });
      const mpics = [], thumbs = h("div", { class: "pics" });  // uploaded at once (/api/pictures), sent with the motion
      const picker = h("input", { type: "file", "data-picker": "motion", accept: "image/jpeg,image/png,image/webp", class: "sr", onchange: async (e) => {
        const f = e.target.files[0]; e.target.value = "";
        if (!f || mpics.length >= 4) return;
        const fd = new FormData(); fd.append("file", f);
        const r = await fetch("/api/pictures", { method: "POST", body: fd }), j = await r.json().catch(() => ({}));
        if (!r.ok || !j.id) { alert(j.error || "Couldn't add that picture."); return; }
        mpics.push(j.id); thumbs.append(h("img", { src: `/pictures/${j.id}-s.jpg`, alt: `Picture ${mpics.length} with the motion` }));
      } });
      const result = h("select", { "aria-label": "Result" }, ["", "Passed", "Failed", "Tabled", "Withdrawn", "Died for lack of a second"]
        .map((v) => h("option", { value: v }, v || "Result…")));
      const val = (w) => w.querySelector("input").value.trim();
      const go = async () => {
        if (!val(mover) || !what.value.trim() || !result.value) { alert("Fill in who moved it, what it was, and the result."); return; }
        if (await send("/business/motion", { mover: val(mover), seconder: val(second), what: what.value, result: result.value, pics: mpics })) { mform = motionForm(); draw(); }
      };
      const q = quorum();
      return h("div", { class: "bizitem motion" },
        q && !q.ok ? h("p", { class: "msg warn", role: "status" }, "No quorum yet: motions shouldn't be brought up until there is one.") :
          q ? h("p", { class: "hint", role: "status" }, `A motion needs ${q.majority} vote${q.majority === 1 ? "" : "s"} to pass (a majority of the members present).`) : null,
        h("div", { class: "row" }, mover, h("span", {}, "moved to")), what, picker, thumbs,
        h("div", { class: "row" }, second, result, h("button", { class: "btn small", onclick: go }, "Add motion")));
    };
    // New Business: two cards, Items and Motions, both open, each with its box ready (Jim, 2026-11-20)
    const card = (title, rows, form) => h("details", { class: "card bizcard", open: true }, h("summary", {}, h("h3", {}, title)), ...rows, form);
    let addBox, mform;  // kept across redraws (any save redraws), so a half-typed item or motion isn't lost
    const draw = () => {
      const items = isNew ? D.business.new : D.business.old;
      if (isNew) { addBox ??= add(); mform ??= motionForm(); }
      box.replaceChildren(...(isNew ? [card("Items", items.filter((x) => !x.motion).map(row), addBox),
        card("Motions", items.filter((x) => x.motion).map(row), mform)]
        : [...(items.length ? items.map(row) : [h("p", { class: "muted" }, "No open items.")]),
          h("p", { class: "hint" }, "New items go in New Business. ", h("a", { href: "/business", target: "_blank", rel: "noopener" }, "All business items"))]));
      setTimeout(() => box.querySelectorAll("textarea").forEach(grow), 0);
    };
    draw();
    return h("div", {}, h("h2", {}, sec), shint(HINTS[sec]), box);
  }

  // ---------- chow: did tonight's member bring it? The rest is read-only, from the chow schedule. ----------
  function chowSection() {
    const C = D.chow;
    const box = h("div", { class: "chowbox" }), list = h("ul", { class: "chowlist" });
    const showList = () => {
      M.sections["Upcoming Chow"] = C.section.map((t) => ({ text: t, member: null }));
      list.replaceChildren(...(C.section.length ? C.section.map((t) => h("li", {}, t)) : [h("li", { class: "muted" }, "Nothing scheduled.")]));
    };
    const post = async (data) => {
      const r = await fetch("/chow/record", { method: "POST", headers: { Accept: "application/json" },
        body: new URLSearchParams({ date: C.date, note: "", ...data }) });
      const j = await r.json();
      if (!r.ok) { alert(j.error || "Couldn't save"); return; }
      Object.assign(C, { covered_by: null, note: "" }, data, { section: j.section }); draw(); showList(); save();
    };
    const draw = () => {
      if (!C.member) { box.replaceChildren(h("b", {}, "Chow tonight: "), C.status === "none" ? "no chow tonight." : "nobody was scheduled."); return; }
      const what = h("input", { placeholder: "What they brought (optional)", value: C.note || "", size: 26 });
      const cover = h("select", {}, h("option", { value: "" }, "Who brought it?"),
        D.members.filter((m) => m.id !== C.member).map((m) => h("option", { value: m.id, selected: m.id === C.covered_by }, m.name)));
      const done = { provided: [emo("✅", "check"), " provided"], missed: [emo("❌", "xcircle"), " not provided"], covered: "brought by someone else" }[C.status];
      box.replaceChildren(
        h("div", {}, h("b", {}, "Chow tonight: "), C.name, done ? h("span", { class: "badge" }, " ", done) : null,
          C.status === "covered" && C.covered_by ? ` (${people[C.covered_by]?.name || C.covered_by})` : "",
          C.note ? `: ${C.note}` : ""),
        h("div", { class: "row" }, what,
          h("button", { class: "btn small", onclick: () => post({ status: "provided", note: what.value }) }, "Provided"),
          h("button", { class: "btn small ghost", onclick: () => { if (confirm(`Record that ${C.name} did not provide chow?${C.missed_note ? " " + C.missed_note : ""}`)) post({ status: "missed" }); } }, "Not provided"),
          C.status && C.status !== "scheduled" ? h("button", { class: "btn small ghost", title: "Clear", onclick: () => post({ status: "scheduled" }) }, "Undo") : null),
        h("details", { class: "edit" }, h("summary", {}, "Someone else brought it"),
          h("div", { class: "row" }, cover,
            h("button", { class: "btn small ghost", onclick: () => { if (!cover.value) return alert("Pick who brought it."); post({ status: "covered", covered_by: cover.value, note: what.value }); } }, "Save"))));
    };
    draw(); showList();
    return h("div", {}, h("h2", {}, "Chow"), box,
      shint(HINTS["Upcoming Chow"], " ", h("a", { href: "/chow", target: "_blank", rel: "noopener" }, "Open the Chow page")),
      h("h3", {}, "In the minutes (Upcoming Chow)"), list);
  }

  // ---------- review & submit ----------
  function review() {
    const box = h("div", { class: "hint" }, "Loading preview…");
    const top = submit(), end = submit();  // hidden until the minutes are on screen: never submitted sight unseen (Jim, 2026-11-22)
    top.hidden = end.hidden = true;
    save(true).then(async () => {
      const r = await fetch(`/api/meetings/${M.slug}/live`);
      if (!r.ok) throw new Error(r.status);
      box.className = ""; box.innerHTML = await r.text(); top.hidden = end.hidden = false;
    }).catch(() => { box.className = "msg warn"; box.textContent = "The preview didn't load, so the minutes can't be submitted yet. Go back a step and return to Review to try again."; });
    const missing = present().filter((k) => !S.done.includes(k));
    const invites = M.guests.filter(inviteDue);
    return h("div", {},
      h("h2", {}, "Review"),
      invites.length ? h("div", { class: "invitebox" }, `Before closing: invite ${invites.map((g) => `${g.name} (${ordinal(visitNo(g))} visit)`).join(", ")} to apply for membership.`) : null,
      missing.length ? h("p", { class: "hint" }, `No report recorded for: ${missing.map(label).join(", ")}.`) : null,
      top,
      box,
      end);  // at the end too, where Next is on the other steps (Jim, 2026-11-21)
  }
  function submit() {
    return h("p", {}, h("button", { class: "btn warn", onclick: async () => {
      const E = D.email, who = E.only_to ? `TEST MODE: only to ${E.only_to.join(", ")}` : `all ${E.roster} members on the roster`;
      if (!confirm(`Submit these minutes? They will be published to the archive and emailed (${who}).`)) return;
      await save(true); document.getElementById("submitform").submit();
    } }, "Submit minutes"));
  }

  render();
})();
