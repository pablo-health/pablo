# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""What a failed clearinghouse call is allowed to say in the log.

Two properties, and the second is a guardrail rather than a convenience.

**The vendor's own code survives.** Our exception classes are coarser than
the vendor's answers — one class stands for several things a person would
act on differently — so a log line naming only the class cannot be acted on.
The worked example is the one that prompted this: the enrollment API refuses
a test-mode key with ``403 access_denied`` and a sentence saying exactly
that, and the log used to read ``error=ClearinghouseAccessDeniedError`` and
stop, which is indistinguishable from a key that has been revoked.

**The vendor's sentence is kept away from the surfaces that send patient
data.** :func:`describe_error` is safe everywhere; only
:func:`describe_error_with_message` carries free text, and it is called just
where the request contained no patient data to be quoted back. The test
below pins the property the split exists for — that the safe helper never
leaks the message — rather than the call sites, which move.
"""

from __future__ import annotations

import pytest
from app.claims.clearinghouse import (
    ClearinghouseAccessDeniedError,
    ClearinghouseError,
    ClearinghouseInFlightError,
    ClearinghouseUnavailableError,
    describe_error,
    describe_error_with_message,
)


class TestTheVendorsCode:
    def test_an_error_carries_the_code_the_vendor_sent(self) -> None:
        exc = ClearinghouseAccessDeniedError("not available in test mode", code="access_denied")

        assert exc.code == "access_denied"

    def test_an_error_we_raised_ourselves_carries_no_code(self) -> None:
        """A timeout is ours, not the vendor's; inventing a code would lie."""
        assert ClearinghouseUnavailableError("the call timed out").code is None

    def test_the_retry_hint_and_the_code_coexist(self) -> None:
        """``ClearinghouseInFlightError`` overrides ``__init__``; both survive."""
        exc = ClearinghouseInFlightError("still going", retry_after=5.0, code="CONFLICT")

        assert (exc.retry_after, exc.code) == (5.0, "CONFLICT")


class TestDescribeError:
    def test_it_names_the_class_and_the_vendors_code(self) -> None:
        exc = ClearinghouseAccessDeniedError("whatever the vendor said", code="access_denied")

        assert describe_error(exc) == ("error=ClearinghouseAccessDeniedError code=access_denied")

    def test_a_missing_code_reads_as_none_rather_than_an_empty_value(self) -> None:
        assert describe_error(ClearinghouseUnavailableError("timeout")) == (
            "error=ClearinghouseUnavailableError code=none"
        )

    @pytest.mark.parametrize(
        "message",
        [
            "subscriber.memberId W123456789 is invalid",
            "patient lastName 'Okonkwo' failed validation",
        ],
    )
    def test_it_never_repeats_the_vendors_message(self, message: str) -> None:
        """Guardrail 5, at the one seam where vendor free text enters a log.

        These are the wordings that would matter: a member id and a name are
        both patient identifiers, and both are things a validation message
        could quote back out of a claim we just sent.
        """
        described = describe_error(ClearinghouseError(message, code="INVALID_REQUEST_BODY"))

        assert message not in described
        assert "W123456789" not in described
        assert "Okonkwo" not in described


class TestDescribeErrorWithMessage:
    def test_it_adds_the_vendors_sentence(self) -> None:
        exc = ClearinghouseAccessDeniedError(
            "Access Denied - This functionality is not available in Test Mode.",
            code="access_denied",
        )

        assert describe_error_with_message(exc) == (
            "error=ClearinghouseAccessDeniedError code=access_denied "
            "message=Access Denied - This functionality is not available in Test Mode."
        )

    def test_it_is_the_safe_description_plus_the_message(self) -> None:
        """The two stay in step: whatever ``describe_error`` says is a prefix."""
        exc = ClearinghouseAccessDeniedError("denied", code="access_denied")

        assert describe_error_with_message(exc).startswith(describe_error(exc))
