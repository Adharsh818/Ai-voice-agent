// Emma UI behaviours shared by every page: the apple.com staggered reveal, the segmented
// control's sliding thumb, and the floating glass toasts. No dependencies.

const reducedMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

// apple.com's StaggeredFadeIn: a group fades and rises in, child by child, when it enters the
// viewport. Each child gets --i so the CSS can delay it by i × step. Runs once per group.
const revealObserver = "IntersectionObserver" in window ? new IntersectionObserver((entries) => {
  for (const entry of entries) {
    if (!entry.isIntersecting) continue;
    entry.target.classList.add("is-in");
    revealObserver.unobserve(entry.target);
    finishLater(entry.target);
  }
}, { rootMargin: "0px 0px -15% 0px", threshold: 0 }) : null;   // apple.com: start "t - 85vh"

// Like apple.com's "staggered-end": once every item has arrived, drop the reveal transition so
// each element's own transitions (a card's hover lift) apply again.
function finishLater(group) {
  const css = getComputedStyle(group);
  const ms = (name, fallback) => {
    const v = css.getPropertyValue(name).trim();
    return v ? parseFloat(v) * (v.endsWith("ms") ? 1 : 1000) : fallback;
  };
  const items = group.hasAttribute("data-reveal") ? group.children.length : 1;
  const total = (items - 1) * ms("--reveal-step", 150) + Math.max(ms("--reveal-fade", 900), ms("--reveal-rise", 700));
  clearTimeout(group._revealDone);
  group._revealDone = setTimeout(() => group.classList.add("is-done"), total + 50);
}

export function reveal(root = document) {
  const groups = [];
  if (root instanceof Element && (root.hasAttribute("data-reveal") || root.hasAttribute("data-reveal-self"))) groups.push(root);
  groups.push(...root.querySelectorAll("[data-reveal]:not(.is-in), [data-reveal-self]:not(.is-in)"));
  for (const group of groups) {
    if (group.hasAttribute("data-reveal")) {
      [...group.children].forEach((child, i) => child.style.setProperty("--i", String(i)));
    }
    if (reducedMotion() || !revealObserver) {
      group.classList.add("is-in", "is-done");
    } else {
      revealObserver.observe(group);
    }
  }
}

// apple.com's card hover (overview Card component): --card-element-scale = (width + 6) / width,
// recomputed on resize, so every card grows by exactly 6 px whatever its size.
const cardObserver = "ResizeObserver" in window ? new ResizeObserver((entries) => {
  for (const entry of entries) {
    const width = entry.target.offsetWidth;          // layout width, unaffected by the hover transform
    if (width > 0) entry.target.style.setProperty("--card-element-scale", ((width + 6) / width).toFixed(5));
  }
}) : null;

export function cardHover(root = document) {
  if (!cardObserver) return;
  root.querySelectorAll(".tile-hover").forEach((card) => cardObserver.observe(card));
}

// Re-run a group's reveal (a dashboard section opened again).
export function replay(group) {
  if (!group) return;
  clearTimeout(group._revealDone);
  group.classList.remove("is-in", "is-done");
  void group.offsetWidth;          // restart the transition from the hidden state
  reveal(group);
}

// A segmented control (buttons with aria-pressed) gets a white thumb that slides to the chosen one.
export function segmented(control) {
  if (!control) return () => {};
  let thumb = control.querySelector(".seg-thumb");
  if (!thumb) {
    thumb = document.createElement("span");
    thumb.className = "seg-thumb";
    thumb.setAttribute("aria-hidden", "true");
    control.prepend(thumb);
  }
  const place = () => {
    const chosen = control.querySelector('button[aria-pressed="true"]') || control.querySelector("button");
    if (!chosen) return;
    thumb.style.width = `${chosen.offsetWidth}px`;
    thumb.style.transform = `translateX(${chosen.offsetLeft}px)`;
  };
  place();
  new MutationObserver(place).observe(control, { attributes: true, subtree: true, attributeFilter: ["aria-pressed"] });
  window.addEventListener("resize", place);
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(place);
  return place;
}

// Floating glass toast at the bottom centre.
export function toast(message, kind = "") {
  let stack = document.getElementById("toasts");
  if (!stack) {
    stack = document.createElement("div");
    stack.id = "toasts";
    document.body.append(stack);
  }
  stack.classList.add("toast-stack");
  const node = document.createElement("div");
  node.className = `toast-pill glass-pill ${kind}`;
  node.setAttribute("role", kind === "error" ? "alert" : "status");
  node.textContent = message;
  stack.append(node);
  const life = kind === "urgent" ? 12000 : 4500;
  setTimeout(() => {
    node.classList.add("leaving");
    node.addEventListener("animationend", () => node.remove(), { once: true });
    setTimeout(() => node.remove(), 600);
  }, life);
}

// Restart the shake animation on an element (a wrong password).
export function shake(node, className = "shake") {
  if (!node) return;
  node.classList.remove(className);
  void node.offsetWidth;
  node.classList.add(className);
}

// Restart the stronger horizontal shake from the 22 Oct ledger (the sign-in page).
export function shakeX(node) { shake(node, "e-shake"); }

/* ==================================================================
   Components from the 22 Oct ledger (docs/APPLE_DESIGN_PLAN.md 11.4). Each one pairs with
   the e-* classes in emma-ui.css. Everything is CSS transitions (interruptible: a new target
   starts from the on-screen value) except the sheet drag, which follows the finger.
   ================================================================== */

const px = (n) => `${Math.round(n * 100) / 100}px`;
const isShown = (el) => Boolean(el) && !el.hidden && el.offsetWidth > 0;

// Turn transitions off for the first layout (apple.com's tabnav-disable-transitions).
function withoutAnimation(control, fn) {
  control.classList.add("e-no-anim");
  fn();
  void control.offsetWidth;
  requestAnimationFrame(() => requestAnimationFrame(() => control.classList.remove("e-no-anim")));
}

// B1: a floating capsule nav that slides away while scrolling down and comes back on the way up.
export function capsuleNav(wrap) {
  if (!wrap) return;
  let last = window.scrollY;
  let queued = false;
  const update = () => {
    queued = false;
    const y = window.scrollY;
    if (y > last + 4 && y > 80) wrap.classList.add("is-hidden");
    else if (y < last - 4 || y <= 80) wrap.classList.remove("is-hidden");
    last = y;
  };
  window.addEventListener("scroll", () => {
    if (!queued) { queued = true; requestAnimationFrame(update); }
  }, { passive: true });
  wrap.addEventListener("focusin", () => wrap.classList.remove("is-hidden"));
}

// B4: segmented tab nav. Buttons carry aria-pressed (or aria-selected); a dark indicator
// slides under the chosen one (left/width, 320ms ease-out) and its label flips after 160ms.
// Clicking a button selects it unless { manage: false }; onSelect(button) is told either way.
export function tabnav(control, { onSelect, manage = true } = {}) {
  if (!control) return () => {};
  let indicator = control.querySelector(":scope > .e-tabnav-indicator");
  if (!indicator) {
    indicator = document.createElement("span");
    indicator.className = "e-tabnav-indicator";
    indicator.setAttribute("aria-hidden", "true");
    control.prepend(indicator);
  }
  const buttons = () => [...control.querySelectorAll(":scope > button")];
  const chosen = () => buttons().find((b) => b.getAttribute("aria-pressed") === "true" || b.getAttribute("aria-selected") === "true");
  const place = () => {
    const b = chosen();
    control.style.setProperty("--tab-x", px(b ? b.offsetLeft : 4));
    control.style.setProperty("--tab-w", px(b ? b.offsetWidth : 0));
  };
  withoutAnimation(control, place);
  control.addEventListener("click", (event) => {
    const b = event.target.closest("button");
    if (!b || b.parentElement !== control) return;
    if (manage) {
      const attr = b.hasAttribute("aria-selected") ? "aria-selected" : "aria-pressed";
      buttons().forEach((other) => other.setAttribute(attr, String(other === b)));
    }
    if (onSelect) onSelect(b);
  });
  new MutationObserver(place).observe(control, { attributes: true, subtree: true, attributeFilter: ["aria-pressed", "aria-selected", "hidden"] });
  if ("ResizeObserver" in window) new ResizeObserver(place).observe(control);
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(() => withoutAnimation(control, place));
  return place;
}

// B3: one shared bubble slides under the selected option (.is-selected, aria-pressed,
// aria-checked or aria-selected), stretching in X and squashing in Y while it travels.
// It also follows a press, so the choice answers on pointer-down.
const SELECTED = ':scope > .is-selected, :scope > [aria-pressed="true"], :scope > [aria-checked="true"], :scope > [aria-selected="true"]';
export function bubbles(control) {
  if (!control) return { place() {} };
  let bubble = control.querySelector(":scope > .e-bubble");
  if (!bubble) {
    bubble = document.createElement("span");
    bubble.className = "e-bubble is-empty";
    bubble.setAttribute("aria-hidden", "true");
    control.prepend(bubble);
  }
  let pressed = null;
  let shown = false;
  let lastX = null, lastW = null;
  let settle;
  const target = () => (isShown(pressed) ? pressed : [...control.querySelectorAll(SELECTED)].find(isShown));

  function place() {
    const el = target();
    if (!el) {
      bubble.classList.add("is-empty");
      shown = false;
      return;
    }
    const x = el.offsetLeft, w = el.offsetWidth;
    if (x === lastX && w === lastW && shown) return;
    const set = () => {
      bubble.style.setProperty("--bubble-x", px(x));
      bubble.style.setProperty("--bubble-w", px(w));
    };
    if (!shown) {
      withoutAnimation(control, set);     // appear in place, never slide in from 0
    } else {
      set();
      if (lastX !== null && Math.abs(x - lastX) > 1 && !reducedMotion()) {
        control.classList.add("is-moving");
        bubble.style.setProperty("--bubble-sx", "1.08");
        bubble.style.setProperty("--bubble-sy", "0.92");
        clearTimeout(settle);
        settle = setTimeout(() => {
          control.classList.remove("is-moving");
          bubble.style.setProperty("--bubble-sx", "1");
          bubble.style.setProperty("--bubble-sy", "1");
        }, 120);
      }
    }
    bubble.classList.remove("is-empty");
    shown = true;
    lastX = x;
    lastW = w;
  }

  control.addEventListener("pointerdown", (event) => {
    const item = event.target.closest("button, a");
    if (!item || item.parentElement !== control || item.disabled) return;
    pressed = item;
    place();
  });
  const release = () => { pressed = null; requestAnimationFrame(place); };
  control.addEventListener("pointerleave", release);
  control.addEventListener("pointercancel", release);
  control.addEventListener("click", release);

  const watch = new MutationObserver((records) => {
    if (records.every((r) => r.target === bubble || (r.target === control && r.attributeName === "class"))) return;
    observeChildren();
    place();
  });
  watch.observe(control, { attributes: true, subtree: true, childList: true, characterData: true,
    attributeFilter: ["class", "hidden", "aria-pressed", "aria-checked", "aria-selected"] });
  const sizes = "ResizeObserver" in window ? new ResizeObserver(() => place()) : null;
  function observeChildren() {
    if (!sizes) return;
    sizes.disconnect();
    sizes.observe(control);
    [...control.children].forEach((c) => c !== bubble && sizes.observe(c));
  }
  observeChildren();
  place();
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(place);
  return { place };
}

// FLIP: when a container's children change place (one appears, leaves or changes width), each
// child glides from where it was drawn to its new place instead of jumping. It starts from the
// on-screen position, so a change during a glide redirects it smoothly.
export function flip(container, { selector = ":scope > :not(.e-bubble)", duration = 400, easing = "cubic-bezier(0.25, 0.1, 0.3, 1)" } = {}) {
  if (!container) return () => {};
  const last = new WeakMap();
  const items = () => [...container.querySelectorAll(selector)].filter((el) => el.offsetParent !== null);
  function run() {
    for (const el of items()) {
      const x = el.offsetLeft, y = el.offsetTop;
      const prev = last.get(el);
      last.set(el, { x, y });
      if (!prev || reducedMotion()) continue;
      const t = getComputedStyle(el).transform;
      const m = t && t !== "none" ? new DOMMatrixReadOnly(t) : { m41: 0, m42: 0 };
      const dx = prev.x + m.m41 - x, dy = prev.y + m.m42 - y;
      if (Math.abs(dx) < 0.5 && Math.abs(dy) < 0.5) continue;
      el.style.transition = "none";
      el.style.transform = `translate(${px(dx)}, ${px(dy)})`;
      void el.offsetWidth;
      el.style.transition = `transform ${duration}ms ${easing}`;
      el.style.transform = "";
      clearTimeout(el._flipDone);
      el._flipDone = setTimeout(() => el.style.removeProperty("transition"), duration + 40);
    }
  }
  run();
  new MutationObserver(run).observe(container, { childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ["hidden", "class"] });
  if ("ResizeObserver" in window) {
    const sizes = new ResizeObserver(run);
    sizes.observe(container);
    [...container.children].forEach((c) => sizes.observe(c));
  }
  return run;
}

// B2: a materialising pill bar. Keeps --bar-w at the bar's width so the shared glass glides to a
// new size, holds a pill that is being hidden for a 200ms fade-out before it leaves the layout,
// and marks the bar settled once its entrance has played.
export function materialise(bar) {
  if (!bar) return;
  setTimeout(() => bar.classList.add("is-settled"), reducedMotion() ? 0 : 1100);
  const width = () => bar.style.setProperty("--bar-w", px(bar.offsetWidth));
  width();
  if ("ResizeObserver" in window) new ResizeObserver(width).observe(bar);
  const shown = new Set([...bar.children].filter((c) => !c.hidden));
  new MutationObserver((records) => {
    for (const r of records) {
      const el = r.target;
      if (r.attributeName !== "hidden" || el.parentElement !== bar) continue;
      if (el.hidden && shown.has(el) && !reducedMotion() && bar.classList.contains("is-settled")) {
        el.classList.add("is-leaving");
        clearTimeout(el._leave);
        el._leave = setTimeout(() => el.classList.remove("is-leaving"), 200);
      } else if (!el.hidden) {
        clearTimeout(el._leave);
        el.classList.remove("is-leaving");
      }
      if (el.hidden) shown.delete(el); else shown.add(el);
    }
  }).observe(bar, { attributes: true, subtree: true, attributeFilter: ["hidden"] });
}

// B5: overshoot pop-in, index x 50ms apart; popOut leaves fast and all together.
export function popIn(nodes) {
  const list = [...(nodes instanceof Element ? [nodes] : nodes)];
  list.forEach((node, i) => {
    node.style.setProperty("--i", String(i));
    node.classList.add("e-pop");
    node.classList.remove("is-out", "is-in");
  });
  if (list.length) void list[0].offsetWidth;
  list.forEach((node) => node.classList.add("is-in"));
}
export function popOut(nodes) {
  const list = [...(nodes instanceof Element ? [nodes] : nodes)];
  list.forEach((node) => { node.classList.remove("is-in"); node.classList.add("e-pop", "is-out"); });
  return new Promise((resolve) => setTimeout(resolve, reducedMotion() ? 0 : 200));
}

// B14: an 8-spoke spinner element.
export function spinner(size) {
  const el = document.createElement("span");
  el.className = "e-spinner";
  el.setAttribute("aria-hidden", "true");
  for (let i = 0; i < 8; i++) el.append(document.createElement("i"));
  if (size) el.style.setProperty("--spinner-size", px(size));
  return el;
}

// B10: a stretching-pill level meter. The middle dot stretches toward a 48px pill with the
// level, and its neighbours follow continuously. Returns update(level 0..1, active, sweep):
// with sweep the stretch travels gently back and forth (a "working on it" shimmer).
export function levelMeter(el, count = 5) {
  if (!el) return () => {};
  el.classList.add("e-meter");
  el.replaceChildren(...Array.from({ length: count }, () => document.createElement("i")));
  const dots = [...el.children];
  const centre = (count - 1) / 2;
  dots[Math.round(centre)].classList.add("is-current");
  return (level, active = true, sweep = false) => {
    const amount = active ? 0.22 + 0.78 * Math.min(1, Math.max(0, level)) : 0;
    const at = sweep && !reducedMotion() ? centre + Math.sin(performance.now() / 420) * centre : centre;
    dots.forEach((dot, i) => {
      const near = Math.max(0, 1 - Math.abs(i - at) / 1.6);
      dot.style.setProperty("--w", px(8 + 40 * amount * near));
    });
  };
}

// B17: accordion. Heads are .e-acc-head buttons followed by an .e-acc-tray.
export function accordion(root, { single = false } = {}) {
  if (!root) return;
  root.addEventListener("click", (event) => {
    const head = event.target.closest(".e-acc-head");
    if (!head || !root.contains(head)) return;
    const open = head.getAttribute("aria-expanded") !== "true";
    if (single && open) root.querySelectorAll('.e-acc-head[aria-expanded="true"]').forEach((h) => h.setAttribute("aria-expanded", "false"));
    head.setAttribute("aria-expanded", String(open));
  });
}

// B6: sheet / modal. Markup:
//   <div class="e-sheet-root" hidden><div class="e-sheet-scrim"></div>
//     <div class="e-sheet" role="dialog" aria-modal="true" tabindex="-1">
//       <div class="e-sheet-handle"></div>
//       <div class="e-sheet-close-wrap"><button class="e-sheet-close" aria-label="Close">...</button></div>
//       ...content...</div></div>
// On phones it is a bottom sheet: drag the handle down to dismiss. The sheet follows the
// finger 1:1, a flick is projected forward the way iOS does, and its speed carries into the close.
export function sheet(root, { onClose } = {}) {
  if (!root) return { open() {}, close() {} };
  const panel = root.querySelector(".e-sheet");
  const handle = root.querySelector(".e-sheet-handle");
  const phone = window.matchMedia("(max-width: 734px)");
  let opener = null;
  let hideTimer;

  function open(from = document.activeElement) {
    clearTimeout(hideTimer);
    opener = from;
    panel.style.removeProperty("--drag-y");
    panel.style.removeProperty("transition-duration");
    root.hidden = false;
    document.documentElement.classList.add("e-sheet-open");
    void root.offsetWidth;
    root.classList.add("is-open");
    const first = panel.querySelector("[autofocus]") || panel;
    first.focus({ preventScroll: true });
  }

  function close() {
    if (!root.classList.contains("is-open")) return;
    root.classList.remove("is-open");
    clearTimeout(hideTimer);
    hideTimer = setTimeout(() => {
      root.hidden = true;
      document.documentElement.classList.remove("e-sheet-open");
      panel.style.removeProperty("--drag-y");
      panel.style.removeProperty("transition-duration");
    }, reducedMotion() ? 200 : 600);
    if (opener && opener.focus) opener.focus({ preventScroll: true });
    if (onClose) onClose();
  }

  root.addEventListener("click", (event) => {
    if (event.target.closest(".e-sheet-close, [data-sheet-close]") || event.target.classList.contains("e-sheet-scrim")
      || event.target === root) close();
  });
  root.addEventListener("keydown", (event) => {
    if (event.key === "Escape") { event.stopPropagation(); close(); return; }
    if (event.key !== "Tab") return;
    const focusable = [...panel.querySelectorAll('a[href], button:not([disabled]), input:not([disabled]), select, textarea, [tabindex]:not([tabindex="-1"])')]
      .filter((el) => el.offsetParent !== null);
    if (!focusable.length) return;
    const first = focusable[0], last = focusable[focusable.length - 1];
    if (event.shiftKey && (document.activeElement === first || document.activeElement === panel)) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  });

  if (handle) {
    let startY = 0, y = 0, history = [], dragging = false;
    const rubber = (over, size) => (over * size * 0.55) / (size + 0.55 * Math.abs(over));
    handle.addEventListener("pointerdown", (event) => {
      if (!phone.matches || !root.classList.contains("is-open")) return;
      dragging = true;
      startY = event.clientY;
      y = 0;
      history = [{ y: 0, t: event.timeStamp }];
      handle.setPointerCapture(event.pointerId);
      panel.classList.add("is-dragging");
    });
    handle.addEventListener("pointermove", (event) => {
      if (!dragging) return;
      const dy = event.clientY - startY;
      y = dy >= 0 ? dy : rubber(dy, panel.offsetHeight);
      panel.style.setProperty("--drag-y", px(y));
      history.push({ y, t: event.timeStamp });
      if (history.length > 6) history.shift();
    });
    const end = () => {
      if (!dragging) return;
      dragging = false;
      panel.classList.remove("is-dragging");
      const a = history[0], b = history[history.length - 1];
      const v = a && b && b.t > a.t ? ((b.y - a.y) / (b.t - a.t)) * 1000 : 0;   // px/s
      const projected = y + ((v / 1000) * 0.998) / (1 - 0.998) * 0.25;          // iOS projection, softened
      const h = panel.offsetHeight;
      if (projected > h * 0.4 || v > 900) {
        const remaining = Math.max(0, h - y);
        const ms = Math.min(320, Math.max(140, (remaining / Math.max(v, 600)) * 1000));
        panel.style.setProperty("transition-duration", `${Math.round(ms)}ms`);
        close();
      } else {
        panel.style.removeProperty("--drag-y");    // settles back from where the finger left it
      }
    };
    handle.addEventListener("pointerup", end);
    handle.addEventListener("pointercancel", end);
  }
  return { open, close };
}
