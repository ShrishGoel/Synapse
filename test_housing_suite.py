from unittest.mock import AsyncMock, patch

import pytest

import main


def _housing_payload() -> main.SynthesizeRequest:
    return main.SynthesizeRequest(
        user_prompt="Find Zillow rentals in Pasadena near Caltech for $1000-$1500 per month",
        firecrawl_query_budget=1,
        active_tabs=[
            main.ActiveTab(
                url="https://www.zillow.com/homedetails/1030-E-Green-St-APT-11B-Pasadena-CA-91106/429321865_zpid/",
                summary="Furnished studio on East Green Street for $1450, walkable to Caltech and South Lake.",
            ),
            main.ActiveTab(
                url="https://www.zillow.com/homedetails/Pasadena-CA-91106/460711387_zpid/",
                summary="Michigan-area private room for $1150, 0.7 miles from Caltech in Pasadena.",
            ),
        ],
    )


@pytest.mark.asyncio
async def test_build_rubric_instructs_llm_to_extract_seed_patterns() -> None:
    structured = AsyncMock(
        return_value=main.ComparisonRubric(
            fields=["Price USD", "Distance to Caltech", "Rental Type"],
            inferred_constraints=["Pasadena near Caltech", "$1000-$1500"],
            default_ordering="closest in-budget rental first",
            seed_patterns=["all seeds are Pasadena rentals"],
        )
    )
    payload = _housing_payload()

    with patch("main._structured_llm_call", structured):
        await main._build_rubric(payload, "sample context")

    kwargs = structured.await_args.kwargs
    assert "Infer the rubric from the seed items themselves" in kwargs["system_prompt"]
    assert "Distance to Anchor" in kwargs["user_prompt"]
    assert "Source URL" in kwargs["user_prompt"]


@pytest.mark.asyncio
async def test_evaluate_context_instructs_llm_to_use_small_targeted_queries() -> None:
    structured = AsyncMock(
        return_value=main.EvaluationState(
            is_complete=False,
            should_search_more=True,
            missing_fields=["Lease Term"],
            search_queries=["Pasadena Caltech studio Zillow $1000 $1500"],
            stop_reason="Need one more qualifying rental.",
        )
    )

    with patch("main._structured_llm_call", structured):
        await main._evaluate_context(
            user_prompt="Find Zillow rentals in Pasadena near Caltech for $1000-$1500 per month",
            rubric=main.ComparisonRubric(
                fields=["Price USD", "Distance to Caltech", "Rental Type"],
                inferred_constraints=["Pasadena near Caltech", "$1000-$1500"],
                default_ordering="closest in-budget rental first",
                seed_patterns=["all seeds are Pasadena rentals"],
            ),
            context="Known Zillow rentals",
            remaining_query_budget=1,
        )

    kwargs = structured.await_args.kwargs
    assert "Pasadena near Caltech" in kwargs["user_prompt"]
    assert "no more than three targeted search queries" in kwargs["user_prompt"]
    assert "Search for candidate listings that expose price" in kwargs["system_prompt"]
