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
- **fonts** are one of Fraunces, Newsreader, Lora, Source Serif 4, Literata,
  EB Garamond, Crimson Pro, Inter, Hanken Grotesk, Nunito Sans, Space Grotesk
  or Poppins. The portal serves them itself.
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


## Where the files are served from

A deployment answers for a website's hosts in one of two ways, chosen by
configuration; the practice sees no difference.

**Through the frontend** (the default). The frontend looks the host up, fetches
the live version's files through the backend, and answers. Nothing to set up
beyond `PRACTICE_SITE_BUCKET`.

**From object storage.** Set `PRACTICE_SITE_LIVE_BUCKET` to a world-readable
bucket on the same storage provider, and `PRACTICE_DOMAIN_SITE_BACKEND` to the
load balancer's backend bucket that fronts it: a name when it is in the serving
project, or `projects/<project>/global/backendBuckets/<name>` when the public
bucket, and so its backend bucket, live in a project of their own. The domain reconciler then
copies each published version into that bucket (at its own path, with a copy of
every `index.html` at its folder's key, `Cache-Control` of a minute for pages
and an hour for the rest, and a `robots.txt` disallowing everything when the
practice asked not to be listed) and routes the website's custom hosts straight
at it: the primary host to a path matcher of the practice's own, which rewrites
every path into the live version's folder; the others to a redirect to the
primary. A visitor's request never reaches the deployment. A publish, a roll
back or a search-listing change asks the reconciler for a run, which copies the
new version before it moves the hosts, and empties the cache in front of them
after. A copy that fails leaves the hosts on what they served before.

Headers the frontend adds on its path (the content-security policy, the
`noindex` on a hidden website) are not added by a bucket; set the policy as a
custom response header on the backend bucket, and rely on `robots.txt` for the
listing. The hosted address a deployment may give every practice
(`<slug>.<hosted domain>`) is served the same way: once the website is
published, the reconciler gives that host its own rule to the website's folder,
with no certificate work since the hosted wildcard covers it, follows a change
of slug, and takes the rule down with the website. An exact host rule takes
precedence over the hosted wildcard rule, which keeps answering, through the
frontend, for practices that have not published.
