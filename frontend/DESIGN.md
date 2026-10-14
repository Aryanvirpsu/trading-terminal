# Terminal UI — design spec

Flat dark, minimal, dense. The job of the interface is to make a decision readable, not to be looked at.

## Principles
- **No effects.** No glow, blur, gradients, glass or animated backgrounds. Surfaces are solid; separation comes from 1px borders.
- **One accent** (blue `#4c8dff`) for navigation and selection. Green / red / amber are *only* for meaning: P&L, verdicts, freshness.
- **Numbers are monospace** (`JetBrains Mono`, tabular figures). Everything else is `Inter`.
- **Fixed, aligned layouts.** Every page is a stack of cards on a 12px/16px rhythm; no draggable widgets.
- **Say what is stale or missing.** Loading, error and unavailable states are explicit, never a blank or an old number left looking current.
- **Read-only.** Nothing in the UI can place, preview or queue an order.

## Tokens (`src/styles/tokens.css`)
| Role | Value |
|---|---|
| Page / sidebar / card / raised | `#0b0d12` / `#0e1117` / `#12151c` / `#171b24` |
| Border / strong border | `#222834` / `#2d3442` |
| Text / dim / faint | `#e6e9f0` / `#9aa3b6` / `#6f7a90` (both secondary tones clear WCAG AA on the card surface) |
| Accent | `#4c8dff` |
| Up / down / warn | `#2fd37f` / `#f0566a` / `#e7b341` |
| Radius | 8px cards, 5–7px controls |

## Structure
Sidebar (Overview · Stock · Scanner · Markets · Portfolio · Strategies · Testing · System) + top bar (stock search, market status, account view) + routed page.
Stock pages are deep-linkable: `#/stock/PLTR/<tab>`; experiments: `#/testing/<folder>/<tab>`.

## Motion (tier L1 — refined, calm)
Motion exists to orient, never to decorate. Everything is opacity/transform only, ≤ 220 ms, and disabled by `prefers-reduced-motion`.
- **Page entrance:** 220 ms fade + 6 px rise when the section changes. **Tab switch:** 160 ms fade. The active-tab underline grows in.
- **Numbers are never tweened.** A figure renders exactly as formatted; only a genuine change gets a brief green/red highlight (live price, equity).
- **Loading:** skeletons for content; a 2 px top progress line only for user-triggered requests slower than 350 ms (background polling is silent).
- **Feedback:** 1 px press on buttons, visible keyboard focus everywhere, clickable table rows are keyboard-operable (Tab, Enter / Space).
- **No** blur, glow, parallax, count-ups, custom cursors or background effects.

## Behaviour
- Stock tabs are sticky; switching tabs keeps the scroll position; changing page or symbol returns to the top.
- The Ledger switch (Local | Cloud) is global and always paired with an amber banner when the cloud mirror is shown.
