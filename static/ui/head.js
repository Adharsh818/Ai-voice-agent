// Runs in <head> before first paint: content that fades in on scroll is hidden only when
// JavaScript runs, as apple.com does with html.no-js / html.js. Without it, everything shows.
document.documentElement.classList.add("js");
