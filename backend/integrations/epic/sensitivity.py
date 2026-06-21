# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Detect specially-protected (DS4P / 42 CFR Part 2) FHIR resources.

Some records carry stronger-than-HIPAA protection — substance-use-disorder
data (42 CFR Part 2), psychiatry, HIV, sexual/domestic-violence — and must
not be persisted into a general chart without the right consent. FHIR
expresses this with security labels on ``meta.security`` (the HL7 Data
Segmentation for Privacy model). Imports exclude these by default; opting
in is a deliberate, consent-gated decision.
"""

from integrations.epic.mappers import JsonDict

# HL7 Confidentiality codes that mark a resource as restricted.
_RESTRICTED_CONFIDENTIALITY = frozenset({"R", "V"})

# HL7 ActCode information-sensitivity codes for specially-protected
# categories. Not exhaustive — the common behavioral-health-relevant set.
_SENSITIVE_ACT_CODES = frozenset(
    {
        "ETH",  # substance abuse information
        "ALC",  # alcohol use
        "PSY",  # psychiatry / mental health
        "HIV",  # HIV/AIDS
        "SDV",  # sexual and domestic violence
        "SCA",  # substance abuse — clinical
        "42CFRPart2",  # explicit Part 2 marking some systems apply
    }
)


def is_restricted(resource: JsonDict) -> bool:
    """True if a resource carries a restricted or sensitive security label."""
    for label in resource.get("meta", {}).get("security", []):
        code = label.get("code")
        if code in _RESTRICTED_CONFIDENTIALITY or code in _SENSITIVE_ACT_CODES:
            return True
    return False
