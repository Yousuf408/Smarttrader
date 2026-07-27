---
name: css-driven-auto-hide
description: For time-bound visual elements (toasts, banners, splash), prefer CSS chained animations + animationend over a JS setTimeout. JS timers are at the mercy of the event loop; CSS animations are compositor-driven and immune.
---

# Use CSS animations + `animationend` for time-bound UI elements

## Rule
When a UI element must auto-hide after a fixed delay (toast, banner, splash, hold-to-confirm ring), drive the timing with a CSS `animation` chain and listen for `animationend` to flip visibility. Keep a JS `setTimeout` as belt-and-braces only — it should FAIL OPEN (call the same hide function) on the same delay so both paths converge.

```css
.toast.show {
  display: block;
  animation:
    in  0.22s ease both 0s,
    out 0.30s ease both 4s;
}
@keyframes out { to { opacity: 0; transform: translateY(20px); } }
```

```js
t.addEventListener('animationend', e =>
  e.animationName === 'out' && hideToast(t));
// Belt-and-braces
t._hideTimer = setTimeout(() => hideToast(t), 4000);
```

## Why
- JS `setTimeout(fn, 5000)` is queued on the event loop. If a heavy synchronous task is running (e.g. `renderStrategyData()` rebuilding a 119-row table, the auto-refresh ticker in screener), the timer fires whenever the loop drains, not at 5s. Browsers can also clamp timers on background tabs or after `requestAnimationFrame` storms.
- CSS animations live on the compositor thread, so the fade-out happens at exactly the wall-clock boundary even while synchronous JS hogs the main thread.
- `animationend` resolves to the actual finish time (close enough for visual UX), and the JS fallback timer rescues edge cases where animations are disabled by OS settings or the page was backgrounded during the animation window.

## How to apply
- Snack/toast lib rewrites and any "appeared-N-seconds-ago" UI.
- Force a reflow (`void t.offsetWidth`) before re-adding the show class so back-to-back toasts re-trigger the entry animation instead of being merged into one longer animation chain.
- Resolve `document.getElementById(id)` inside `show/`hide` rather than caching a reference at module load — captured refs can silently point at detached nodes after a re-render.
- Pin the off-state to `display: none; pointer-events: none`, not just `opacity: 0`. A hovered invisible toast that takes pointer events blocks clicks underneath.

## Triggers that favor this pattern over JS setTimeout
- A heavy DOM re-render precedes or follows the toast fire (e.g. table refresh with hundreds of rows).
- The timer is paired with a visible animation (fade, slide) — the animation already encodes the duration.
- The same UI element can fire several times in rapid succession — reuse the CSS chain instead of stacking/clearing JS timers.
