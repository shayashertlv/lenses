---
name: Lenses
description: The optical counter of a chain optician, rendered as a category signage system.
colors:
  paper: "#ffffff"
  card: "#f2f1ec"
  card-sunk: "#e7e5dd"
  ink: "#0e1310"
  ink-2: "#4a534d"
  ink-3: "#626b65"
  field: "#0f5132"
  field-deep: "#0a3a24"
  signal: "#ff5a1f"
  signal-ink: "#3a0f00"
  danger: "#a11212"
  rule: "rgba(14, 19, 16, .16)"
  rule-strong: "rgba(14, 19, 16, .30)"
  on-field: "#ffffff"
  on-field-2: "rgba(255, 255, 255, .78)"
typography:
  display:
    fontFamily: "Archivo, system-ui, -apple-system, Segoe UI, sans-serif"
    fontSize: "clamp(2.75rem, 0.6rem + 9.5vw, 6.5rem)"
    fontWeight: 800
    lineHeight: 0.92
    letterSpacing: "-0.02em"
    fontVariation: "font-stretch: 62%"
  headline:
    fontFamily: "Archivo, system-ui, -apple-system, Segoe UI, sans-serif"
    fontSize: "clamp(2.25rem, 1.2rem + 5vw, 4rem)"
    fontWeight: 800
    lineHeight: 0.92
    letterSpacing: "-0.02em"
    fontVariation: "font-stretch: 66%"
  title:
    fontFamily: "Archivo, system-ui, -apple-system, Segoe UI, sans-serif"
    fontSize: "1.375rem"
    fontWeight: 800
    lineHeight: 1
    letterSpacing: "-0.02em"
    fontVariation: "font-stretch: 72%"
  brand:
    fontFamily: "Archivo, system-ui, -apple-system, Segoe UI, sans-serif"
    fontSize: "1.0625rem"
    fontWeight: 800
    lineHeight: 1
    letterSpacing: "normal"
    fontVariation: "font-stretch: 70%"
  body:
    fontFamily: "Archivo, system-ui, -apple-system, Segoe UI, sans-serif"
    fontSize: "0.9375rem"
    fontWeight: 400
    lineHeight: 1.6
    letterSpacing: "normal"
  label:
    fontFamily: "Archivo, system-ui, -apple-system, Segoe UI, sans-serif"
    fontSize: "0.6875rem"
    fontWeight: 500
    lineHeight: 1.35
    letterSpacing: "0.08em"
  figure:
    fontFamily: "Archivo, system-ui, -apple-system, Segoe UI, sans-serif"
    fontSize: "0.8125rem"
    fontWeight: 700
    lineHeight: 1.35
    letterSpacing: "normal"
    fontFeature: "font-variant-numeric: tabular-nums"
rounded:
  tag: "3px"
  ctl: "5px"
  panel: "8px"
  pill: "999px"
spacing:
  s-1: "0.25rem"
  s-2: "0.5rem"
  s-3: "0.75rem"
  s-4: "1rem"
  s-5: "1.5rem"
  s-6: "2.5rem"
  s-7: "4rem"
components:
  signage-band:
    backgroundColor: "{colors.field}"
    textColor: "{colors.on-field}"
    typography: "{typography.label}"
    rounded: "0px"
    padding: "6px 0.75rem"
  shelf-edge-label:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink}"
    rounded: "{rounded.ctl}"
    padding: "0.75rem"
  shelf-edge-label-hover:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink}"
  shape-tag:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink}"
    typography: "{typography.label}"
    rounded: "{rounded.ctl}"
    padding: "0.5rem 6px"
    height: "66px"
  shape-tag-selected:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.paper}"
  shape-tag-dimmed:
    backgroundColor: "{colors.card}"
    textColor: "{colors.ink-3}"
  filter-pill:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink}"
    typography: "{typography.label}"
    rounded: "{rounded.ctl}"
    padding: "0 1rem"
    height: "44px"
  filter-pill-active:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.paper}"
  price-block:
    backgroundColor: "{colors.signal}"
    textColor: "{colors.signal-ink}"
    typography: "{typography.figure}"
    rounded: "{rounded.tag}"
    padding: "2px 7px"
  action-primary:
    backgroundColor: "{colors.signal}"
    textColor: "{colors.signal-ink}"
    rounded: "{rounded.ctl}"
    padding: "0 1rem"
    height: "52px"
  action-primary-disabled:
    backgroundColor: "{colors.card-sunk}"
    textColor: "{colors.ink-2}"
  action-outline:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink}"
    rounded: "{rounded.ctl}"
    padding: "0 0.75rem"
    height: "44px"
  action-outline-hover:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.paper}"
  counter-ticket:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink}"
    typography: "{typography.label}"
    rounded: "{rounded.ctl}"
    padding: "0.5rem"
    width: "220px"
  counter-ticket-done:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink}"
  dispensing-docket:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink}"
    rounded: "{rounded.panel}"
    padding: "1rem"
    width: "min(900px, 100%)"
  result-card:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink}"
    rounded: "{rounded.ctl}"
    padding: "0.75rem"
  colour-swatch:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink}"
    typography: "{typography.label}"
    rounded: "{rounded.ctl}"
    padding: "0.5rem 4px"
    size: "30px"
---

# Design System: Lenses

## Overview

**Creative North Star: "The Optical Counter"**

This is the front-of-house of a chain optician rendered in code: a shelf run of frames where every product wears a printed shelf-edge label, category signage bands file the stock into groups, and the AI works behind a dispensing counter that fills in a docket while you wait. Nothing here borrows the conventions of a shop. There is no cart, no wishlist, no checkout; the surfaces exist so an evaluator can watch four capabilities run against a real 147-frame catalogue and picture them on their own inventory.

The register is precise and well lit rather than promotional. Retail signage carries a standing risk of reading as discount, and this catalogue runs to four-figure ILS prices, so the system holds the line with generous white space, hairline rules instead of chrome, one committed colour and exactly one accent under strict rationing. Density is high where the work is (94 facet controls filed under signage bands, spec rows on every label) and deliberately loose where the pitch is (one enormous condensed headline beside a single dispensing tray).

Light, not dark, and forced rather than chosen: the recorded use scene is a fluorescent-lit retail floor and a laptop screen-shared in a meeting room, and the site gets browsed on projectors. The build refuses the category default it replaced — near-black ground, one violet accent, Inter, four identical icon-plus-heading cards as the page structure. The four capabilities are filed as a numbered directory with rules and per-row durations. Not cards.

**Key Characteristics:**
- One saturated colour at field scale (`--field`, G-15 green) carrying every band, header and progress fill
- A single restricted accent (`--signal`) that appears on price blocks and one primary action per surface
- Archivo variable, self-hosted, with the condensed width axis doing all display work
- Drawn SVG frame silhouettes as the only pictorial vocabulary; no icon fonts, no Unicode glyphs
- Flat surfaces on hairlines; shadow reserved for things that genuinely float
- Mechanical motion: exponential ease-out, nothing bounces

## Colors

One saturated colour at field scale on a warm-white ground, with a single rationed accent and a five-step neutral ramp; every muted value carries a measured contrast ratio.

### Primary
- **Optical Green** (`--field`): THE committed colour, and not a palette pick. It is G-15 green, a real value in the product's own `lenses.color` taxonomy and the lens of Ray-Ban, which is 50 of the 147 catalogue products. It fills every category signage band, topbar, docket header, ticket header, results bar and progress fill, and it is the hover/active border on drop zones. Measured 9.4:1 on paper.
- **Field Deep** (`--field-deep`): the pressed/hover state for controls that already sit on a field ground, such as the back button in the results bar.
- **On Field** (`--on-field`) and **On Field Muted** (`--on-field-2`): the reversed pair for type on a field ground, measured 9.4:1 and 6.4:1 respectively. Secondary text on a band always takes the muted one; never a grey.

### Secondary
- **Signal Orange** (`--signal`): the accent, rationed. Price blocks and one primary action per surface, plus the ready state of a counter ticket. Never decorative, never a second brand colour.
- **Signal Ink** (`--signal-ink`): the only text colour permitted on signal. It is the darkest shade of the same family, measured 5.6:1; black and grey are both wrong on this ground, and the nearby `#4a1400` was measured at 4.37 and rejected.

### Tertiary
- **Alert Red** (`--danger`): failure only — the error mark on the landing error card and the header, border and bar of a failed counter ticket. It appears nowhere else and is not part of the expressive palette.

### Neutral
- **Paper** (`--paper`): the page ground and the resting fill of every control and card.
- **Shelf Card Stock** (`--card`): photo wells, docket side panels, score chips, the profile panel, and the hover fill of an unselected tag. The warm off-white that makes the shelf read as printed stock rather than screen.
- **Sunk Stock** (`--card-sunk`): the empty channel of a progress bar and the disabled fill of a primary action.
- **Ink** (`--ink`): all body and display type, the selected-tag fill, and the 2px rule under a section header.
- **Ink 2** (`--ink-2`): supporting paragraphs and secondary rows. Measured 7.2:1 on paper.
- **Ink 3** (`--ink-3`): the muted floor — spec rows, docket labels, score keys, out-of-range tags. Measured 5.5:1 on paper and 4.8:1 on card. It is tuned against card rather than paper deliberately, because most muted text sits on a card and a value that clears 4.5:1 on white can still fail on the slightly darker stock.
- **Rule** and **Rule Strong** (`--rule`, `--rule-strong`): hairlines and dashed drop-zone borders. Depth in this system is a line, not a shadow.

### Named Rules
**The One Field Rule.** There is exactly one saturated colour and it operates at field scale, not accent scale. Any new grouping header, panel header or progress fill is `--field`. Introducing a second hue to distinguish a section is forbidden; distinguish with type and rule weight instead.

**The Signal Ration Rule.** `--signal` is permitted on price blocks and on one primary action per surface, plus the ready state of a counter ticket. It is never a hover colour, never a border for emphasis, never a chart or badge tint. Audit test: count the signal-filled elements in a viewport; a price row plus one button is the ceiling.

**The Measured Muted Rule.** No muted text colour enters the system without a measured ratio against the ground it actually sits on. `--ink-3` is the floor at 4.8:1 on card, and an out-of-range control drops to `--ink-3` with a dashed border rather than to a low opacity — opacity is how the previous design failed contrast at every viewport.

## Typography

**Display Font:** Archivo (variable, weight 400–800, width 62–125%), with `system-ui`, `-apple-system`, `Segoe UI` fallback
**Body Font:** Archivo, same file, at normal width
**Label/Mono Font:** none distinct; figures use Archivo with `font-variant-numeric: tabular-nums`

**Character:** One grotesque doing every job, with the width axis carrying all the drama. Display is Archivo condensed hard and set in uppercase so a headline reads as a printed shelf header; body relaxes to normal width and a comfortable 1.6 line-height so paragraphs stay readable in a meeting-room screen share. The font is self-hosted as two variable woff2 subsets and licensed OFL — the product ships as a container on the client's own infrastructure and must not depend on a font CDN. The latin-ext subset is not optional: U+20AA, the shekel sign, lives there and every price on the site uses it.

### Hierarchy
- **Display** (800, `clamp(2.75rem, 0.6rem + 9.5vw, 6.5rem)`, line-height 0.92, tracking -0.02em, uppercase, width 62%): one per site, the landing pitch headline. Balanced with `text-wrap: balance`.
- **Headline** (800, `clamp(2.25rem, 1.2rem + 5vw, 4rem)`, line-height 0.92, uppercase, width 66%): the storefront hero.
- **Title** (800, 22px, line-height 1, uppercase, width 72–74%): wordmarks in a signage band, section headers above the shelf run, modal headings.
- **Brand** (800, 17px, line-height 1, uppercase, width 70%): the brand line on a shelf-edge label, a result card and the pulled-frame panel. This is the shelf-label voice and it is what makes a card read as printed stock.
- **Body** (400, 15px, line-height 1.6, measure 68ch): supporting paragraphs, docket notes, values in a profile row.
- **Label** (500, 11px, tracking 0.08em, uppercase): signage bands, spec rows, tag captions, ticket rows, durations, every small key. This is the workhorse of the system and it appears on every surface.
- **Figure** (700, tabular-nums): prices, counts, percentages, shelf indices, score values.

### Named Rules
**The Eleven Pixel Floor Rule.** 11px is the functional floor and nothing readable goes below it. The contract test allows exactly one distinct font size under 12px across all CSS and templates; a second one fails the build.

**The Condensed Voice Rule.** Display weight is always 800 on the condensed width axis (62–78%) and always uppercase. A large heading at normal width is not part of this system, and neither is a light or regular weight above 22px.

**The Tracking Floor Rule.** Display tracking is -0.02em and the floor is -0.04em. Uppercase labels track open at 0.08em. Nothing sits between those two treatments.

## Layout

Every wide surface runs a centred container capped at 1320px with `--s-5` (24px) gutters, dropping to `--s-4` (16px) below 900. Spacing is a 4px grid exposed as seven steps (4, 8, 12, 16, 24, 40, 64px); the large two, `--s-6` and `--s-7`, do the vertical section rhythm and the rest do component internals. Body text is capped at 68ch.

The landing counter is a two-column grid — pitch beside a fixed 320px dispensing tray — collapsing to one column at 900. The capabilities directory is a four-column row grid (index, name, description, duration) that drops its description column at 900 rather than reflowing into cards. The shelf run is a fixed 3-up grid that becomes 2-up at 900 and stays 2-up at 560 with a tighter gutter; facet tiles and swatches auto-fill from an 88px/76px minimum, tightening to 74px/66px below 560. The dispensing docket is body-plus-260px-side and collapses to one column at 900.

**The Two Breakpoint Rule.** There are exactly two width breakpoints in the shipped CSS, 560 and 900, plus one landscape-phone height query, `(max-height: 500px) and (max-width: 900px)`. Custom properties cannot be used inside `@media` preludes, so the set cannot live in the token layer and is enforced instead by `UI/tests/test_css_contract.py`, which also fails any breakpoint appearing in only one stylesheet. (The token-layer comment still describes a third value at 1280; no stylesheet uses it. 1320px is a container cap, not a breakpoint.) Before adding a third width, change the test and say why.

**The Forty-Four Rule.** Every interactive target — pill, chip, radio label, back button, dismiss, directory row link — carries `min-height: 44px`, and primary actions go to 48–52px.

## Elevation & Depth

The system is flat. Surfaces sit on the page ground and separate by a 1px hairline (`--rule`), a tonal shift to card stock, or a 2px ink rule under a section header. Cards, tags, pills, tiles, docket panels and result cards all carry zero shadow at rest; hover raises the border from `--rule` to `--rule-strong` or to `--ink`, and the shelf-edge label adds a 2px lift in transform only. Shadow is reserved for the three things that genuinely float above the page: the dispensing docket, the counter tickets in the dock, and modal or slide-over panels.

### Shadow Vocabulary
- **Panel** (`box-shadow: 0 8px 24px rgba(14, 19, 16, .14)`): the dispensing docket and each counter ticket in the dock — objects resting on top of the page but still in the room.
- **Modal** (`box-shadow: 0 18px 48px rgba(14, 19, 16, .28)`): overlay dialogs, the photo-tips box, and the free-search slide-over panel.

### Named Rules
**The Offset-and-Blur Rule.** Every shadow carries an offset AND a blur. A zero-offset coloured halo is decoration, not depth, and does not belong in this system.

**The Hairline-First Rule.** Depth is a line before it is a shadow. If a surface is not floating over other content, it separates with `--rule`, a card-stock fill, or a 2px ink rule — never with a shadow.

## Shapes

Signage is squared and only controls soften. The radius ramp is deliberately tiny: 3px on printed marks (price blocks, badges, swatch dots, thumbnails, score chips), 5px on interactive controls (tiles, pills, buttons, cards, drop zones), 8px on floating panels (docket, modal, tips box), and a full pill radius reserved for genuinely circular elements — the selected-tag signal dot, colour dots, size dots. Category signage bands are square-bottomed, rounding only their top two corners when they cap a group.

The recurring geometry is the drawn frame silhouette: `FRAME_GLYPHS` in `common.js` holds one SVG path set per silhouette in the tag taxonomy — round, oval, square, rectangular, flat-top, teardrop/aviator/pilot, cat-eye, butterfly, browline, hexagonal/geometric, irregular, shield, curved-wrap/wrap, wayfarer — drawn on a 120×46 viewBox with a 2.6 stroke in `currentColor`, falling back to the rectangular plate for an unknown shape. It is real vector geometry and it does double duty: the mark inside a facet tag, and the photo-well placeholder that shows through a shelf-edge label until the product image decodes, so an unloaded label reads as filed stock rather than a broken image. Borders elsewhere are functional signals: dashed means a drop zone or an out-of-range control, solid ink means selected.

**The Drawn-Geometry Rule.** Every mark in this system is drawn SVG or a CSS-shaped box. No icon fonts, no icon packages, no Unicode glyph pressed into service as an arrow, check or bullet — the shipped code contains zero rendered Unicode glyphs, and that is a rule, not a coincidence.

## Components

### Buttons
- **Shape:** softly squared (5px), full-width in panels and auto in rows.
- **Primary:** signal fill with signal ink, 700 weight, 48–52px tall. One per surface. Hover is `filter: brightness(1.06)` — no colour change, no lift. Disabled drops to sunk stock with ink-2 text.
- **Outline (the default action):** paper fill, 1px ink border, ink text; hover inverts to ink fill with paper text. This is the try-on button, the recolour button, the shop button and both storefront AI entry points, and it is the correct choice for anything that is not the one primary action.
- **On-field:** transparent with a 1px `--on-field-2` border and reversed text; hover fills `--field-deep`.
- **Focus:** every focusable element takes a 2px `--field` outline at 2px offset plus a 3px translucent field ring.

### Chips
- **Filter pill:** paper fill, hairline border, 11px uppercase label at 0.08em, 44px tall, with a live count set at 62% opacity beside the name. Hover strengthens the border and fills card stock.
- **State:** active fills `--ink` with paper text. The strip scrolls horizontally with the scrollbar hidden.

### Cards / Containers
- **Corner Style:** 5px on shelf and result cards, 8px on floating panels.
- **Background:** paper body over a card-stock photo well, divided by a hairline.
- **Shadow Strategy:** none at rest; see Elevation.
- **Border:** 1px `--rule`, going to `--ink` on hover and on the primary result.
- **Internal Padding:** `--s-3` (12px) in a card body, dropping to `--s-2` below 560.

### Inputs / Fields
- **Style:** the facet layer hides its native inputs (1px, opacity 0, kept in flow so `:focus-visible` still reaches the label) and styles the label as the control. Selection is expressed with `:has()`, which is order-independent — the adjacent-sibling form it replaced broke selection feedback silently across 161 controls whenever markup was restructured. Two markup shapes exist (input nested in the label for tiles and swatches, input as a sibling inside a wrapping span for chips and radios) and both are covered by explicit `:has()` selectors so neither can lose its selected state.
- **Drop zone:** card-stock fill with a 1.5px dashed `--rule-strong` border and a field-coloured drawn icon; hover turns the border field and the fill paper; a chosen photo turns the border solid field.
- **Focus:** the tag's outer wrapper takes the field outline via `:has(input:focus-visible)`.
- **Out of range:** card fill, dashed hairline, `--ink-3` text (4.8:1), and the mark at 0.55 opacity. Never a low-opacity whole control.

### Navigation
Navigation is a category signage band: a sticky full-bleed `--field` bar holding an uppercase condensed wordmark at 22px, a reversed uppercase back link at label size in `--on-field-2`, and a right-aligned note that drops out below 560. There is no menu, no dropdown and no second nav level anywhere in the build.

### Shelf-Edge Label
The signature component and the unit of the shelf run. A 3:2 photo well on card stock holds the drawn frame silhouette in `--rule-strong` with the product photo layered over it, so the label reads as filed stock before the image decodes; a gender badge sits top-left in paper with a hairline. The body sets the brand in 17px condensed signage caps at weight 800, then the model name (with the brand prefix stripped, since the shelf label already says it once), then a stack of uppercase spec-row tags from the real tag schema in `--ink-3`. The foot pushes to the bottom of the card and pairs a small tabular shelf index with the signal price block and a try-on action. Hover lifts the border to ink, translates the card up 2px and scales the photo to 1.04.

### Category Signage Band
The grouping device across the whole system: a `--field` bar with the group name left and a live count right in `--on-field-2` tabular figures, 11px uppercase at 0.08em, rounded only on its top corners. It heads facet groups, docket panels, counter tickets and results bars alike. A section that needs a header gets this, or a 2px ink rule under a 22px condensed title — nothing else.

### Shape Tag
The facet unit. Paper fill, hairline, 66px minimum height (60px below 560), a drawn silhouette above an 11px uppercase caption that is allowed to break mid-word rather than push past its box. Selected inverts to an ink fill with the silhouette reversed and a single 13px `--signal` dot pinned to the top-right corner — the one place signal appears outside a price or a primary action, and it is a selection state, not decoration.

### Colour Swatch
A shape tag whose mark is a 30px squared dot (3px radius, hairline border). When selected on an ink ground the dot's border switches to translucent white so the swatch stays legible against the fill. The swatch colours themselves are product data, still carried inline in the templates.

### Counter Ticket
The dock unit, stacked bottom-right, 220px wide (190px when more than one is docked, 200px in a full-width scrolling rail below 560). A `--field` header carries the ticket number and job kind, the body pairs a thumbnail or drawn silhouette with a two-line caption, and a 5px progress bar over sunk stock fills with `--field` — animated as `transform: scaleX()` from a left origin, never as a width. The foot holds a status line and percent in uppercase tabular figures. Ready flips the header, border and bar to `--signal`; failure flips them to `--danger`. Tapped while still working, it nudges ±3px and returns — mechanical, not a bounce.

### Dispensing Docket
The wait surface: a 900px panel with a `--field` header, a key/value rule stack for the face profile on the left, notes arriving one at a time (fading up 6px over `--d-slow`), and the chosen frame pulled out into a card-stock panel on the right with its own brand line, spec row and signal price block, with render progress beneath. Every reveal is opacity plus a small translate; nothing slides in from off-screen.

### Result Card
The shelf-edge language applied to a rendered try-on. Same paper card on a hairline with a card-stock image well; a `--field` label chip marks the result, the brand line takes the 17px condensed voice, and score chips sit on card stock with `--ink-3` keys over tabular values. The primary result spans the full row, takes an ink border, and caps its image at 58vh (46vh below 560, 70vh in landscape phone). While a try-on is pending the well shows an uppercase wait line over a 72px indeterminate bar whose 45% segment translates across — again transform, never width.

## Do's and Don'ts

### Do:
- **Do** use `--field` for every band, panel header, progress fill and grouping device. It is the system's one saturated colour and it operates at field scale.
- **Do** keep `--signal` to price blocks and one primary action per surface, plus the ready state of a counter ticket.
- **Do** set display type in Archivo at weight 800 on the condensed width axis (62–78%), uppercase, tracking -0.02em.
- **Do** measure any new muted colour against the ground it will actually sit on, not against white, and keep it at or above `--ink-3`.
- **Do** draw new marks as SVG geometry, and extend `FRAME_GLYPHS` when a new frame silhouette enters the tag taxonomy.
- **Do** animate progress and reveals with `transform` (`scaleX`, `translate`) on `cubic-bezier(.16, 1, .30, 1)`.
- **Do** give every interactive target `min-height: 44px` and let focus take the field outline plus ring.
- **Do** express selection with `:has()`, covering both markup shapes, so a DOM restructure cannot silently kill selected state.
- **Do** keep all colour, size, spacing, radius and breakpoint values in `tokens.css`; the contract test allows zero raw hex outside it.

### Don't:
- **Don't** introduce a second accent hue, or use `--signal` as a hover, border-emphasis or badge tint.
- **Don't** add a third width breakpoint. The set is 560 and 900 plus one landscape-phone height query, and `test_css_contract.py` fails a breakpoint that appears in only one stylesheet.
- **Don't** use an icon font, an icon package, or a Unicode character as a glyph. The build renders zero of them.
- **Don't** use bounce or elastic easing anywhere, and don't retune the `RingTimer` pacing curve in `common.js` — it is a protected region, tuned against the real pipeline timings.
- **Don't** transition `width` or `height`; the contract test fails any layout-property transition.
- **Don't** render text below 11px, or add a second sub-12px size.
- **Don't** dim an unavailable control with opacity. Drop it to `--ink-3` with a dashed border so it still clears 4.5:1.
- **Don't** put a shadow on a resting surface, and don't use a zero-offset coloured halo as depth.
- **Don't** rebuild a page as a row of identical icon-plus-heading cards. The capabilities are a numbered directory with rules; that refusal is structural.
- **Don't** add promotional furniture — burst shapes, strike-through pricing, countdowns, ribbons. The register is the precise optical department, not the promo aisle.

## Known Gaps

Recorded so the next pass does not mistake them for system:

- `--sh-lift` and `--field-hi` are declared in `tokens.css` and referenced by no stylesheet. They are not part of the shipped system; either use them or delete them.
- `--wd-display`, `--wd-tight`, `--wd-normal` and `--lh-snug` are likewise declared but unreferenced. Every `font-stretch` in the build is a literal percentage (62, 66, 68, 70, 72, 74, 78, 84), which is where the width axis currently lives.
- `.sg-display`, `.sg-label`, `.sg-figure` and `.sg-price` in `tokens.css` are real utilities but only partially adopted; most surfaces restate the same declarations locally. The shared classes are the intended path.
- Facet geometry (clip-path silhouettes, indicator sizing) and swatch colours are still inline `style` attributes in the templates — 186 of them, budgeted and ratcheting down. They are product data that has not been extracted into the token layer. This is where the system currently stops.
- The `1280` breakpoint named in the `tokens.css` header comment does not exist in any stylesheet. The comment is stale; the code is the record.

## Product colour vocabulary

These are **product data, not design tokens**. The catalogue names sixteen lens
tints and eighteen frame colours; the swatch that represents each one has to be
that colour, so they live inline on the control and are deliberately outside the
`tokens.css` palette. A future surface inherits the *mechanism* (a swatch shows
its own colour) and never these values as UI colours.

The tint values currently shipped, harvested from the templates:

```
  #00838f  #16a34a  #1a1a1a  #1a237e  #1a2a3a  #1a73e8
  #20a040  #2563eb  #2a2a2a  #2a3a4a  #2e7d32  #3a3a3a
  #3f51b5  #404040  #4060c0  #4caf50  #4dd0e1  #4fc3f7
  #587848  #607840  #616161  #654321  #688058  #6a1b9a
  #6b4226  #6b7280  #709060  #808080  #8090a0  #8090a8
  #8b4513  #8d4e2a  #8d6e3f  #9098a8  #9888b0  #98c090
  #9ca3af  #9e9e9e  #a040a0  #a0724a  #a0a8b0  #a8a8a8
  #a8c090  #a8c8a0  #ab47bc  #b0b0b0  #b0d0a0  #b71c1c
  #b8c6d4  #c04010  #c06020  #c09050  #c0c0c0  #c2185b
  #c47860  #c4963c  #c8d0d8  #c9a84c  #cd7f50  #cd853f
  #d0c8e0  #d0d0d0  #d0d8e0  #d2691e  #d4a0a0  #d4a574
  #dc2626  #dce6ef  #e08020  #e0e0e0  #e0e8f0  #e8b4a0
  #e8b4b4  #e8c860  #e8d48b  #eab308  #ec4899  #ef5350
  #f06292  #f8f8f8  #f9a825  #f9a8d4  #ffee58
```

They are recorded here so the detector can tell a product swatch from an
undocumented UI colour. Extracting them into `data-color` rules driven by a
named map remains the open work noted in Known Gaps.
