---
name: ui-screenshot-verify
description: After modifying any file under web/components/, web/app/, web/lib/, or web/styles/, run the appropriate Playwright screenshot script to visually verify the change in a real browser. Type-check + build passing does NOT mean the page renders correctly — past regressions have shipped with green CI. Required before declaring a UI task done.
---

# ui-screenshot-verify

## When this skill triggers

Trigger after any Edit/Write that touches:

- `web/components/**`
- `web/app/**` (Next.js routes)
- `web/lib/**` (if it affects rendering — fetcher, formatters, hooks)
- `web/styles/**`, `tailwind.config.*`, `web/app/globals.css`

## Why this matters

`tsc --noEmit` and `next build` both succeed on broken layouts. They do not catch:

- Treemap tile aspect-ratio collapse (the recurring d3-hierarchy vs. Recharts trap)
- Hover popups rendered off-viewport / clipped by overflow
- Light vs. dark theme contrast bugs
- Mobile drawer / responsive padding regressions
- Content shift when async data resolves

Only a real browser render catches these. **Build green ≠ UI correct.**

## Which script to run

There are exactly three permanent regression scripts. Pick by what changed:

| Change scope | Script |
|---|---|
| Sectors heatmap / treemap layout, hover popup | `node web/scripts/screenshot.mjs` |
| Anything in a regular page (light + dark coverage) | `node web/scripts/screenshot-all.mjs` |
| Mobile drawer, responsive padding, viewport-dependent layout | `node web/scripts/screenshot-mobile.mjs` |

If the change spans categories, run all three.

## How to run

1. Make sure `next dev` is up. If not: `./launch.bat` (or check `status.bat`).
   - **DO NOT** run `npm run build` while `next dev` is alive — they share `web/.next/` and will corrupt each other. See CLAUDE.md §1.
2. From repo root:
   ```
   node web/scripts/screenshot.mjs        # or screenshot-all / screenshot-mobile
   ```
3. Read the resulting PNG with the Read tool to visually confirm the change.
4. If a hover/popup assertion fails inside the script, fix the component — do not loosen the assertion.

## What NOT to do

- **Do not** create new one-shot scripts like `screenshot-s2-foo.mjs` or `screenshot-validation.mjs`. Either fold the new check into one of the three permanent scripts, or write a temp script and **delete it after**. Sprint-tag scripts have accumulated as garbage in the past.
- **Do not** declare a UI task done based on type-check + build. Always run the screenshot.
- **Do not** run screenshots against a stale `next dev` if you suspect HMR has drifted — restart it via `./restart.bat`.

## When Playwright cannot be used

If the environment can't launch Chromium (e.g. headless container without deps), say so explicitly to the user — **do not** silently claim success based on build output. Offer to:
- describe the expected visual change in detail, or
- ask the user to run the screenshot script themselves and paste the output.
