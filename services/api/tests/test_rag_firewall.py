from __future__ import annotations

from hypermeshdb.rag.symbolic.firewall import HallucinationFirewall


def test_supported_factual_sentence_is_returned():
    result = HallucinationFirewall().check(
        "The package is reachable from the public entry point [HEDGE-7].",
        {"HEDGE-7"},
    )

    assert result.outcome == "supported"
    assert not result.abstained
    assert result.coverage == 1.0


def test_invented_citation_is_not_returned():
    result = HallucinationFirewall().check(
        "The package is reachable from the public entry point [HEDGE-999].",
        {"HEDGE-7"},
    )

    assert result.outcome == "abstained"
    assert result.abstained
    assert "reachable" in result.stripped[0]


def test_valid_tag_cannot_launder_an_invented_tag():
    result = HallucinationFirewall().check(
        "The package is reachable from the public entry point [HEDGE-7][HEDGE-999].",
        {"HEDGE-7"},
    )

    assert result.outcome == "abstained"
    assert result.abstained


def test_honest_unverified_answer_is_allowed_without_a_citation():
    result = HallucinationFirewall().check(
        "[UNVERIFIED: cannot determine from available data]",
        set(),
    )

    assert result.outcome == "supported"
    assert not result.abstained


def test_supported_and_unsupported_sentences_return_only_supported_claims():
    result = HallucinationFirewall().check(
        "The package is present [HEDGE-7]. It is exploitable in production [HEDGE-99].",
        {"HEDGE-7"},
    )

    assert result.outcome == "stripped"
    assert result.answer == "The package is present [HEDGE-7]."
    assert len(result.stripped) == 1
