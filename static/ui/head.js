// Runs in <head> before first paint: content that fades in on scroll is hidden only when
// JavaScript runs, as apple.com does with html.no-js / html.js. Without it, everything shows.
document.documentElement.classList.add("js");

// Liquid Glass lens (docs/APPLE_DESIGN_PLAN.md section 13, LG27-LG29): real refraction needs an
// SVG filter in backdrop-filter, which only Chromium draws. Safari and Firefox may still parse the
// declaration and then draw no backdrop at all, so @supports can't be trusted: the lens is turned
// on by this class only for Chromium, and never when the visitor asks for less transparency,
// more contrast or less motion. Everyone else keeps the frosted blur.
(function () {
  try {
    const brands = (navigator.userAgentData && navigator.userAgentData.brands) || [];
    const chromium = brands.some((b) => /Chromium/.test(b.brand));
    const parses = window.CSS && CSS.supports("backdrop-filter", "url(#lg)");
    const q = (m) => window.matchMedia && window.matchMedia(m).matches;
    const asksLess = q("(prefers-reduced-transparency: reduce)") || q("(prefers-contrast: more)") || q("(forced-colors: active)");
    if (chromium && parses && !asksLess) document.documentElement.classList.add("lg-refract");
  } catch (_) { /* no lens: the frosted blur stays */ }
})();
