# Pablo Brand & Surface System

> Working design reference for the Pablo app. Source of truth for tokens is
> the marketing site (https://pablo.health); this doc reconciles the app to it
> and records the decisions we've made. Pair with `frontend/app/globals.css`.

## North star

**Warm paper, crisp ink. Honey for action, brown for authority, a blush of humanity.**

If a design choice doesn't serve "warm paper / crisp ink," it's wrong. This one
line is meant to win arguments.

## Anti-goals

- **No EHR blue or grey.** Ever. That cold-clinical look is the thing we're
  beating.
- **No muddy warmth.** Warm ≠ everything-is-beige. Warmth that coats every
  surface reads soft *and* dated. Warmth must stay an accent.
- **No color free-for-all.** One accent leads (honey). The rest support. If
  three colors shout at once, none of them land.

## Canonical tokens (from pablo.health)

```
--white:          #FFFFFF
--warm-cream:     #FDF6EC
--honey:          #E8A849     --honey-light:  #F5D08E
--sage:           #7A9E7E     --sage-light:   #B8D4BA
--sky:            #89B4C8
--deep-brown:     #3D2B1F
--soft-brown:     #8B6F4E
--blush:          #E8B4A2     ← soft coral; MISSING from the app today
--text-primary:   #2C1810
--text-secondary: #6B5344
```

Fonts: **Fraunces** (serif) for display/headings, **DM Sans** for body — already
matches the app. No type change needed.

The app and marketing already share fonts and the core palette. The divergence
is **how the colors are spent**, not which colors exist.

## The core idea: a layered surface system

Marketing is white-dominant (≈8× white sections, 7× deep-brown bands, warmth as
*accent* sections). The app does the opposite — the entire workspace is warm
cream, so surfaces don't separate and dense data loses its edge. Fix = invert it:

| Layer | Color | Role |
|-------|-------|------|
| Canvas (L0) | warm near-white `#FEFCF8` | the all-day background — softer than stark white, kind to the eyes, still crisp |
| Cards / data (L1) | pure white `#FFFFFF` | where real work lives; lifts cleanly off L0 |
| Tinted blocks (L2) | warm-cream `#FDF6EC` | *intentional* warmth — empty states, callouts, grouped/secondary info |
| Dark anchor | deep-brown `#3D2B1F` | chrome accents + contrast bands (the "professional weight") |

Cream stops being the air you breathe and becomes a deliberate accent. This is
most of the "crisp but warm" win, and it's exactly what made the editorial
calendar feel right vs. the cream-everywhere classic view.

## Color roles (resolves the dual-primary)

The app currently defines *two* primaries (a honey `--color-primary-*` scale and
a deep-brown shadcn `--primary`). Give each a clear job:

- **Honey** `#E8A849` → *the* primary action + active/selected state. The energy.
- **Deep-brown** `#3D2B1F` → ink + dark surfaces. **Not a button color.**
- **Sage** → positive / success / "completed" / secondary actions.
- **Sky** → informational accents, subtle gradients.
- **Blush** `#E8B4A2` → soft/human moments **only**: avatars, empty-state art,
  gentle highlights, mascot contexts. Never on clinical data or status badges
  (coral on a "no-show" would misread).

Echo marketing's generous whitespace through **card padding, section spacing,
and line-height** — roominess as comfort — not empty screen where data belongs.

## Decisions (made — "that's the way it is")

1. **Chrome:** Keep the sidebar (medical apps have them; removing it is too
   jarring). **Default = light/warm sidebar** with deep-brown *accents* (wordmark
   spine, footer/account zone), not a fully dark sidebar. The bold deep-brown
   sidebar ships as a **selectable theme** ("Cozy") so it can be tried live
   without committing.
2. **Canvas:** warm near-white `#FEFCF8` (not stark white, not cream).
3. **Blush:** garnish only — a rare human touch, never a third data color.

## Theme switcher (so you can *see* the options)

The app already drives everything through CSS custom properties and ships a dark
theme, so the infra exists. Make each look a **named theme = a set of variable
overrides** scoped to `[data-theme="..."]` on `<html>`:

- A switcher sets `data-theme` and persists it (localStorage for instant feel +
  a user preference in the DB for cross-device).
- `NEXT_PUBLIC_DEFAULT_THEME` env var sets the default when a user has no
  preference (lets you change the house style without a code change).
- **Keep it to 2–3 themes** — each is a maintenance surface.

Starter set to try:

| Theme | Canvas | Chrome | Vibe |
|-------|--------|--------|------|
| **Warm Paper** (default) | `#FEFCF8` | light sidebar + brown accents | crisp, warm, professional |
| **Cozy** | more cream + blush | deep-brown sidebar | softer, bolder, "fireside" |
| **Classic Cream** | today's `#FDF6EC` everywhere | current | for side-by-side comparison during transition |

This is the fastest way to learn what *you* like: ship the variants, flip
between them on a real screen, keep the winner, delete the rest.

**Easter egg — "Boring EHR™".** A deliberately joyless cold-blue/grey theme,
tucked at the bottom of the picker with a wink. It's not a real option — it's
the anti-goal made selectable, so anyone toggling it *feels* exactly what Pablo
refuses to be. On-brand precisely because it's a joke.

## Pilot surface: the login page

`app/login/page.tsx` is the best place to apply the system first — one page, no
dense data, and it's the first impression. Today it's a white `AuthCard` on a
soft honey→cream→sage gradient (`AuthCard.tsx:12`): pleasant but generic.

Upgrade it into a real brand moment: a **split layout** — a deep-brown panel on
one side (wordmark, one-line value prop, room for the Pablo mascot) beside the
white form card. That single screen demonstrates the whole system (brown
authority + white crispness + honey CTA) and is low-risk to iterate on.

## Design principles for non-designers (the cheat sheet)

Transferable rules that explain *why* the above works:

1. **60-30-10.** Roughly 60% dominant surface (near-white), 30% secondary
   (white cards + warm accents), 10% bold accent (honey/brown). "Cream
   everywhere" fails because it's 100/0/0 — no ratio, no hierarchy.
2. **Surfaces must separate.** Adjacent areas need a visible step in
   color/elevation/border, or the eye can't parse structure. Cream-on-cream
   has no step.
3. **Warmth is seasoning, not the meal.** Apply it to accents and moments, not
   the whole canvas.
4. **One accent leads.** Let honey carry "do this." Don't let sage, sky, blush,
   and honey all compete for attention on the same screen.
5. **Whitespace reads as quality.** Generous padding and line-height feel
   premium; cramming feels cheap. Roominess is free polish.
6. **Hierarchy comes from size and weight, not more fonts.** Two fonts is
   plenty; vary scale/weight to signal importance.
7. **Consistency beats cleverness.** A repeated, predictable system feels more
   designed than one-off flourishes.
