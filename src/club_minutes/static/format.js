// The formatting toolbar (richtext.py): B, I, lists, a quote, code, a web address, QR and Picture, for a text box marked data-format. It appears
// above the box when you click into it, wraps the selected text in Markdown marks, and fires the box's input and change
// events (so the console saves as usual). A box marked data-pic="report|comment|edit" also gets Picture, which opens the
// file box marked data-picker with the same value. Without JavaScript people type the marks or plain text (which
// reads the same) and use the pictures' own file boxes, which this script hides (the js-fmt class) only when it runs.
(() => {
  document.documentElement.classList.add("js-fmt");
  const WEB = /^https?:\/\/\S+\.\S+$/;
  const ADDR = /https?:\/\/[^\s<>"')\]]+/g;  // an address in the text, as richtext.py finds them
  const ask = () => {  // "example.org" gets https:// in front; anything else that isn't an address says so
    let u = (prompt("The web address, like example.org or https://example.org/page") || "").trim();
    if (!u) return null;
    if (!/^[a-z][\w+.-]*:/i.test(u)) u = `https://${u}`;
    if (WEB.test(u)) return u;
    alert(`"${u}" doesn't look like a web address. Try one like example.org or https://example.org/page.`);
    return null;
  };
  const LI = /^(\s*)(?:([-*])|(\d+)\.)\s+/;  // a list line's mark: "- ", "* " or "3. "
  const list = (t, num) => t.split("\n").map((l, n) => `${num ? `${n + 1}.` : "-"} ${l.replace(LI, "")}`).join("\n");
  // With nothing selected, a list starts on its own line with the caret after the mark, ready to type.
  const listAt = (v, s, e, num) => [s, e, s === e ? (s && v[s - 1] !== "\n" ? "\n" : "") + (num ? "1. " : "- ") : list(v.slice(s, e), num)];
  const lines = (v, s, e) => {  // the whole lines the selection (or the caret) is on
    const end = v.indexOf("\n", e > s && v[e - 1] === "\n" ? e - 1 : e);
    return [v.lastIndexOf("\n", s - 1) + 1, end < 0 ? v.length : end];
  };
  const QM = /^((?:>[ \t]?)+)/;  // a quote line's marks
  const spaced = (v, at, t) => (at && !/\s/.test(v[at - 1]) ? " " : "") + t;  // a space before it, unless there is one
  // Each returns [from, to, replacement, caret in it (default: its end)] for the box's text v and selection s..e, or null to do nothing. Links always
  // show their full address (a printed copy keeps it): Link puts an address in; QR adds " [QR]" after one (or takes it off).
  const act = {
    b: (v, s, e) => [s, e, `**${v.slice(s, e) || "bold"}**`],
    i: (v, s, e) => [s, e, `*${v.slice(s, e) || "italic"}*`],
    ul: (v, s, e) => listAt(v, s, e, false),
    ol: (v, s, e) => listAt(v, s, e, true),
    q: (v, s, e) => {  // "> " on each line (not on one that has it: no quote in a quote), or off if they all have it
      const [a, b] = lines(v, s, e), ls = v.slice(a, b).split("\n"), on = ls.every((l) => QM.test(l));
      return [a, b, ls.map((l) => (on ? l.replace(/^>[ \t]?/, "") : QM.test(l) ? l : l || ls.length === 1 ? `> ${l}` : ">")).join("\n")];  // an empty line on its own: "> ", ready to type
    },
    code: (v, s, e) => {  // a selection over lines, or the caret on an empty line: a block of its own; else `code`
      const sel = v.slice(s, e), [a, b] = lines(v, s, s);
      if (!sel.includes("\n") && (sel || v.slice(a, b).trim())) return [s, e, `\`${sel}\``, sel ? undefined : 1];
      const pre = s && v[s - 1] !== "\n" ? "\n" : "", post = e < v.length && v[e] !== "\n" ? "\n" : "";
      return [s, e, `${pre}\`\`\`\n${sel.replace(/\n$/, "")}\n\`\`\`${post}`, sel ? undefined : pre.length + 4];
    },
    a: (v, s, e) => { const u = ask(); return u && [e, e, spaced(v, e, u)]; },
    qr: (v, s, e) => {
      for (const m of v.matchAll(ADDR)) {  // the caret on (or just after) an address: toggle its [QR]
        const end = m.index + m[0].length, mark = v.slice(end).match(/^[ \t]*\[QR\]/i);
        if (s >= m.index && s <= end + (mark ? mark[0].length : 0))
          return mark ? [end, end + mark[0].length, ""] : [end, end, " [QR]"];
      }
      const u = ask();
      return u && [e, e, spaced(v, e, `${u} [QR]`)];
    },
  };
  // Line icons like the app's own (_icons.html): 24x24, stroke = currentColor; B and I stay letters.
  const svg = (d) => `<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${d}</svg>`;
  const ICON = {
    ul: svg('<path d="M9 6h11M9 12h11M9 18h11"/><circle cx="4.5" cy="6" r="1"/><circle cx="4.5" cy="12" r="1"/><circle cx="4.5" cy="18" r="1"/>'),
    ol: svg('<path d="M10 6h10M10 12h10M10 18h10M4 4.5h1.5V9M4 9h3M4 14.5c.4-.6 2.7-.8 2.7.6 0 1.1-2.7 2-2.7 3.4h3"/>'),
    a: svg('<path d="M10 13a5 5 0 0 0 7.5.5l3-3a5 5 0 0 0-7-7l-1.7 1.7"/><path d="M14 11a5 5 0 0 0-7.5-.5l-3 3a5 5 0 0 0 7 7l1.7-1.7"/>'),
    qr: svg('<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><path d="M14 14h3v3h-3zM20 14v.01M14 20.5h.01M17.5 20.5H21V17"/>'),
    q: svg('<path d="M10 7H6a1 1 0 0 0-1 1v4h5V7zM10 12c0 3-1.5 4.5-4 5M19 7h-4a1 1 0 0 0-1 1v4h5V7zM19 12c0 3-1.5 4.5-4 5"/>'),
    code: svg('<path d="m8 7-5 5 5 5M16 7l5 5-5 5M13.5 5l-3 14"/>'),
    pic: svg('<rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="9" cy="9.5" r="1.8"/><path d="m21 16-4.5-4.5L7 20"/>'),
  };
  const bar = document.createElement("div");
  bar.className = "fmtbar"; bar.setAttribute("role", "toolbar"); bar.setAttribute("aria-label", "Formatting");
  for (const [k, label, title] of [["b", "B", "Bold"], ["i", "I", "Italic"], ["ul", "", "Bulleted list"], ["ol", "", "Numbered list"],
      ["q", "", "Quote"], ["code", "", "Code (on lines of its own: a code block)"], ["a", "", "Web address"], ["qr", "", "QR code for a web address (in the PDF and on screen)"], ["pic", "", "Add pictures"]]) {
    const btn = document.createElement("button");
    Object.assign(btn, { type: "button", className: `fmt fmt-${k}`, title });
    if (ICON[k]) btn.innerHTML = ICON[k]; else btn.textContent = label;  // our own fixed markup, never typed text
    btn.setAttribute("aria-label", title); btn.dataset.fmt = k;
    bar.append(btn);
  }
  const note = document.createElement("span");  // the picture files chosen for a comment
  note.className = "fmtnote"; note.setAttribute("aria-live", "polite");
  bar.append(note);
  let box = null;
  const put = (t, text, from, to) => {  // change the box's text as typing would: its input and change events fire
    t.setRangeText(text, from, to, "end");
    t.dispatchEvent(new Event("input", { bubbles: true }));
    t.dispatchEvent(new Event("change", { bubbles: true }));  // boxes that save on change (a business item's text)
  };
  const picker = () => {  // the file box for this text box, nearest first; none: no Picture (4 already)
    for (let el = box?.dataset.pic && box.parentElement; el; el = el.parentElement) {
      const f = el.querySelector(`input[type=file][data-picker="${box.dataset.pic}"]`);
      if (f) return f;
    }
  };
  document.addEventListener("focusin", (e) => {
    if (!e.target.matches?.("textarea[data-format]")) return;
    box = e.target; box.before(bar); note.textContent = "";
    bar.querySelector(".fmt-pic").hidden = !picker();
  });
  bar.addEventListener("mousedown", (e) => e.preventDefault());  // keep the box's selection
  bar.addEventListener("click", (e) => {
    const k = e.target.closest("[data-fmt]")?.dataset.fmt;
    if (!k || !box) return;
    if (k === "pic") { picker()?.click(); return; }
    const out = act[k](box.value, box.selectionStart, box.selectionEnd);
    if (!out) return;
    put(box, out[2], out[0], out[1]);
    if (out[3] !== undefined) box.setSelectionRange(out[0] + out[3], out[0] + out[3]);
    box.focus();
  });
  // Typing @ in a box lists the Active members (the page's #mentions JSON, club.mentionable()), narrowed by what follows;
  // picking one writes "@[Jane D](n0call) " (richtext.py shows it as a name pill). Arrow keys + Enter or a tap picks, Escape closes.
  const menu = document.createElement("ul");
  Object.assign(menu, { className: "combo-menu mention-menu", id: "mention-list", hidden: true });
  menu.setAttribute("role", "listbox"); menu.setAttribute("aria-label", "Members");
  document.body.append(menu);
  let people = null, shown = [], at = 0, from = 0;
  const closeMenu = () => { menu.hidden = true; box?.removeAttribute("aria-activedescendant"); box?.setAttribute("aria-expanded", "false"); };
  const mark = () => {
    [...menu.children].forEach((li, i) => { li.classList.toggle("at", i === at); li.setAttribute("aria-selected", i === at); });
    box.setAttribute("aria-activedescendant", `mention-${at}`); menu.children[at]?.scrollIntoView({ block: "nearest" });
  };
  const choose = (p) => {
    const t = box, name = (p.short || p.name).replace(/[\[\]\n]/g, "").slice(0, 60);
    closeMenu(); put(t, `@[${name}](${p.id}) `, from, t.selectionStart);
    t.focus();
  };
  const openMenu = (t) => {
    people ??= JSON.parse(document.getElementById("mentions")?.textContent || "[]");
    const m = t.value.slice(0, t.selectionStart).match(/(?:^|\s)@([^\n@[\]]{0,30})$/);
    const q = m?.[1].toLowerCase() ?? "";
    shown = m ? people.filter((p) => [p.name, p.short, p.callsign].some((x) => (x || "").toLowerCase().includes(q))).slice(0, 8) : [];
    if (!shown.length) return closeMenu();
    from = t.selectionStart - q.length - 1; at = 0;
    menu.replaceChildren(...shown.map((p, i) => {
      const li = document.createElement("li");
      li.id = `mention-${i}`; li.setAttribute("role", "option"); li.textContent = p.short || p.name;
      if (p.name !== p.short || p.callsign) { const s = document.createElement("span"); s.textContent = [p.name !== p.short && p.name, p.callsign].filter(Boolean).join(" · "); li.append(s); }
      li.addEventListener("mousedown", (e) => { e.preventDefault(); choose(p); });  // keeps the box's focus, also on a tap
      return li;
    }));
    const host = t.closest("dialog") || document.body;  // in a modal dialog (a business item's comments) the list must be
    if (menu.parentElement !== host) host.append(menu);   // inside it: anything outside is drawn under it and can't be tapped
    const r = t.getBoundingClientRect();
    menu.hidden = false; menu.style.left = `${Math.max(4, r.left)}px`;
    const h = menu.offsetHeight;  // under the box, or over it when the screen ends there
    menu.style.top = `${r.bottom + h + 8 > innerHeight && r.top > h + 8 ? r.top - h - 4 : r.bottom + 4}px`;
    box = t; t.setAttribute("aria-controls", menu.id); t.setAttribute("aria-expanded", "true"); mark();
  };
  document.addEventListener("input", (e) => { if (e.target.matches?.("textarea[data-format]") && !e.isComposing) { box = e.target; openMenu(e.target); } });
  document.addEventListener("focusout", (e) => { if (e.target === box) closeMenu(); });
  document.addEventListener("keydown", (e) => {
    if (menu.hidden || e.target !== box) return;
    if (e.key === "ArrowDown" || e.key === "ArrowUp") { at = (at + (e.key === "ArrowDown" ? 1 : shown.length - 1)) % shown.length; mark(); }
    else if (e.key === "Enter" || e.key === "Tab") choose(shown[at]);
    else if (e.key === "Escape") closeMenu();
    else return;
    e.preventDefault(); e.stopImmediatePropagation();
  }, true);
  // Enter on a list line starts the next item ("- " or the next number), on a quote line the next quote line; Enter on
  // an empty one ends the list or quote. Not inside a code block (an odd number of ``` lines above). On beforeinput, not
  // keydown: phone keyboards send their Enter as an unnamed key, and Shift+Enter would break out of a quote.
  document.addEventListener("beforeinput", (e) => {
    const t = e.target;
    if (!/^insert(LineBreak|Paragraph)$/.test(e.inputType) || !t.matches?.("textarea[data-format]") || t.selectionStart !== t.selectionEnd) return;
    const v = t.value, at = t.selectionStart, from = v.lastIndexOf("\n", at - 1) + 1, m = v.slice(from, at).match(LI) || v.slice(from, at).match(QM);
    if (!m || (v.slice(0, from).match(/^```/gm) || []).length % 2) return;
    e.preventDefault();
    if (!v.slice(from + m[0].length, at).trim()) put(t, "", from, at);
    else if (m[0].startsWith(">")) put(t, `\n${m[1].replace(/[ \t]*$/, " ")}`, at, at);
    else put(t, `\n${m[1]}${m[3] ? `${+m[3] + 1}.` : m[2]} `, at, at);
  });
  // A box marked data-count shows how much of its maxlength is used, after its label (Jim, 2026-11-20: business text,
  // comments and notes; it was cut off silently).
  const counts = new WeakMap();
  const count = (t) => { counts.get(t).textContent = `${t.value.length.toLocaleString()} / ${t.maxLength.toLocaleString()} characters`; };
  document.querySelectorAll("textarea[data-count]").forEach((t, i) => {
    const c = document.createElement("small");
    Object.assign(c, { className: "fmtcount", id: `count-${i}` });
    (t.closest("label") || t).after(c); counts.set(t, c); count(t);
    t.setAttribute("aria-describedby", `${t.getAttribute("aria-describedby") || ""} count-${i}`.trim());
  });
  document.addEventListener("input", (e) => counts.has(e.target) && count(e.target));
  document.addEventListener("change", (e) => {
    const f = e.target;
    if (!f.matches?.("input[data-picker]") || !f.files.length) return;
    if (f.dataset.picker === "edit") f.form.requestSubmit();  // an item's pictures: added at once
    else if (f.dataset.picker === "comment") note.textContent = [...f.files].map((x) => x.name).join(", ");
  });
})();
