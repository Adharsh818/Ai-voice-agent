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
  }
}, { rootMargin: "0px 0px -12% 0px", threshold: 0.01 }) : null;

export function reveal(root = document) {
  const groups = [];
  if (root instanceof Element && (root.hasAttribute("data-reveal") || root.hasAttribute("data-reveal-self"))) groups.push(root);
  groups.push(...root.querySelectorAll("[data-reveal]:not(.is-in), [data-reveal-self]:not(.is-in)"));
  for (const group of groups) {
    if (group.hasAttribute("data-reveal")) {
      [...group.children].forEach((child, i) => child.style.setProperty("--i", String(i)));
    }
    if (reducedMotion() || !revealObserver) {
      group.classList.add("is-in");
    } else {
      revealObserver.observe(group);
    }
  }
}

// Re-run a group's reveal (a dashboard section opened again).
export function replay(group) {
  if (!group) return;
  group.classList.remove("is-in");
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
