# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Claim Adjustment Reason Codes (CARC), as published by X12.

Source: X12 External Code Lists, "Claim Adjustment Reason Codes",
https://x12.org/codes/claim-adjustment-reason-codes
List version: 11/1/2025 (the "updated" date shown on the page).
Retrieved: 2026-09-06.

Every code X12 publishes is here, including deactivated ones: a remittance
posted years ago still carries the code it was issued with, and a lookup that
returns nothing for a retired code is less useful than the description it had.
:data:`DEACTIVATED_CARC` names the retired ones so a caller can tell.

Generated from the public list; do not hand-edit descriptions.
"""

CARC: dict[str, str] = {
    "1": "Deductible Amount",
    "2": "Coinsurance Amount",
    "3": "Co-payment Amount",
    "4": (
        "The procedure code is inconsistent with the modifier used. Usage: Refer to the 835 "
        "Healthcare Policy Identification Segment (loop 2110 Service Payment Information REF), if "
        "present."
    ),
    "5": (
        "The procedure code/type of bill is inconsistent with the place of service. Usage: Refer to"
        " the 835 Healthcare Policy Identification Segment (loop 2110 Service Payment Information "
        "REF), if present."
    ),
    "6": (
        "The procedure/revenue code is inconsistent with the patient's age. Usage: Refer to the 835"
        " Healthcare Policy Identification Segment (loop 2110 Service Payment Information REF), if "
        "present."
    ),
    "7": (
        "The procedure/revenue code is inconsistent with the patient's gender. Usage: Refer to the "
        "835 Healthcare Policy Identification Segment (loop 2110 Service Payment Information REF), "
        "if present."
    ),
    "8": (
        "The procedure code is inconsistent with the provider type/specialty (taxonomy). Usage: "
        "Refer to the 835 Healthcare Policy Identification Segment (loop 2110 Service Payment "
        "Information REF), if present."
    ),
    "9": (
        "The diagnosis is inconsistent with the patient's age. Usage: Refer to the 835 Healthcare "
        "Policy Identification Segment (loop 2110 Service Payment Information REF), if present."
    ),
    "10": (
        "The diagnosis is inconsistent with the patient's gender. Usage: Refer to the 835 "
        "Healthcare Policy Identification Segment (loop 2110 Service Payment Information REF), if "
        "present."
    ),
    "11": (
        "The diagnosis is inconsistent with the procedure. Usage: Refer to the 835 Healthcare "
        "Policy Identification Segment (loop 2110 Service Payment Information REF), if present."
    ),
    "12": (
        "The diagnosis is inconsistent with the provider type. Usage: Refer to the 835 Healthcare "
        "Policy Identification Segment (loop 2110 Service Payment Information REF), if present."
    ),
    "13": "The date of death precedes the date of service.",
    "14": "The date of birth follows the date of service.",
    "15": (
        "The authorization number is missing, invalid, or does not apply to the billed services or "
        "provider."
    ),
    "16": (
        "Claim/service lacks information or has submission/billing error(s). Usage: Do not use this"
        " code for claims attachment(s)/other documentation. At least one Remark Code must be "
        "provided (may be comprised of either the NCPDP Reject Reason Code, or Remittance Advice "
        "Remark Code that is not an ALERT.) Refer to the 835 Healthcare Policy Identification "
        "Segment (loop 2110 Service Payment Information REF), if present."
    ),
    "17": (
        "Requested information was not provided or was insufficient/incomplete. At least one Remark"
        " Code must be provided (may be comprised of either the Remittance Advice Remark Code or "
        "NCPDP Reject Reason Code.)"
    ),
    "18": (
        "Exact duplicate claim/service (Use only with Group Code OA except where state workers' "
        "compensation regulations requires CO)"
    ),
    "19": (
        "This is a work-related injury/illness and thus the liability of the Worker's Compensation "
        "Carrier."
    ),
    "20": "This injury/illness is covered by the liability carrier.",
    "21": "This injury/illness is the liability of the no-fault carrier.",
    "22": "This care may be covered by another payer per coordination of benefits.",
    "23": (
        "The impact of prior payer(s) adjudication including payments and/or adjustments. (Use only"
        " with Group Code OA)"
    ),
    "24": "Charges are covered under a capitation agreement/managed care plan.",
    "25": "Payment denied. Your Stop loss deductible has not been met.",
    "26": "Expenses incurred prior to coverage.",
    "27": "Expenses incurred after coverage terminated.",
    "28": "Coverage not in effect at the time the service was provided.",
    "29": "The time limit for filing has expired.",
    "30": (
        "Payment adjusted because the patient has not met the required eligibility, spend down, "
        "waiting, or residency requirements."
    ),
    "31": "Patient cannot be identified as our insured.",
    "32": "Our records indicate the patient is not an eligible dependent.",
    "33": "Insured has no dependent coverage.",
    "34": "Insured has no coverage for newborns.",
    "35": "Lifetime benefit maximum has been reached.",
    "36": "Balance does not exceed co-payment amount.",
    "37": "Balance does not exceed deductible.",
    "38": "Services not provided or authorized by designated (network/primary care) providers.",
    "39": "Services denied at the time authorization/pre-certification was requested.",
    "40": (
        "Charges do not meet qualifications for emergent/urgent care. Usage: Refer to the 835 "
        "Healthcare Policy Identification Segment (loop 2110 Service Payment Information REF), if "
        "present."
    ),
    "41": "Discount agreed to in Preferred Provider contract.",
    "42": "Charges exceed our fee schedule or maximum allowable amount. (Use CARC 45)",
    "43": "Gramm-Rudman reduction.",
    "44": "Prompt-pay discount.",
    "45": (
        "Charge exceeds fee schedule/maximum allowable or contracted/legislated fee arrangement. "
        "Usage: This adjustment amount cannot equal the total service or claim charge amount; and "
        "must not duplicate provider adjustment amounts (payments and contractual reductions) that "
        "have resulted from prior payer(s) adjudication. (Use only with Group Codes PR or CO "
        "depending upon liability)"
    ),
    "46": "This (these) service(s) is (are) not covered.",
    "47": "This (these) diagnosis(es) is (are) not covered, missing, or are invalid.",
    "48": "This (these) procedure(s) is (are) not covered.",
    "49": (
        "This is a non-covered service because it is a routine/preventive exam or a "
        "diagnostic/screening procedure done in conjunction with a routine/preventive exam. Usage: "
        "Refer to the 835 Healthcare Policy Identification Segment (loop 2110 Service Payment "
        "Information REF), if present."
    ),
    "50": (
        "These are non-covered services because this is not deemed a 'medical necessity' by the "
        "payer. Usage: Refer to the 835 Healthcare Policy Identification Segment (loop 2110 Service"
        " Payment Information REF), if present."
    ),
    "51": (
        "These are non-covered services because this is a pre-existing condition. Usage: Refer to "
        "the 835 Healthcare Policy Identification Segment (loop 2110 Service Payment Information "
        "REF), if present."
    ),
    "52": (
        "The referring/prescribing/rendering provider is not eligible to "
        "refer/prescribe/order/perform the service billed."
    ),
    "53": "Services by an immediate relative or a member of the same household are not covered.",
    "54": (
        "Multiple physicians/assistants are not covered in this case. Usage: Refer to the 835 "
        "Healthcare Policy Identification Segment (loop 2110 Service Payment Information REF), if "
        "present."
    ),
    "55": (
        "Procedure/treatment/drug is deemed experimental/investigational by the payer. Usage: Refer"
        " to the 835 Healthcare Policy Identification Segment (loop 2110 Service Payment "
        "Information REF), if present."
    ),
    "56": (
        "Procedure/treatment has not been deemed 'proven to be effective' by the payer. Usage: "
        "Refer to the 835 Healthcare Policy Identification Segment (loop 2110 Service Payment "
        "Information REF), if present."
    ),
    "57": (
        "Payment denied/reduced because the payer deems the information submitted does not support "
        "this level of service, this many services, this length of service, this dosage, or this "
        "day's supply."
    ),
    "58": (
        "Treatment was deemed by the payer to have been rendered in an inappropriate or invalid "
        "place of service. Usage: Refer to the 835 Healthcare Policy Identification Segment (loop "
        "2110 Service Payment Information REF), if present."
    ),
    "59": (
        "Processed based on multiple or concurrent procedure rules. (For example multiple surgery "
        "or diagnostic imaging, concurrent anesthesia.) Usage: Refer to the 835 Healthcare Policy "
        "Identification Segment (loop 2110 Service Payment Information REF), if present."
    ),
    "60": (
        "Charges for outpatient services are not covered when performed within a period of time "
        "prior to or after inpatient services."
    ),
    "61": "Adjusted for failure to obtain second surgical opinion",
    "62": "Payment denied/reduced for absence of, or exceeded, pre-certification/authorization.",
    "63": "Correction to a prior claim.",
    "64": "Denial reversed per Medical Review.",
    "65": "Procedure code was incorrect. This payment reflects the correct code.",
    "66": "Blood Deductible.",
    "67": "Lifetime reserve days. (Handled in QTY, QTY01=LA)",
    "68": "DRG weight. (Handled in CLP12)",
    "69": "Day outlier amount.",
    "70": "Cost outlier - Adjustment to compensate for additional costs.",
    "71": "Primary Payer amount.",
    "72": "Coinsurance day. (Handled in QTY, QTY01=CD)",
    "73": "Administrative days.",
    "74": "Indirect Medical Education Adjustment.",
    "75": "Direct Medical Education Adjustment.",
    "76": "Disproportionate Share Adjustment.",
    "77": "Covered days. (Handled in QTY, QTY01=CA)",
    "78": "Non-Covered days/Room charge adjustment.",
    "79": "Cost Report days. (Handled in MIA15)",
    "80": "Outlier days. (Handled in QTY, QTY01=OU)",
    "81": "Discharges.",
    "82": "PIP days.",
    "83": "Total visits.",
    "84": "Capital Adjustment. (Handled in MIA)",
    "85": "Patient Interest Adjustment (Use Only Group code PR)",
    "86": "Statutory Adjustment.",
    "87": "Transfer amount.",
    "88": (
        "Adjustment amount represents collection against receivable created in prior overpayment."
    ),
    "89": "Professional fees removed from charges.",
    "90": "Ingredient cost adjustment. Usage: To be used for pharmaceuticals only.",
    "91": "Dispensing fee adjustment.",
    "92": "Claim Paid in full.",
    "93": "No Claim level Adjustments.",
    "94": "Processed in Excess of charges.",
    "95": "Plan procedures not followed.",
    "96": (
        "Non-covered charge(s). At least one Remark Code must be provided (may be comprised of "
        "either the NCPDP Reject Reason Code, or Remittance Advice Remark Code that is not an "
        "ALERT.) Usage: Refer to the 835 Healthcare Policy Identification Segment (loop 2110 "
        "Service Payment Information REF), if present."
    ),
    "97": (
        "The benefit for this service is included in the payment/allowance for another "
        "service/procedure that has already been adjudicated. Usage: Refer to the 835 Healthcare "
        "Policy Identification Segment (loop 2110 Service Payment Information REF), if present."
    ),
    "98": "The hospital must file the Medicare claim for this inpatient non-physician service.",
    "99": "Medicare Secondary Payer Adjustment Amount.",
    "100": "Payment made to patient/insured/responsible party.",
    "101": (
        "Predetermination: anticipated payment upon completion of services or claim adjudication."
    ),
    "102": "Major Medical Adjustment.",
    "103": "Provider promotional discount (e.g., Senior citizen discount).",
    "104": "Managed care withholding.",
    "105": "Tax withholding.",
    "106": "Patient payment option/election not in effect.",
    "107": (
        "The related or qualifying claim/service was not identified on this claim. Usage: Refer to "
        "the 835 Healthcare Policy Identification Segment (loop 2110 Service Payment Information "
        "REF), if present."
    ),
    "108": (
        "Rent/purchase guidelines were not met. Usage: Refer to the 835 Healthcare Policy "
        "Identification Segment (loop 2110 Service Payment Information REF), if present."
    ),
    "109": (
        "Claim/service not covered by this payer/contractor. You must send the claim/service to the"
        " correct payer/contractor."
    ),
    "110": "Billing date predates service date.",
    "111": "Not covered unless the provider accepts assignment.",
    "112": "Service not furnished directly to the patient and/or not documented.",
    "113": (
        "Payment denied because service/procedure was provided outside the United States or as a "
        "result of war."
    ),
    "114": "Procedure/product not approved by the Food and Drug Administration.",
    "115": "Procedure postponed, canceled, or delayed.",
    "116": (
        "The advance indemnification notice signed by the patient did not comply with requirements."
    ),
    "117": (
        "Transportation is only covered to the closest facility that can provide the necessary "
        "care."
    ),
    "118": "ESRD network support adjustment.",
    "119": "Benefit maximum for this time period or occurrence has been reached.",
    "120": "Patient is covered by a managed care plan.",
    "121": "Indemnification adjustment - compensation for outstanding member responsibility.",
    "122": "Psychiatric reduction.",
    "123": "Payer refund due to overpayment.",
    "124": "Payer refund amount - not our patient.",
    "125": (
        "Submission/billing error(s). At least one Remark Code must be provided (may be comprised "
        "of either the NCPDP Reject Reason Code, or Remittance Advice Remark Code that is not an "
        "ALERT.)"
    ),
    "126": "Deductible -- Major Medical",
    "127": "Coinsurance -- Major Medical",
    "128": "Newborn's services are covered in the mother's Allowance.",
    "129": (
        "Prior processing information appears incorrect. At least one Remark Code must be provided "
        "(may be comprised of either the NCPDP Reject Reason Code, or Remittance Advice Remark Code"
        " that is not an ALERT.)"
    ),
    "130": "Claim submission fee.",
    "131": "Claim specific negotiated discount.",
    "132": "Prearranged demonstration project adjustment.",
    "133": (
        "The disposition of this service line is pending further review. (Use only with Group Code "
        "OA). Usage: Use of this code requires a reversal and correction when the service line is "
        "finalized (use only in Loop 2110 CAS segment of the 835 or Loop 2430 of the 837)."
    ),
    "134": "Technical fees removed from charges.",
    "135": "Interim bills cannot be processed.",
    "136": "Failure to follow prior payer's coverage rules. (Use only with Group Code OA)",
    "137": "Regulatory Surcharges, Assessments, Allowances or Health Related Taxes.",
    "138": "Appeal procedures not followed or time limits not met.",
    "139": (
        "Contracted funding agreement - Subscriber is employed by the provider of services. Use "
        "only with Group Code CO."
    ),
    "140": "Patient/Insured health identification number and name do not match.",
    "141": "Claim spans eligible and ineligible periods of coverage.",
    "142": "Monthly Medicaid patient liability amount.",
    "143": "Portion of payment deferred.",
    "144": "Incentive adjustment, e.g. preferred product/service.",
    "145": "Premium payment withholding",
    "146": "Diagnosis was invalid for the date(s) of service reported.",
    "147": "Provider contracted/negotiated rate expired or not on file.",
    "148": (
        "Information from another provider was not provided or was insufficient/incomplete. At "
        "least one Remark Code must be provided (may be comprised of either the NCPDP Reject Reason"
        " Code, or Remittance Advice Remark Code that is not an ALERT.)"
    ),
    "149": "Lifetime benefit maximum has been reached for this service/benefit category.",
    "150": "Payer deems the information submitted does not support this level of service.",
    "151": (
        "Payment adjusted because the payer deems the information submitted does not support this "
        "many/frequency of services."
    ),
    "152": (
        "Payer deems the information submitted does not support this length of service. Usage: "
        "Refer to the 835 Healthcare Policy Identification Segment (loop 2110 Service Payment "
        "Information REF), if present."
    ),
    "153": "Payer deems the information submitted does not support this dosage.",
    "154": "Payer deems the information submitted does not support this day's supply.",
    "155": "Patient refused the service/procedure.",
    "156": "Flexible spending account payments. Note: Use code 187.",
    "157": "Service/procedure was provided as a result of an act of war.",
    "158": "Service/procedure was provided outside of the United States.",
    "159": "Service/procedure was provided as a result of terrorism.",
    "160": "Injury/illness was the result of an activity that is a benefit exclusion.",
    "161": "Provider performance bonus",
    "162": (
        "State-mandated Requirement for Property and Casualty, see Claim Payment Remarks Code for "
        "specific explanation."
    ),
    "163": "Attachment/other documentation referenced on the claim was not received.",
    "164": (
        "Attachment/other documentation referenced on the claim was not received in a timely "
        "fashion."
    ),
    "165": "Referral absent or exceeded.",
    "166": (
        "These services were submitted after this payers responsibility for processing claims under"
        " this plan ended."
    ),
    "167": (
        "This (these) diagnosis(es) is (are) not covered. Usage: Refer to the 835 Healthcare Policy"
        " Identification Segment (loop 2110 Service Payment Information REF), if present."
    ),
    "168": (
        "Service(s) have been considered under the patient's medical plan. Benefits are not "
        "available under this dental plan."
    ),
    "169": "Alternate benefit has been provided.",
    "170": (
        "Payment is denied when performed/billed by this type of provider. Usage: Refer to the 835 "
        "Healthcare Policy Identification Segment (loop 2110 Service Payment Information REF), if "
        "present."
    ),
    "171": (
        "Payment is denied when performed/billed by this type of provider in this type of facility."
        " Usage: Refer to the 835 Healthcare Policy Identification Segment (loop 2110 Service "
        "Payment Information REF), if present."
    ),
    "172": (
        "Payment is adjusted when performed/billed by a provider of this specialty. Usage: Refer to"
        " the 835 Healthcare Policy Identification Segment (loop 2110 Service Payment Information "
        "REF), if present."
    ),
    "173": "Service/equipment was not prescribed by a physician.",
    "174": "Service was not prescribed prior to delivery.",
    "175": "Prescription is incomplete.",
    "176": "Prescription is not current.",
    "177": "Patient has not met the required eligibility requirements.",
    "178": "Patient has not met the required spend down requirements.",
    "179": (
        "Patient has not met the required waiting requirements. Usage: Refer to the 835 Healthcare "
        "Policy Identification Segment (loop 2110 Service Payment Information REF), if present."
    ),
    "180": "Patient has not met the required residency requirements.",
    "181": "Procedure code was invalid on the date of service.",
    "182": "Procedure modifier was invalid on the date of service.",
    "183": (
        "The referring provider is not eligible to refer the service billed. Usage: Refer to the "
        "835 Healthcare Policy Identification Segment (loop 2110 Service Payment Information REF), "
        "if present."
    ),
    "184": (
        "The prescribing/ordering provider is not eligible to prescribe/order the service billed. "
        "Usage: Refer to the 835 Healthcare Policy Identification Segment (loop 2110 Service "
        "Payment Information REF), if present."
    ),
    "185": (
        "The rendering provider is not eligible to perform the service billed. Usage: Refer to the "
        "835 Healthcare Policy Identification Segment (loop 2110 Service Payment Information REF), "
        "if present."
    ),
    "186": "Level of care change adjustment.",
    "187": (
        "Consumer Spending Account payments (includes but is not limited to Flexible Spending "
        "Account, Health Savings Account, Health Reimbursement Account, etc.)"
    ),
    "188": "This product/procedure is only covered when used according to FDA recommendations.",
    "189": (
        "'Not otherwise classified' or 'unlisted' procedure code (CPT/HCPCS) was billed when there "
        "is a specific procedure code for this procedure/service"
    ),
    "190": (
        "Payment is included in the allowance for a Skilled Nursing Facility (SNF) qualified stay."
    ),
    "191": (
        "Not a work related injury/illness and thus not the liability of the workers' compensation "
        "carrier Note: If adjustment is at the Claim Level, the payer must send and the provider "
        "should refer to the 835 Insurance Policy Number Segment (Loop 2100 Other Claim Related "
        "Information REF qualifier 'IG') for the jurisdictional regulation. If adjustment is at the"
        " Line Level, the payer must send and the provider should refer to the 835 Healthcare "
        "Policy Identification Segment (loop 2110 Service Payment information REF)"
    ),
    "192": (
        "Non standard adjustment code from paper remittance. Usage: This code is to be used by "
        "providers/payers providing Coordination of Benefits information to another payer in the "
        "837 transaction only. This code is only used when the non-standard code cannot be "
        "reasonably mapped to an existing Claims Adjustment Reason Code, specifically Deductible, "
        "Coinsurance and Co-payment."
    ),
    "193": (
        "Original payment decision is being maintained. Upon review, it was determined that this "
        "claim was processed properly."
    ),
    "194": (
        "Anesthesia performed by the operating physician, the assistant surgeon or the attending "
        "physician."
    ),
    "195": "Refund issued to an erroneous priority payer for this claim/service.",
    "196": "Claim/service denied based on prior payer's coverage determination.",
    "197": "Precertification/authorization/notification/pre-treatment absent.",
    "198": "Precertification/notification/authorization/pre-treatment exceeded.",
    "199": "Revenue code and Procedure code do not match.",
    "200": "Expenses incurred during lapse in coverage",
    "201": (
        "Patient is responsible for amount of this claim/service through 'set aside arrangement' or"
        " other agreement. (Use only with Group Code PR) At least one Remark Code must be provided "
        "(may be comprised of either the NCPDP Reject Reason Code, or Remittance Advice Remark Code"
        " that is not an ALERT.)"
    ),
    "202": "Non-covered personal comfort or convenience services.",
    "203": "Discontinued or reduced service.",
    "204": "This service/equipment/drug is not covered under the patient's current benefit plan",
    "205": "Pharmacy discount card processing fee",
    "206": "National Provider Identifier - missing.",
    "207": "National Provider identifier - Invalid format",
    "208": "National Provider Identifier - Not matched.",
    "209": (
        "Per regulatory or other agreement. The provider cannot collect this amount from the "
        "patient. However, this amount may be billed to subsequent payer. Refund to patient if "
        "collected. (Use only with Group code OA)"
    ),
    "210": (
        "Payment adjusted because pre-certification/authorization not received in a timely fashion"
    ),
    "211": "National Drug Codes (NDC) not eligible for rebate, are not covered.",
    "212": "Administrative surcharges are not covered",
    "213": (
        "Non-compliance with the physician self referral prohibition legislation or payer policy."
    ),
    "214": (
        "Workers' Compensation claim adjudicated as non-compensable. This Payer not liable for "
        "claim or service/treatment. Note: If adjustment is at the Claim Level, the payer must send"
        " and the provider should refer to the 835 Insurance Policy Number Segment (Loop 2100 Other"
        " Claim Related Information REF qualifier 'IG') for the jurisdictional regulation. If "
        "adjustment is at the Line Level, the payer must send and the provider should refer to the "
        "835 Healthcare Policy Identification Segment (loop 2110 Service Payment information REF). "
        "To be used for Workers' Compensation only"
    ),
    "215": "Based on subrogation of a third party settlement",
    "216": "Based on the findings of a review organization or the payer's findings.",
    "217": (
        "Based on payer reasonable and customary fees. No maximum allowable defined by legislated "
        "fee arrangement. (Note: To be used for Property and Casualty only)"
    ),
    "218": (
        "Based on entitlement to benefits. Note: If adjustment is at the Claim Level, the payer "
        "must send and the provider should refer to the 835 Insurance Policy Number Segment (Loop "
        "2100 Other Claim Related Information REF qualifier 'IG') for the jurisdictional "
        "regulation. If adjustment is at the Line Level, the payer must send and the provider "
        "should refer to the 835 Healthcare Policy Identification Segment (loop 2110 Service "
        "Payment information REF). To be used for Workers' Compensation only"
    ),
    "219": (
        "Based on extent of injury. Usage: If adjustment is at the Claim Level, the payer must send"
        " and the provider should refer to the 835 Insurance Policy Number Segment (Loop 2100 Other"
        " Claim Related Information REF qualifier 'IG') for the jurisdictional regulation. If "
        "adjustment is at the Line Level, the payer must send and the provider should refer to the "
        "835 Healthcare Policy Identification Segment (loop 2110 Service Payment information REF)."
    ),
    "220": (
        "The applicable fee schedule/fee database does not contain the billed code. Please resubmit"
        " a bill with the appropriate fee schedule/fee database code(s) that best describe the "
        "service(s) provided and supporting documentation if required. (Note: To be used for "
        "Property and Casualty only)"
    ),
    "221": (
        "Claim is under investigation. Note: If adjustment is at the Claim Level, the payer must "
        "send and the provider should refer to the 835 Insurance Policy Number Segment (Loop 2100 "
        "Other Claim Related Information REF qualifier 'IG') for the jurisdictional regulation. If "
        "adjustment is at the Line Level, the payer must send and the provider should refer to the "
        "835 Healthcare Policy Identification Segment (loop 2110 Service Payment information REF). "
        "(Note: To be used by Property & Casualty only)"
    ),
    "222": (
        "Exceeds the contracted maximum number of hours/days/units by this provider for this "
        "period. This is not patient specific. Usage: Refer to the 835 Healthcare Policy "
        "Identification Segment (loop 2110 Service Payment Information REF), if present."
    ),
    "223": (
        "Adjustment code for mandated federal, state or local law/regulation that is not already "
        "covered by another code and is mandated before a new code can be created."
    ),
    "224": (
        "Patient identification compromised by identity theft. Identity verification required for "
        "processing this and future claims."
    ),
    "225": (
        "Penalty or Interest Payment by Payer (Only used for plan to plan encounter reporting "
        "within the 837)"
    ),
    "226": (
        "Information requested from the Billing/Rendering Provider was not provided or not provided"
        " timely or was insufficient/incomplete. At least one Remark Code must be provided (may be "
        "comprised of either the NCPDP Reject Reason Code, or Remittance Advice Remark Code that is"
        " not an ALERT.)"
    ),
    "227": (
        "Information requested from the patient/insured/responsible party was not provided or was "
        "insufficient/incomplete. At least one Remark Code must be provided (may be comprised of "
        "either the NCPDP Reject Reason Code, or Remittance Advice Remark Code that is not an "
        "ALERT.)"
    ),
    "228": (
        "Denied for failure of this provider, another provider or the subscriber to supply "
        "requested information to a previous payer for their adjudication"
    ),
    "229": (
        "Partial charge amount not considered by Medicare due to the initial claim Type of Bill "
        "being 12X. Usage: This code can only be used in the 837 transaction to convey Coordination"
        " of Benefits information when the secondary payer's cost avoidance policy allows providers"
        " to bypass claim submission to a prior payer. (Use only with Group Code PR)"
    ),
    "230": (
        "No available or correlating CPT/HCPCS code to describe this service. Note: Used only by "
        "Property and Casualty."
    ),
    "231": (
        "Mutually exclusive procedures cannot be done in the same day/setting. Usage: Refer to the "
        "835 Healthcare Policy Identification Segment (loop 2110 Service Payment Information REF), "
        "if present."
    ),
    "232": (
        "Institutional Transfer Amount. Usage: Applies to institutional claims only and explains "
        "the DRG amount difference when the patient care crosses multiple institutions."
    ),
    "233": (
        "Services/charges related to the treatment of a hospital-acquired condition or preventable "
        "medical error."
    ),
    "234": (
        "This procedure is not paid separately. At least one Remark Code must be provided (may be "
        "comprised of either the NCPDP Reject Reason Code, or Remittance Advice Remark Code that is"
        " not an ALERT.)"
    ),
    "235": "Sales Tax",
    "236": (
        "This procedure or procedure/modifier combination is not compatible with another procedure "
        "or procedure/modifier combination provided on the same day according to the National "
        "Correct Coding Initiative or workers compensation state regulations/ fee schedule "
        "requirements."
    ),
    "237": (
        "Legislated/Regulatory Penalty. At least one Remark Code must be provided (may be comprised"
        " of either the NCPDP Reject Reason Code, or Remittance Advice Remark Code that is not an "
        "ALERT.)"
    ),
    "238": (
        "Claim spans eligible and ineligible periods of coverage, this is the reduction for the "
        "ineligible period. (Use only with Group Code PR)"
    ),
    "239": "Claim spans eligible and ineligible periods of coverage. Rebill separate claims.",
    "240": (
        "The diagnosis is inconsistent with the patient's birth weight. Usage: Refer to the 835 "
        "Healthcare Policy Identification Segment (loop 2110 Service Payment Information REF), if "
        "present."
    ),
    "241": "Low Income Subsidy (LIS) Co-payment Amount",
    "242": "Services not provided by network/primary care providers.",
    "243": "Services not authorized by network/primary care providers.",
    "244": (
        "Payment reduced to zero due to litigation. Additional information will be sent following "
        "the conclusion of litigation. To be used for Property & Casualty only."
    ),
    "245": "Provider performance program withhold.",
    "246": "This non-payable code is for required reporting only.",
    "247": (
        "Deductible for Professional service rendered in an Institutional setting and billed on an "
        "Institutional claim."
    ),
    "248": (
        "Coinsurance for Professional service rendered in an Institutional setting and billed on an"
        " Institutional claim."
    ),
    "249": "This claim has been identified as a readmission. (Use only with Group Code CO)",
    "250": (
        "The attachment/other documentation that was received was the incorrect "
        "attachment/document. The expected attachment/document is still missing. At least one "
        "Remark Code must be provided (may be comprised of either the NCPDP Reject Reason Code, or "
        "Remittance Advice Remark Code that is not an ALERT)."
    ),
    "251": (
        "The attachment/other documentation that was received was incomplete or deficient. The "
        "necessary information is still needed to process the claim. At least one Remark Code must "
        "be provided (may be comprised of either the NCPDP Reject Reason Code, or Remittance Advice"
        " Remark Code that is not an ALERT)."
    ),
    "252": (
        "An attachment/other documentation is required to adjudicate this claim/service. At least "
        "one Remark Code must be provided (may be comprised of either the NCPDP Reject Reason Code,"
        " or Remittance Advice Remark Code that is not an ALERT)."
    ),
    "253": "Sequestration - reduction in federal payment",
    "254": (
        "Claim received by the dental plan, but benefits not available under this plan. Submit "
        "these services to the patient's medical plan for further consideration."
    ),
    "255": (
        "The disposition of the related Property & Casualty claim (injury or illness) is pending "
        "due to litigation. (Use only with Group Code OA)"
    ),
    "256": "Service not payable per managed care contract.",
    "257": (
        "The disposition of the claim/service is undetermined during the premium payment grace "
        "period, per Health Insurance Exchange requirements. This claim/service will be reversed "
        "and corrected when the grace period ends (due to premium payment or lack of premium "
        "payment). (Use only with Group Code OA)"
    ),
    "258": (
        "Claim/service not covered when patient is in custody/incarcerated. Applicable federal, "
        "state or local authority may cover the claim/service."
    ),
    "259": "Additional payment for Dental/Vision service utilization.",
    "260": "Processed under Medicaid ACA Enhanced Fee Schedule",
    "261": "The procedure or service is inconsistent with the patient's history.",
    "262": "Adjustment for delivery cost. Usage: To be used for pharmaceuticals only.",
    "263": "Adjustment for shipping cost. Usage: To be used for pharmaceuticals only.",
    "264": "Adjustment for postage cost. Usage: To be used for pharmaceuticals only.",
    "265": "Adjustment for administrative cost. Usage: To be used for pharmaceuticals only.",
    "266": "Adjustment for compound preparation cost. Usage: To be used for pharmaceuticals only.",
    "267": (
        "Claim/service spans multiple months. At least one Remark Code must be provided (may be "
        "comprised of either the NCPDP Reject Reason Code, or Remittance Advice Remark Code that is"
        " not an ALERT.)"
    ),
    "268": "The Claim spans two calendar years. Please resubmit one claim per calendar year.",
    "269": (
        "Anesthesia not covered for this service/procedure. Usage: Refer to the 835 Healthcare "
        "Policy Identification Segment (loop 2110 Service Payment Information REF), if present."
    ),
    "270": (
        "Claim received by the medical plan, but benefits not available under this plan. Submit "
        "these services to the patient's dental plan for further consideration."
    ),
    "271": (
        "Prior contractual reductions related to a current periodic payment as part of a "
        "contractual payment schedule when deferred amounts have been previously reported. (Use "
        "only with Group Code OA)"
    ),
    "272": "Coverage/program guidelines were not met.",
    "273": "Coverage/program guidelines were exceeded.",
    "274": "Fee/Service not payable per patient Care Coordination arrangement.",
    "275": (
        "Prior payer's (or payers') patient responsibility (deductible, coinsurance, co-payment) "
        "not covered. (Use only with Group Code PR)"
    ),
    "276": "Services denied by the prior payer(s) are not covered by this payer.",
    "277": (
        "The disposition of the claim/service is undetermined during the premium payment grace "
        "period, per Health Insurance SHOP Exchange requirements. This claim/service will be "
        "reversed and corrected when the grace period ends (due to premium payment or lack of "
        "premium payment). (Use only with Group Code OA)"
    ),
    "278": (
        "Performance program proficiency requirements not met. (Use only with Group Codes CO or PI)"
        " Usage: Refer to the 835 Healthcare Policy Identification Segment (loop 2110 Service "
        "Payment Information REF), if present."
    ),
    "279": (
        "Services not provided by Preferred network providers. Usage: Use this code when there are "
        "member network limitations. For example, using contracted providers not in the member's "
        "'narrow' network."
    ),
    "280": (
        "Claim received by the medical plan, but benefits not available under this plan. Submit "
        "these services to the patient's Pharmacy plan for further consideration."
    ),
    "281": "Deductible waived per contractual agreement. Use only with Group Code CO.",
    "282": (
        "The procedure/revenue code is inconsistent with the type of bill. Usage: Refer to the 835 "
        "Healthcare Policy Identification Segment (loop 2110 Service Payment Information REF), if "
        "present."
    ),
    "283": "Attending provider is not eligible to provide direction of care.",
    "284": (
        "Precertification/authorization/notification/pre-treatment number may be valid but does not"
        " apply to the billed services."
    ),
    "285": "Appeal procedures not followed",
    "286": "Appeal time limits not met",
    "287": "Referral exceeded",
    "288": "Referral absent",
    "289": "Services considered under the dental and medical plans, benefits not available.",
    "290": (
        "Claim received by the dental plan, but benefits not available under this plan. Claim has "
        "been forwarded to the patient's medical plan for further consideration."
    ),
    "291": (
        "Claim received by the medical plan, but benefits not available under this plan. Claim has "
        "been forwarded to the patient's dental plan for further consideration."
    ),
    "292": (
        "Claim received by the medical plan, but benefits not available under this plan. Claim has "
        "been forwarded to the patient's pharmacy plan for further consideration."
    ),
    "293": "Payment made to employer.",
    "294": "Payment made to attorney.",
    "295": "Pharmacy Direct/Indirect Remuneration (DIR)",
    "296": (
        "Precertification/authorization/notification/pre-treatment number may be valid but does not"
        " apply to the provider."
    ),
    "297": (
        "Claim received by the medical plan, but benefits not available under this plan. Submit "
        "these services to the patient's vision plan for further consideration."
    ),
    "298": (
        "Claim received by the medical plan, but benefits not available under this plan. Claim has "
        "been forwarded to the patient's vision plan for further consideration."
    ),
    "299": "The billing provider is not eligible to receive payment for the service billed.",
    "300": (
        "Claim received by the Medical Plan, but benefits not available under this plan. Claim has "
        "been forwarded to the patient's Behavioral Health Plan for further consideration."
    ),
    "301": (
        "Claim received by the Medical Plan, but benefits not available under this plan. Submit "
        "these services to the patient's Behavioral Health Plan for further consideration."
    ),
    "302": "Precertification/notification/authorization/pre-treatment time limit has expired.",
    "303": (
        "Prior payer's (or payers') patient responsibility (deductible, coinsurance, co-payment) "
        "not covered for Qualified Medicare and Medicaid Beneficiaries. (Use only with Group Code "
        "CO)"
    ),
    "304": (
        "Claim received by the medical plan, but benefits not available under this plan. Submit "
        "these services to the patient's hearing plan for further consideration."
    ),
    "305": (
        "Claim received by the medical plan, but benefits not available under this plan. Claim has "
        "been forwarded to the patient's hearing plan for further consideration."
    ),
    "306": (
        "Type of bill is inconsistent with the patient status. Usage: Refer to the 835 Healthcare "
        "Policy Identification Segment (loop 2110 Service Payment Information REF), if present."
    ),
    "307": (
        "Medicare Maximum Fair Price Standard Default Refund Amount Adjustment. At least one Remark"
        " Code must be provided (may be comprised of either the NCPDP Reject Reason Code, or "
        "Remittance Advice Remark Code that is not an ALERT.) Usage: To be used only for the "
        "Medicare Drug Price Negotiation Program."
    ),
    "308": (
        "Payment is adjusted due to contracted funding agreement between the payer and provider."
    ),
    "A0": "Patient refund amount.",
    "A1": (
        "Claim/Service denied. At least one Remark Code must be provided (may be comprised of "
        "either the NCPDP Reject Reason Code, or Remittance Advice Remark Code that is not an "
        "ALERT.) Usage: Use this code only when a more specific Claim Adjustment Reason Code is not"
        " available."
    ),
    "A2": "Contractual adjustment.",
    "A3": "Medicare Secondary Payer liability met.",
    "A4": "Medicare Claim PPS Capital Day Outlier Amount.",
    "A5": "Medicare Claim PPS Capital Cost Outlier Amount.",
    "A6": "Prior hospitalization or 30 day transfer requirement not met.",
    "A7": "Presumptive Payment Adjustment",
    "A8": "Ungroupable DRG.",
    "B1": "Non-covered visits.",
    "B2": "Covered visits.",
    "B3": "Covered charges.",
    "B4": "Late filing penalty.",
    "B5": "Coverage/program guidelines were not met or were exceeded.",
    "B6": (
        "This payment is adjusted when performed/billed by this type of provider, by this type of "
        "provider in this type of facility, or by a provider of this specialty."
    ),
    "B7": (
        "This provider was not certified/eligible to be paid for this procedure/service on this "
        "date of service. Usage: Refer to the 835 Healthcare Policy Identification Segment (loop "
        "2110 Service Payment Information REF), if present."
    ),
    "B8": (
        "Alternative services were available, and should have been utilized. Usage: Refer to the "
        "835 Healthcare Policy Identification Segment (loop 2110 Service Payment Information REF), "
        "if present."
    ),
    "B9": "Patient is enrolled in a Hospice.",
    "B10": (
        "Allowed amount has been reduced because a component of the basic procedure/test was paid. "
        "The beneficiary is not liable for more than the charge limit for the basic procedure/test."
    ),
    "B11": (
        "The claim/service has been transferred to the proper payer/processor for processing. "
        "Claim/service not covered by this payer/processor."
    ),
    "B12": "Services not documented in patient's medical records.",
    "B13": (
        "Previously paid. Payment for this claim/service may have been provided in a previous "
        "payment."
    ),
    "B14": "Only one visit or consultation per physician per day is covered.",
    "B15": (
        "This service/procedure requires that a qualifying service/procedure be received and "
        "covered. The qualifying other service/procedure has not been received/adjudicated. Usage: "
        "Refer to the 835 Healthcare Policy Identification Segment (loop 2110 Service Payment "
        "Information REF), if present."
    ),
    "B16": "'New Patient' qualifications were not met.",
    "B17": (
        "Payment adjusted because this service was not prescribed by a physician, not prescribed "
        "prior to delivery, the prescription is incomplete, or the prescription is not current."
    ),
    "B18": "This procedure code and modifier were invalid on the date of service.",
    "B19": "Claim/service adjusted because of the finding of a Review Organization.",
    "B20": "Procedure/service was partially or fully furnished by another provider.",
    "B21": (
        "The charges were reduced because the service/care was partially furnished by another "
        "physician."
    ),
    "B22": "This payment is adjusted based on the diagnosis.",
    "B23": (
        "Procedure billed is not authorized per your Clinical Laboratory Improvement Amendment "
        "(CLIA) proficiency test."
    ),
    "D1": "Claim/service denied. Level of subluxation is missing or inadequate.",
    "D2": "Claim lacks the name, strength, or dosage of the drug furnished.",
    "D3": (
        "Claim/service denied because information to indicate if the patient owns the equipment "
        "that requires the part or supply was missing."
    ),
    "D4": "Claim/service does not indicate the period of time for which this will be needed.",
    "D5": "Claim/service denied. Claim lacks individual lab codes included in the test.",
    "D6": "Claim/service denied. Claim did not include patient's medical record for the service.",
    "D7": "Claim/service denied. Claim lacks date of patient's most recent physician visit.",
    "D8": "Claim/service denied. Claim lacks indicator that 'x-ray is available for review.'",
    "D9": (
        "Claim/service denied. Claim lacks invoice or statement certifying the actual cost of the "
        "lens, less discounts or the type of intraocular lens used."
    ),
    "D10": "Claim/service denied. Completed physician financial relationship form not on file.",
    "D11": "Claim lacks completed pacemaker registration form.",
    "D12": (
        "Claim/service denied. Claim does not identify who performed the purchased diagnostic test "
        "or the amount you were charged for the test."
    ),
    "D13": (
        "Claim/service denied. Performed by a facility/supplier in which the ordering/referring "
        "physician has a financial interest."
    ),
    "D14": "Claim lacks indication that plan of treatment is on file.",
    "D15": "Claim lacks indication that service was supervised or evaluated by a physician.",
    "D16": "Claim lacks prior payer payment information.",
    "D17": "Claim/Service has invalid non-covered days.",
    "D18": "Claim/Service has missing diagnosis information.",
    "D19": "Claim/Service lacks Physician/Operative or other supporting documentation",
    "D20": "Claim/Service missing service/product information.",
    "D21": "This (these) diagnosis(es) is (are) missing or are invalid",
    "D22": (
        "Reimbursement was adjusted for the reasons to be provided in separate correspondence. "
        "(Note: To be used for Workers' Compensation only) - Temporary code to be added for "
        "timeframe only until 01/01/2009. Another code to be established and/or for 06/2008 meeting"
        " for a revised code to replace or strategy to use another existing code"
    ),
    "D23": (
        "This dual eligible patient is covered by Medicare Part D per Medicare Retro-Eligibility. "
        "At least one Remark Code must be provided (may be comprised of either the NCPDP Reject "
        "Reason Code, or Remittance Advice Remark Code that is not an ALERT.)"
    ),
    "P1": (
        "State-mandated Requirement for Property and Casualty, see Claim Payment Remarks Code for "
        "specific explanation. To be used for Property and Casualty only."
    ),
    "P2": (
        "Not a work related injury/illness and thus not the liability of the workers' compensation "
        "carrier Usage: If adjustment is at the Claim Level, the payer must send and the provider "
        "should refer to the 835 Insurance Policy Number Segment (Loop 2100 Other Claim Related "
        "Information REF qualifier 'IG') for the jurisdictional regulation. If adjustment is at the"
        " Line Level, the payer must send and the provider should refer to the 835 Healthcare "
        "Policy Identification Segment (loop 2110 Service Payment information REF). To be used for "
        "Workers' Compensation only."
    ),
    "P3": (
        "Workers' Compensation case settled. Patient is responsible for amount of this "
        "claim/service through WC 'Medicare set aside arrangement' or other agreement. To be used "
        "for Workers' Compensation only. (Use only with Group Code PR)"
    ),
    "P4": (
        "Workers' Compensation claim adjudicated as non-compensable. This Payer not liable for "
        "claim or service/treatment. Usage: If adjustment is at the Claim Level, the payer must "
        "send and the provider should refer to the 835 Insurance Policy Number Segment (Loop 2100 "
        "Other Claim Related Information REF qualifier 'IG') for the jurisdictional regulation. If "
        "adjustment is at the Line Level, the payer must send and the provider should refer to the "
        "835 Healthcare Policy Identification Segment (loop 2110 Service Payment information REF). "
        "To be used for Workers' Compensation only"
    ),
    "P5": (
        "Based on payer reasonable and customary fees. No maximum allowable defined by legislated "
        "fee arrangement. To be used for Property and Casualty only."
    ),
    "P6": (
        "Based on entitlement to benefits. Usage: If adjustment is at the Claim Level, the payer "
        "must send and the provider should refer to the 835 Insurance Policy Number Segment (Loop "
        "2100 Other Claim Related Information REF qualifier 'IG') for the jurisdictional "
        "regulation. If adjustment is at the Line Level, the payer must send and the provider "
        "should refer to the 835 Healthcare Policy Identification Segment (loop 2110 Service "
        "Payment information REF). To be used for Property and Casualty only."
    ),
    "P7": (
        "The applicable fee schedule/fee database does not contain the billed code. Please resubmit"
        " a bill with the appropriate fee schedule/fee database code(s) that best describe the "
        "service(s) provided and supporting documentation if required. To be used for Property and "
        "Casualty only."
    ),
    "P8": (
        "Claim is under investigation. Usage: If adjustment is at the Claim Level, the payer must "
        "send and the provider should refer to the 835 Insurance Policy Number Segment (Loop 2100 "
        "Other Claim Related Information REF qualifier 'IG') for the jurisdictional regulation. If "
        "adjustment is at the Line Level, the payer must send and the provider should refer to the "
        "835 Healthcare Policy Identification Segment (loop 2110 Service Payment information REF). "
        "To be used for Property and Casualty only."
    ),
    "P9": (
        "No available or correlating CPT/HCPCS code to describe this service. To be used for "
        "Property and Casualty only."
    ),
    "P10": (
        "Payment reduced to zero due to litigation. Additional information will be sent following "
        "the conclusion of litigation. To be used for Property and Casualty only."
    ),
    "P11": (
        "The disposition of the related Property & Casualty claim (injury or illness) is pending "
        "due to litigation. To be used for Property and Casualty only. (Use only with Group Code "
        "OA)"
    ),
    "P12": (
        "Workers' compensation jurisdictional fee schedule adjustment. Usage: If adjustment is at "
        "the Claim Level, the payer must send and the provider should refer to the 835 Class of "
        "Contract Code Identification Segment (Loop 2100 Other Claim Related Information REF). If "
        "adjustment is at the Line Level, the payer must send and the provider should refer to the "
        "835 Healthcare Policy Identification Segment (loop 2110 Service Payment information REF) "
        "if the regulations apply. To be used for Workers' Compensation only."
    ),
    "P13": (
        "Payment reduced or denied based on workers' compensation jurisdictional regulations or "
        "payment policies, use only if no other code is applicable. Usage: If adjustment is at the "
        "Claim Level, the payer must send and the provider should refer to the 835 Insurance Policy"
        " Number Segment (Loop 2100 Other Claim Related Information REF qualifier 'IG') if the "
        "jurisdictional regulation applies. If adjustment is at the Line Level, the payer must send"
        " and the provider should refer to the 835 Healthcare Policy Identification Segment (loop "
        "2110 Service Payment information REF) if the regulations apply. To be used for Workers' "
        "Compensation only."
    ),
    "P14": (
        "The Benefit for this Service is included in the payment/allowance for another "
        "service/procedure that has been performed on the same day. Usage: Refer to the 835 "
        "Healthcare Policy Identification Segment (loop 2110 Service Payment Information REF), if "
        "present. To be used for Property and Casualty only."
    ),
    "P15": (
        "Workers' Compensation Medical Treatment Guideline Adjustment. To be used for Workers' "
        "Compensation only."
    ),
    "P16": (
        "Medical provider not authorized/certified to provide treatment to injured workers in this "
        "jurisdiction. To be used for Workers' Compensation only. (Use with Group Code CO or OA)"
    ),
    "P17": (
        "Referral not authorized by attending physician per regulatory requirement. To be used for "
        "Property and Casualty only."
    ),
    "P18": (
        "Procedure is not listed in the jurisdiction fee schedule. An allowance has been made for a"
        " comparable service. To be used for Property and Casualty only."
    ),
    "P19": (
        "Procedure has a relative value of zero in the jurisdiction fee schedule, therefore no "
        "payment is due. To be used for Property and Casualty only."
    ),
    "P20": (
        "Service not paid under jurisdiction allowed outpatient facility fee schedule. To be used "
        "for Property and Casualty only."
    ),
    "P21": (
        "Payment denied based on the Medical Payments Coverage (MPC) and/or Personal Injury "
        "Protection (PIP) Benefits jurisdictional regulations, or payment policies. Usage: If "
        "adjustment is at the Claim Level, the payer must send and the provider should refer to the"
        " 835 Insurance Policy Number Segment (Loop 2100 Other Claim Related Information REF "
        "qualifier 'IG') if the jurisdictional regulation applies. If adjustment is at the Line "
        "Level, the payer must send and the provider should refer to the 835 Healthcare Policy "
        "Identification Segment (loop 2110 Service Payment information REF) if the regulations "
        "apply. To be used for Property and Casualty Auto only."
    ),
    "P22": (
        "Payment adjusted based on the Medical Payments Coverage (MPC) and/or Personal Injury "
        "Protection (PIP) Benefits jurisdictional regulations, or payment policies. Usage: If "
        "adjustment is at the Claim Level, the payer must send and the provider should refer to the"
        " 835 Insurance Policy Number Segment (Loop 2100 Other Claim Related Information REF "
        "qualifier 'IG') if the jurisdictional regulation applies. If adjustment is at the Line "
        "Level, the payer must send and the provider should refer to the 835 Healthcare Policy "
        "Identification Segment (loop 2110 Service Payment information REF) if the regulations "
        "apply. To be used for Property and Casualty Auto only."
    ),
    "P23": (
        "Medical Payments Coverage (MPC) or Personal Injury Protection (PIP) Benefits "
        "jurisdictional fee schedule adjustment. Usage: If adjustment is at the Claim Level, the "
        "payer must send and the provider should refer to the 835 Class of Contract Code "
        "Identification Segment (Loop 2100 Other Claim Related Information REF). If adjustment is "
        "at the Line Level, the payer must send and the provider should refer to the 835 Healthcare"
        " Policy Identification Segment (loop 2110 Service Payment information REF) if the "
        "regulations apply. To be used for Property and Casualty Auto only."
    ),
    "P24": (
        "Payment adjusted based on Preferred Provider Organization (PPO). Usage: If adjustment is "
        "at the Claim Level, the payer must send and the provider should refer to the 835 Class of "
        "Contract Code Identification Segment (Loop 2100 Other Claim Related Information REF). If "
        "adjustment is at the Line Level, the payer must send and the provider should refer to the "
        "835 Healthcare Policy Identification Segment (loop 2110 Service Payment information REF) "
        "if the regulations apply. To be used for Property and Casualty only. Use only with Group "
        "Code CO."
    ),
    "P25": (
        "Payment adjusted based on Medical Provider Network (MPN). Usage: If adjustment is at the "
        "Claim Level, the payer must send and the provider should refer to the 835 Class of "
        "Contract Code Identification Segment (Loop 2100 Other Claim Related Information REF). If "
        "adjustment is at the Line Level, the payer must send and the provider should refer to the "
        "835 Healthcare Policy Identification Segment (loop 2110 Service Payment information REF) "
        "if the regulations apply. To be used for Property and Casualty only. (Use only with Group "
        "Code CO)."
    ),
    "P26": (
        "Payment adjusted based on Voluntary Provider network (VPN). Usage: If adjustment is at the"
        " Claim Level, the payer must send and the provider should refer to the 835 Class of "
        "Contract Code Identification Segment (Loop 2100 Other Claim Related Information REF). If "
        "adjustment is at the Line Level, the payer must send and the provider should refer to the "
        "835 Healthcare Policy Identification Segment (loop 2110 Service Payment information REF) "
        "if the regulations apply. To be used for Property and Casualty only. (Use only with Group "
        "Code CO)."
    ),
    "P27": (
        "Payment denied based on the Liability Coverage Benefits jurisdictional regulations and/or "
        "payment policies. Usage: If adjustment is at the Claim Level, the payer must send and the "
        "provider should refer to the 835 Insurance Policy Number Segment (Loop 2100 Other Claim "
        "Related Information REF qualifier 'IG') if the jurisdictional regulation applies. If "
        "adjustment is at the Line Level, the payer must send and the provider should refer to the "
        "835 Healthcare Policy Identification Segment (loop 2110 Service Payment information REF) "
        "if the regulations apply. To be used for Property and Casualty Auto only."
    ),
    "P28": (
        "Payment adjusted based on the Liability Coverage Benefits jurisdictional regulations "
        "and/or payment policies. Usage: If adjustment is at the Claim Level, the payer must send "
        "and the provider should refer to the 835 Insurance Policy Number Segment (Loop 2100 Other "
        "Claim Related Information REF qualifier 'IG') if the jurisdictional regulation applies. If"
        " adjustment is at the Line Level, the payer must send and the provider should refer to the"
        " 835 Healthcare Policy Identification Segment (loop 2110 Service Payment information REF) "
        "if the regulations apply. To be used for Property and Casualty Auto only."
    ),
    "P29": (
        "Liability Benefits jurisdictional fee schedule adjustment. Usage: If adjustment is at the "
        "Claim Level, the payer must send and the provider should refer to the 835 Class of "
        "Contract Code Identification Segment (Loop 2100 Other Claim Related Information REF). If "
        "adjustment is at the Line Level, the payer must send and the provider should refer to the "
        "835 Healthcare Policy Identification Segment (loop 2110 Service Payment information REF) "
        "if the regulations apply. To be used for Property and Casualty Auto only."
    ),
    "P30": (
        "Payment denied for exacerbation when supporting documentation was not complete. To be used"
        " for Property and Casualty only."
    ),
    "P31": (
        "Payment denied for exacerbation when treatment exceeds time allowed. To be used for "
        "Property and Casualty only."
    ),
    "P32": "Payment adjusted due to Apportionment.",
    "W1": (
        "Workers' compensation jurisdictional fee schedule adjustment. Note: If adjustment is at "
        "the Claim Level, the payer must send and the provider should refer to the 835 Class of "
        "Contract Code Identification Segment (Loop 2100 Other Claim Related Information REF). If "
        "adjustment is at the Line Level, the payer must send and the provider should refer to the "
        "835 Healthcare Policy Identification Segment (loop 2110 Service Payment information REF) "
        "if the regulations apply."
    ),
    "W2": (
        "Payment reduced or denied based on workers' compensation jurisdictional regulations or "
        "payment policies, use only if no other code is applicable. Note: If adjustment is at the "
        "Claim Level, the payer must send and the provider should refer to the 835 Insurance Policy"
        " Number Segment (Loop 2100 Other Claim Related Information REF qualifier 'IG') if the "
        "jurisdictional regulation applies. If adjustment is at the Line Level, the payer must send"
        " and the provider should refer to the 835 Healthcare Policy Identification Segment (loop "
        "2110 Service Payment information REF) if the regulations apply. To be used for Workers' "
        "Compensation only."
    ),
    "W3": (
        "The Benefit for this Service is included in the payment/allowance for another "
        "service/procedure that has been performed on the same day. Note: Refer to the 835 "
        "Healthcare Policy Identification Segment (loop 2110 Service Payment Information REF), if "
        "present. For use by Property and Casualty only."
    ),
    "W4": "Workers' Compensation Medical Treatment Guideline Adjustment.",
    "W5": (
        "Medical provider not authorized/certified to provide treatment to injured workers in this "
        "jurisdiction. (Use with Group Code CO or OA)"
    ),
    "W6": "Referral not authorized by attending physician per regulatory requirement.",
    "W7": (
        "Procedure is not listed in the jurisdiction fee schedule. An allowance has been made for a"
        " comparable service."
    ),
    "W8": (
        "Procedure has a relative value of zero in the jurisdiction fee schedule, therefore no "
        "payment is due."
    ),
    "W9": "Service not paid under jurisdiction allowed outpatient facility fee schedule.",
    "Y1": (
        "Payment denied based on Medical Payments Coverage (MPC) or Personal Injury Protection "
        "(PIP) Benefits jurisdictional regulations or payment policies, use only if no other code "
        "is applicable. Note: If adjustment is at the Claim Level, the payer must send and the "
        "provider should refer to the 835 Insurance Policy Number Segment (Loop 2100 Other Claim "
        "Related Information REF qualifier 'IG') if the jurisdictional regulation applies. If "
        "adjustment is at the Line Level, the payer must send and the provider should refer to the "
        "835 Healthcare Policy Identification Segment (loop 2110 Service Payment information REF) "
        "if the regulations apply. To be used for P&C Auto only."
    ),
    "Y2": (
        "Payment adjusted based on Medical Payments Coverage (MPC) or Personal Injury Protection "
        "(PIP) Benefits jurisdictional regulations or payment policies, use only if no other code "
        "is applicable. Note: If adjustment is at the Claim Level, the payer must send and the "
        "provider should refer to the 835 Insurance Policy Number Segment (Loop 2100 Other Claim "
        "Related Information REF qualifier 'IG') if the jurisdictional regulation applies. If "
        "adjustment is at the Line Level, the payer must send and the provider should refer to the "
        "835 Healthcare Policy Identification Segment (loop 2110 Service Payment information REF) "
        "if the regulations apply. To be used for P&C Auto only."
    ),
    "Y3": (
        "Medical Payments Coverage (MPC) or Personal Injury Protection (PIP) Benefits "
        "jurisdictional fee schedule adjustment. Note: If adjustment is at the Claim Level, the "
        "payer must send and the provider should refer to the 835 Class of Contract Code "
        "Identification Segment (Loop 2100 Other Claim Related Information REF). If adjustment is "
        "at the Line Level, the payer must send and the provider should refer to the 835 Healthcare"
        " Policy Identification Segment (loop 2110 Service Payment information REF) if the "
        "regulations apply. To be used for P&C Auto only."
    ),
}

DEACTIVATED_CARC: frozenset[str] = frozenset(
    {
        "15",
        "17",
        "25",
        "28",
        "30",
        "36",
        "37",
        "38",
        "41",
        "42",
        "43",
        "46",
        "47",
        "48",
        "52",
        "57",
        "62",
        "63",
        "64",
        "65",
        "67",
        "68",
        "71",
        "72",
        "73",
        "77",
        "79",
        "80",
        "81",
        "82",
        "83",
        "84",
        "86",
        "87",
        "88",
        "92",
        "93",
        "98",
        "99",
        "113",
        "120",
        "123",
        "124",
        "125",
        "126",
        "127",
        "138",
        "141",
        "145",
        "156",
        "162",
        "165",
        "168",
        "191",
        "196",
        "214",
        "217",
        "218",
        "220",
        "221",
        "230",
        "244",
        "255",
        "A2",
        "A3",
        "A4",
        "A7",
        "B2",
        "B3",
        "B5",
        "B6",
        "B17",
        "B18",
        "B19",
        "B21",
        "D1",
        "D2",
        "D3",
        "D4",
        "D5",
        "D6",
        "D7",
        "D8",
        "D9",
        "D10",
        "D11",
        "D12",
        "D13",
        "D14",
        "D15",
        "D16",
        "D17",
        "D18",
        "D19",
        "D20",
        "D21",
        "D22",
        "D23",
        "W1",
        "W2",
        "W3",
        "W4",
        "W5",
        "W6",
        "W7",
        "W8",
        "W9",
        "Y1",
        "Y2",
        "Y3",
    }
)
