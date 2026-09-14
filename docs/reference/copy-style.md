# Writing copy for Pablo

For anyone — person or AI — writing text a clinician will read: product UI,
empty states, error messages, onboarding, marketing pages.

## The principle

**Keep the safety model. Simplify what the reader sees.**

Pablo touches how a clinician gets paid, so the reasoning behind a screen is
usually careful and load-bearing. The mistake is making every sentence carry
that reasoning.

This was written after a review of the billing setup flow, whose verdict was:

> The weakness was not the core reasoning; it was trying to make every sentence
> carry that reasoning. The result was long, defensive copy that exposed billing
> machinery too early and sometimes contradicted its own rules.

The reasoning belongs in the code comments and the design doc. The screen gets
the conclusion.

## Who is reading

A solo mental-health clinician, often not technical, doing administration
between sessions. They are mid-task and want to finish. They did not come to
learn how claims work.

Many work through a service like Headway, Alma, or Rula. **They may intend to
stay there forever, and that is a good outcome.** Copy must never imply
otherwise.

## Rules

### Say less

- One sentence beats three. Cut any sentence that only restates the last one.
- **Do not recite what will not happen.** Listing the things a screen does not
  do introduces machinery the reader had not thought about and makes a safe
  default sound dangerous.
- Do not add a nudge beside a working button. It creates doubt without helping
  anyone decide.
- Reassurance that does not name a consequence is noise. "Nothing starts until
  you're ready" sounds like it says something and does not — *what* starts?

### Say only what is true

- **Never claim readiness the system has not checked.** Reaching the end of a
  flow is not the same as being finished. Ask the data.
- Never promise an artifact that cannot be produced — a superbill needs an NPI.
- Never say Pablo will submit, file, or send something it is not yet authorized
  to submit, file, or send.
- Never imply completing setup means insurance can be billed. Being contracted
  and being able to file a claim are different states.
- Prefer *may* to *will* when the outcome depends on someone else's system.

### Respect what the reader already has

- Never push someone to leave a service they use.
- Never imply Pablo holds, retrieved, or can separate data living in another
  system.
- Recommend without requiring. An EIN and Type 2 NPI help some practices; a
  sole proprietor billing under their own NPI is a supported structure.

### Words

- Explain a term before using its abbreviation. "An electronic remittance
  advice, or ERA, explains how a payer handled your claims."
- Avoid internal vocabulary: *route*, *transaction type*, *aggregation level*,
  *provider record*, *payer enrollment* where "asking a payer" would do.
- Prefer the reader's phrasing. They say "I'm in-network with Aetna", not "I
  have contracts in my own practice".
- American spelling. `enroll`, not `enrol`.
- **No gendered pronouns** for a clinician or a client. Second person usually
  solves it; otherwise *they*.
- Name services as examples of where someone's clients come from — never
  comparatively, never with leaving implied.

## Worked examples

Each of these shipped, was reviewed, and changed.

| Instead of | Write |
|---|---|
| Nothing here changes how the service that pays you works today. We won't enroll you with payers, redirect any payment, or ask for anything only an independent biller needs. | This won't change how the service handles your current clients or payments. |
| Pick everything that applies. This just decides what we set up first — you can add the rest any time. | Choose all that apply. You can change this later. |
| Pablo will help you apply and keep track of it. It takes months, nothing starts until you're ready, and none of it changes how you're paid in the meantime. | Pablo can help prepare and track your applications while you keep using the service. |
| Clients pay me themselves | Clients pay me directly |
| Insurance I bill myself | I bill insurance myself |
| Anything receiving this payer's remittances today stops receiving them — an old clearinghouse, a billing service, or a platform handling your insurance side. | This may stop the reports from going to the service, clearinghouse, or biller that receives them today. |
| We can't tell whether {payer} routes remittances by NPI or by tax ID. | We can't tell whether {payer} will make this change only for this NPI or for every clinician using this tax ID. |
| Your record is yours, whatever the platform holds | *(cut — implies we can retrieve it)* |
| We'll put your applications in and chase them | Pablo prepares and tracks applications. Pablo will not submit an application until you authorize it. |
| You're set up to bill *(unconditionally)* | You're set up to bill **when the profile is actually complete**; otherwise: Your billing setup is underway |

## Before you call copy done

- Would a clinician between sessions understand it on one read?
- Does every claim about readiness reflect data, not arrival at a screen?
- Does it name machinery nobody asked about?
- Could a sentence be cut without losing meaning?
- Does it push anyone toward leaving something that works for them?
- Is any promise made that needs an authorization we do not have?

## Where the reasoning goes instead

Not on the screen. Put it in:

- the code comment above the string, where the next person editing it will look
- the design doc, when it settles a product question
- the PR body, when it explains a change

That is not a consolation prize. Copy that has to justify itself on screen is
usually copy that has not decided what it is for.
