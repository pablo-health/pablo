# Churn & activation monitoring

Working doc for the THERAPY-chur epic (usage pulse + CoS rescue loop). The
rescue-loop design lives in
[`replyable-reengagement-design.md`](replyable-reengagement-design.md); this
doc holds the operational records and (via THERAPY-chur.8) the measurement
definitions those flows depend on.

## 6. Outreach infrastructure

### 6.1 Sender authentication — pablo.health

**Status: PASS — cleared for outreach use.** Recorded 2026-07-29
(THERAPY-chur.11, decided by Kurt on pablo-saas#1287; DNS evidence pulled live
by the eng head the same day):

| Mechanism | Record | Result |
|---|---|---|
| SPF | `v=spf1 include:_spf.google.com include:spf.brevo.com -all` | Google-sent mail authorized; hard-fail for everything else |
| DKIM (Google) | key live at `google._domainkey.pablo.health` | signing active for Workspace sends |
| DMARC | `v=DMARC1; p=quarantine; pct=100; sp=quarantine; aspf=r` | enforced at 100%, relaxed SPF alignment |
| DMARC monitoring | `rua=mailto:…@dmarc.postmarkapp.com` | aggregate reports flow to Postmark's DMARC monitoring — alignment is continuously watched, not one-shot verified |

Net: sends from the `pablo@pablo.health` alias via Google Workspace pass
SPF, DKIM and DMARC, and ongoing alignment regressions would surface in the
Postmark DMARC reports. This standing monitoring is stronger evidence than
the one-off test-send header inspection the original acceptance asked for.

_Sections for conversion definitions (14-day window, per-flag-kind funnel)
land with THERAPY-chur.8._
