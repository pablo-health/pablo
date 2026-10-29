# Practice domain reconciler: serving a practice's hosts

Module: `backend/app/services/practice_domain_reconciler.py`

Serving a practice's hosts: bringing what the load balancer serves into line
with ``platform.practice_domains``.

One sweep visits every host, practice by practice, and takes it one step
further each time — every step is safe to repeat, so a sweep that stops half
way loses nothing:

1. Look the practice's records up (``PracticeDomainService.check``, the same
   check Settings > Domains runs), which also records a domain's ownership
   when its ``_pablo-verify`` TXT is found, notes when a host's records are
   all in place, and reports a host that has stayed not active too long since
   (audited here as ``practice_domain_stuck``). Such a host is checked as
   before: it goes active by itself if what held it up clears.
2. Per host, ask for its DNS authorisation (storing the ``_acme-challenge``
   value, so the practice is shown the record at once) and its certificate.
   ``pending`` becomes ``verifying``: the records are known and the job is
   waiting on the practice's DNS and on the certificate. What it is waiting
   for is kept in ``last_error``.
3. The issuer tries to authorise a certificate as soon as it is requested —
   usually before the practice has added the ``_acme-challenge`` record — and
   after that failed attempt it may not look again for a long time. So when a
   certificate is stuck behind a failed attempt and this sweep's check finds
   that record in place, the certificate is deleted and requested again
   against the same authorisation (the record stays right), at most once per
   :data:`REISSUE_INTERVAL` per host.
4. When the domain's ownership is confirmed, the host's own record points
   here, and the certificate is active: add the certificate-map entry and the
   URL-map host rule, and the host becomes ``active``.
5. ``error`` is kept for what the practice or whoever runs the deployment has
   to act on: a request the cloud refused, or a CAA record that forbids the
   certificate. A record not added yet is waiting, not an error.
6. An ``active`` host is checked again every sweep. If its record stops
   pointing here, or its certificate stops being active, it goes to ``error``
   with the reason — and its host rule stays: taking a working site down over
   a DNS blip would be worse than the blip. No answer in time changes nothing.
   It comes back to ``active`` by itself once both are right again.
7. A ``removing`` host has its host rule, map entry, certificate and DNS
   authorisation removed, in that order, and then its row is deleted; with the
   last host under a domain, the domain's row (and its email identity) go too.
   The hosts of a practice that is no longer active (``is_active`` false, or
   offboarded with ``deleted_at`` set, or gone) are made ``removing`` first.

8. A purpose (portal, website) with an ``active`` host and no primary gets
   one, since links are built on the primary and would otherwise never name
   the practice's own domain.
9. Where the deployment serves published websites from object storage (a
   :class:`~app.sites.mirror.SiteMirror` is given), a website whose live
   version, or whose search setting, is not what the load balancer serves is
   copied into the public bucket first; then each of its hosts going or
   staying ``active`` is routed at that version's folder -- the primary host
   itself, the others to a redirect to the primary -- the cache in front is
   emptied for them, and what is served is recorded on the website. A copy
   that fails leaves every host where it was: on what was served before, or
   on the deployment's own matcher if nothing was. A website never published
   stays on the deployment's own matcher too. The website's hosted address
   (``<slug>.<hosted domain>``, where the deployment has one) is routed at the
   same version with its own host rule -- no certificate work, the hosted
   wildcard covers it -- and the rule follows a slug change and goes with the
   website; a practice with a hosted address and no domain of its own is
   visited for this alone. After the practice's hosts are done, so that
   two going active in one sweep are chosen between rather than raced: the
   bare domain first, then a name that is not a ``www.`` alias, then the
   oldest. The write only claims a primary where there is none, so a primary
   the practice chose is never replaced; and a sweep that finds active hosts
   with no primary — added before this rule, or left by removing the primary
   — fills it the same way.

Then, when the deployment registered one, each domain whose ownership is
confirmed has its email sending identity ensured (see
``practice_domain_email``).

Every status change is audited with the system as the actor, scoped to the
practice. Status writes are guarded on the status the sweep read, so a removal
the practice makes meanwhile is never overwritten. Cloud calls happen outside
any database transaction.

No PHI: hostnames, practice ids and setup state.
