# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The clinician's credential record: her facts, and her status on each panel.

Five modules, each owning one thing the tables alone cannot enforce:

* :mod:`government_ids` — the only reader and writer of the encrypted columns,
  and the thing that audits a decryption.
* :mod:`employment` — derives the gaps in a work history rather than storing
  them.
* :mod:`disclosures` — keeps an answer pinned to the wording it answered.
* :mod:`participation` — moves a panel's status and writes its event row in the
  same transaction, so the history can never disagree with the status.
* :mod:`clocks` — proposes compliance-item due dates from record dates, and
  only proposes.
* :mod:`rates` — what a payer contracted to pay, and the variance against what
  it actually allowed.
"""
