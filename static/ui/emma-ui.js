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
export function shake(node) {
  if (!node) return;
  node.classList.remove("shake");
  void node.offsetWidth;
  node.classList.add("shake");
}
