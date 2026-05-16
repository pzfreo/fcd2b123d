"""Regression gate: the number of unexcluded corpus fixtures must not drop.

The corpus suite catches individual-fixture failures naturally. The
sneakier failure mode is "the suite passes because the fixture was
silently added to EXCLUDED" — count drops, green check still passes.
This test guards against that.

Per CLAUDE.md, adding a fixture to ``EXCLUDED_FROM_TEST`` always needs
explicit approval. If you legitimately need to bump the floor (e.g.,
a fixture was found to be invalid input rather than a translator gap),
update ``EXPECTED_MIN_RUNNING_COUNT`` *with the same PR* and cite the
reason here. Otherwise the gate has done its job — the PR is doing
something the human should review.
"""

from __future__ import annotations

from tests.test_translator_corpus import EXCLUDED_FROM_TEST, FIXTURE_DIRS


# Floor ratchets up as the corpus grows. Adding the seed-2026 sample
# (PR-pending) added 65 new running fixtures from the 100-file audit,
# bringing the total to 237. Should grow over time as issues close and
# fixtures move from EXCLUDED back to passing — never shrink without
# a justified update to this constant.
EXPECTED_MIN_RUNNING_COUNT = 238


def test_corpus_unexcluded_count_floor() -> None:
    total = sum(len(list(d.glob("*.FCStd"))) for d in FIXTURE_DIRS)
    running = total - len(EXCLUDED_FROM_TEST)
    assert running >= EXPECTED_MIN_RUNNING_COUNT, (
        f"Corpus running-fixture count dropped: {running} < {EXPECTED_MIN_RUNNING_COUNT}. "
        f"Either a fixture was added to EXCLUDED_FROM_TEST (needs explicit human "
        f"approval per CLAUDE.md) or fixtures were deleted. Total fixtures: {total}, "
        f"excluded: {len(EXCLUDED_FROM_TEST)}."
    )
