# Practice website files

For whoever builds a practice's website to upload in Settings > Website.

A website is a zip of a static folder: HTML, CSS, scripts, images and fonts,
with `index.html` at the top. Everything in it is served as uploaded. One file
is also read by the portal: `theme.json`, at the top beside `index.html`.

## theme.json

`theme.json` lets the practice's client portal, on the practice's own domain,
look like the website. It is a short list of values, never code:

```json
{
  "version": 1,
  "colors": {
    "accent": "#24504c",
    "accentText": "#ffffff",
    "background": "#fbf8f3",
    "surface": "#ffffff",
    "text": "#1d2726",
    "mutedText": "#55605e"
  },
  "fonts": { "heading": "Fraunces", "body": "Inter" },
  "radius": "lg",
  "header": {
    "wordmark": "Riverside Counseling",
    "subtitle": "Individual and couples therapy",
    "links": [
      { "label": "Services", "href": "/#services" },
      { "label": "About", "href": "/about" }
    ],
    "cta": { "label": "Schedule a visit", "href": "/#schedule" }
  }
}
```

- **colors** are hex, `#rgb` or `#rrggbb`. Text colors must keep a 4.5:1
  contrast with what they are drawn on; one that doesn't is left out.
- **fonts** are one of Fraunces, Newsreader, Inter, Hanken Grotesk, Nunito
  Sans, Space Grotesk or Poppins. The portal serves them itself.
- **radius** is `none`, `sm`, `md` or `lg`.
- **header** is described below.

Every key is optional. Each value is checked on its own: one that can't be
used is left out, the rest still apply, and Settings > Website lists what was
left out and why as soon as the zip is uploaded. Keys the portal doesn't know
are ignored. A missing or broken `theme.json` never stops a website from
publishing.

The theme belongs to the version it was published with, so rolling the
website back brings back that version's theme.

## Portal header

The `header` block makes the top of the portal match the top of the website:

| Key | What it is | Limit |
|---|---|---|
| `wordmark` | The practice's name as the website shows it. Links to the website's home page. | 60 characters |
| `subtitle` | A short line under the wordmark. | 80 characters |
| `links` | Up to 5 links, each `{"label": ..., "href": ...}`, shown in a row of their own. | label 24 characters |
| `cta` | One link shown as a button, `{"label": ..., "href": ...}`. | label 24 characters |

The practice's name as it is set in Pablo stays the page's heading. When the
wordmark is the same name, it is shown once; when it differs, the wordmark is
what visitors see and the practice's name is what screen readers announce.

### Text

Text is counted after spaces are tidied. It is left out when it:

- contains a control character, an invisible or direction-changing character,
  or a line break;
- mixes alphabets within one word (such as a Cyrillic letter among Latin
  ones), the way look-alike addresses are made;
- is empty, or longer than its limit.

A label can't be "Sign in", "Log in", "Login" or "Sign out", which the portal
uses itself, or the name of the software the portal runs on; the wordmark
can't start with that name. A self-hosted deployment sets that name with
`PORTAL_HEADER_BRAND_NAMES` (comma-separated, `Pablo` by default).

### Links

An `href` is one of:

- a page on the website, starting with a single `/`: `/about`, `/#services`,
  `/services?for=couples#fees`. It opens on the website's live address.
- a full `https://` address on one of the practice's own working hosts,
  website or portal, with no port and no user name.

Anything else is left out: `http:`, `mailto:`, `tel:`, `javascript:` and other
schemes, other sites, addresses with `//` at the start or `..` in the path,
and links that only start with `#` (write `/#services`, not `#services`). Two
links to the same page keep the first. A link missing its label or `href` is
left out whole, and so is the call to action unless both its parts pass.

A full address is checked again whenever the portal is shown: if the practice
stops using that host, the link disappears.

### A header suggested from index.html

When `theme.json` has no `header` block, Settings > Website suggests one from
the site's `index.html` when the zip is uploaded. The practice can accept it or
edit it first. Either way it is written into the draft's `theme.json` and
checked like any header there, and the portal shows it once the draft is
published. A suggestion nobody accepts never reaches the portal.

The suggestion is a guess from the page's first `<header>` (or its first
`<nav>`): the link to the home page is the name, a link styled as a button is
the call to action, and the other links are the links. Links that start with
`#` or are relative to the page become paths, so `#services` becomes
`/#services`. Links to the practice's own portal are left out.

To make the suggestion exact rather than guessed, mark the header:

```html
<header data-pablo-header>
  <a href="/">
    <strong data-pablo="brand">Riverside Counseling</strong>
    <span data-pablo="subtitle">Individual and couples therapy</span>
  </a>
  <nav data-pablo="nav">
    <a href="#services">Services</a>
    <a href="/about">About</a>
  </nav>
  <a data-pablo="cta" class="button" href="#schedule">Schedule a visit</a>
</header>
```

- `data-pablo-header` marks the element holding the rest.
- `data-pablo="brand"` is the name; its text is used.
- `data-pablo="subtitle"` is the line under it.
- `data-pablo="nav"` on a container makes every link inside it a link; on a
  single `<a>`, just that one.
- `data-pablo="cta"` is the call to action: an `<a>`, or an element with one
  inside.

The markers change nothing about how the website looks. Only `index.html` is
read, and only when it is 256 KB or smaller.
