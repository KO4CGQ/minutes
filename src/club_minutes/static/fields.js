// A Clear button beside every optional date and time box (base.html loads this on every page). iPhones fill a box in
// with today or now the moment it's tapped, and their picker can't empty it again. Required boxes get none; the
// button shows only while there's something to clear. Boxes added later (the console builds its own) get one too.
(() => {
  const SEL = "input:is([type=date], [type=time], [type=datetime-local], [type=month]):not([required], [readonly], [disabled], [data-noclear])";
  const add = (box) => {
    if (box.parentElement && box.parentElement.classList.contains("clearwrap")) return;
    const name = box.getAttribute("aria-label") || (box.closest("label") && box.closest("label").textContent.trim()) || "the date";
    const b = document.createElement("button");
    b.type = "button";
    b.className = "clearbox";
    b.textContent = "✕";
    b.title = "Clear";
    b.setAttribute("aria-label", `Clear ${name}`);
    const show = () => { b.hidden = !box.value; };
    b.addEventListener("click", (e) => {
      e.preventDefault();  // inside a <label>: don't open the picker again
      box.value = "";
      box.dispatchEvent(new Event("input", { bubbles: true }));
      box.dispatchEvent(new Event("change", { bubbles: true }));  // what the page does with a change (the console saves)
      show();
    });
    box.addEventListener("input", show);
    box.addEventListener("change", show);
    // in a wrapper with the box, so the ✕ sits inside the box's right edge (a box that fills its line keeps doing so)
    const wrap = document.createElement("span");
    wrap.className = "clearwrap" + (getComputedStyle(box).display === "block" ? " block" : "");
    box.replaceWith(wrap);
    wrap.append(box, b);
    show();
  };
  const scan = (node) => {
    if (node.matches && node.matches(SEL)) add(node);
    if (node.querySelectorAll) node.querySelectorAll(SEL).forEach(add);
  };
  scan(document);
  new MutationObserver((changes) => changes.forEach((c) => c.addedNodes.forEach(scan))).observe(document.body, { childList: true, subtree: true });
})();
