// The formatting toolbar (richtext.py): B, I, lists, a web address, QR and Picture, for a text box marked data-format. It appears
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
  const spaced = (v, at, t) => (at && !/\s/.test(v[at - 1]) ? " " : "") + t;  // a space before it, unless there is one
  // Each returns [from, to, replacement] for the box's text v and selection s..e, or null to do nothing. Links always
  // show their full address (a printed copy keeps it): Link puts an address in; QR adds " [QR]" after one (or takes it off).
  const act = {
    b: (v, s, e) => [s, e, `**${v.slice(s, e) || "bold"}**`],
    i: (v, s, e) => [s, e, `*${v.slice(s, e) || "italic"}*`],
    ul: (v, s, e) => listAt(v, s, e, false),
    ol: (v, s, e) => listAt(v, s, e, true),
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
    pic: svg('<rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="9" cy="9.5" r="1.8"/><path d="m21 16-4.5-4.5L7 20"/>'),
  };
  const bar = document.createElement("div");
  bar.className = "fmtbar"; bar.setAttribute("role", "toolbar"); bar.setAttribute("aria-label", "Formatting");
  for (const [k, label, title] of [["b", "B", "Bold"], ["i", "I", "Italic"], ["ul", "", "Bulleted list"], ["ol", "", "Numbered list"],
      ["a", "", "Web address"], ["qr", "", "QR code for a web address (in the PDF and on screen)"], ["pic", "", "Add pictures"]]) {
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
    box.focus();
  });
  // Enter on a list line starts the next item ("- " or the next number); Enter on an empty item ends the list.
  document.addEventListener("keydown", (e) => {
    const t = e.target;
    if (e.key !== "Enter" || e.shiftKey || e.isComposing || !t.matches?.("textarea[data-format]") || t.selectionStart !== t.selectionEnd) return;
    const v = t.value, at = t.selectionStart, from = v.lastIndexOf("\n", at - 1) + 1, m = v.slice(from, at).match(LI);
    if (!m) return;
    e.preventDefault();
    if (!v.slice(from + m[0].length, at).trim()) put(t, "", from, at);
    else put(t, `\n${m[1]}${m[3] ? `${+m[3] + 1}.` : m[2]} `, at, at);
  });
  document.addEventListener("change", (e) => {
    const f = e.target;
    if (!f.matches?.("input[data-picker]") || !f.files.length) return;
    if (f.dataset.picker === "edit") f.form.requestSubmit();  // an item's pictures: added at once
    else if (f.dataset.picker === "comment") note.textContent = [...f.files].map((x) => x.name).join(", ");
  });
})();
