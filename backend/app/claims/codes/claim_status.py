# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""835 claim status codes (CLP02).

Source: ASC X12N 005010X221A1 Health Care Claim Payment/Advice (835),
Technical Report Type 3, loop 2100 CLP segment, element CLP02 "Claim Status
Code" (X12 data element 1029). These ten values live inside the 835 guide
rather than in an external code list, so there is no x12.org page to cite.
The guide itself is licensed; the values and wording below are the subset it
permits, as reproduced verbatim in public payer companion guides (the CMS
Medicare 835 companion guide among them). Retrieved: 2026-09-06.

The code tells a practice what the payer did with the whole claim before it
looks at any line-level adjustment: processed as primary, secondary or
tertiary; denied; reversed; or forwarded to another payer.
"""

from __future__ import annotations

CLAIM_STATUS: dict[str, str] = {
    "1": "Processed as Primary",
    "2": "Processed as Secondary",
    "3": "Processed as Tertiary",
    "4": "Denied",
    "19": "Processed as Primary, Forwarded to Additional Payer(s)",
    "20": "Processed as Secondary, Forwarded to Additional Payer(s)",
    "21": "Processed as Tertiary, Forwarded to Additional Payer(s)",
    "22": "Reversal of Previous Payment",
    "23": "Not Our Claim, Forwarded to Additional Payer(s)",
    "25": "Predetermination Pricing Only - No Payment",
}
