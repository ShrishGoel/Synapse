from unittest.mock import AsyncMock, patch

import pytest

import main


def _cooler_payload() -> main.SynthesizeRequest:
    return main.SynthesizeRequest(
        user_prompt="Graph the laptop cooling pads I have been looking at on Amazon.",
        firecrawl_query_budget=1,
        active_tabs=[
            main.ActiveTab(
                url="https://www.amazon.com/dp/B0EXAMPLE01",
                summary=(
                    "KLIM Wind cooling pad, 5 fans, RGB lighting, fits 15-17 inch laptops, "
                    "USD 39.99, many reviews mention loudness at max fan speed."
                ),
            ),
            main.ActiveTab(
                url="https://www.amazon.com/dp/B0EXAMPLE02",
                summary=(
                    "llano RGB laptop cooler with high-RPM turbo fan, fits 15.6-19 inch laptops, "
                    "USD 89.99, users report stronger cooling but larger footprint."
                ),
            ),
        ],
    )


@pytest.mark.asyncio
async def test_build_rubric_for_amazon_coolers_uses_user_prompt_context() -> None:
    structured = AsyncMock(
        return_value=main.ComparisonRubric(
            fields=["Price USD", "Fan Count", "Noise Level", "Cooling Performance"],
            inferred_constraints=["Amazon listings", "laptop cooling pads"],
            default_ordering="cooling performance first",
            seed_patterns=["higher fan speed vs noise tradeoff"],
        )
    )
    payload = _cooler_payload()

    with patch("main._structured_llm_call", structured):
        await main._build_rubric(payload, "sample cooler context")

    kwargs = structured.await_args.kwargs
    assert "Graph the laptop cooling pads I have been looking at on Amazon." in kwargs["user_prompt"]
    assert "Infer the rubric from the seed items themselves" in kwargs["system_prompt"]


@pytest.mark.asyncio
async def test_evaluate_context_for_coolers_keeps_query_budget_and_missing_fields() -> None:
    structured = AsyncMock(
        return_value=main.EvaluationState(
            is_complete=False,
            should_search_more=True,
            missing_fields=["Noise Level"],
            search_queries=["amazon laptop cooling pad noise dba reviews"],
            stop_reason="Need stronger noise evidence.",
        )
    )

    with patch("main._structured_llm_call", structured):
        await main._evaluate_context(
            user_prompt="Graph the laptop cooling pads I have been looking at on Amazon.",
            rubric=main.ComparisonRubric(
                fields=["Price USD", "Fan Count", "Noise Level", "Cooling Performance"],
                inferred_constraints=["Amazon listings", "laptop cooling pads"],
                default_ordering="cooling performance first",
                seed_patterns=["higher fan speed vs noise tradeoff"],
            ),
            context="Known cooler tab summaries",
            remaining_query_budget=1,
        )

    kwargs = structured.await_args.kwargs
    assert "Remaining Firecrawl query budget" in kwargs["user_prompt"]
    assert "Graph the laptop cooling pads I have been looking at on Amazon." in kwargs["user_prompt"]


@pytest.mark.asyncio
async def test_synthesize_graph_for_coolers_requires_seed_and_discovered_source_types() -> None:
    structured = AsyncMock(
        return_value=main.ReactFlowGraphData(
            nodes=[
                main.ReactFlowNode(
                    id="seed-cooler-1",
                    type="research",
                    data={
                        "title": "KLIM Wind",
                        "url": "https://www.amazon.com/dp/B0EXAMPLE01",
                        "priceUsd": 39.99,
                        "aiRank": 2,
                        "aiReason": "Lower price and wide compatibility.",
                        "sourceType": "seed",
                        "constraintViolated": False,
                        "constraintReason": "",
                    },
                    position=main.GraphPosition(x=0, y=0),
                ),
                main.ReactFlowNode(
                    id="disc-cooler-2",
                    type="research",
                    data={
                        "title": "IETS GT500",
                        "url": "https://www.amazon.com/dp/B0EXAMPLE03",
                        "priceUsd": 79.99,
                        "aiRank": 1,
                        "aiReason": "Best cooling performance from user reports.",
                        "sourceType": "discovered",
                        "constraintViolated": True,
                        "constraintReason": "Above budget",
                    },
                    position=main.GraphPosition(x=320, y=0),
                ),
            ],
            edges=[],
        )
    )

    rubric = main.ComparisonRubric(
        fields=["Price USD", "Fan Count", "Noise Level", "Cooling Performance"],
        inferred_constraints=["Amazon listings", "laptop cooling pads"],
        default_ordering="cooling performance first",
        seed_patterns=["higher fan speed vs noise tradeoff"],
    )

    with patch("main._structured_llm_call", structured):
        graph = await main._synthesize_graph(
            user_prompt="Graph the laptop cooling pads I have been looking at on Amazon.",
            rubric=rubric,
            context="Cooler context and review snippets",
        )

    kwargs = structured.await_args.kwargs
    assert "sourceType is either seed or discovered" in kwargs["system_prompt"]
    assert len(graph.nodes) == 2
    assert {node.data.sourceType for node in graph.nodes} == {"seed", "discovered"}


def test_canonicalize_cooler_metadata_does_not_map_noise_to_distance() -> None:
    graph = main.ReactFlowGraphData(
        nodes=[
            main.ReactFlowNode(
                id="cooler-1",
                type="research",
                data={
                    "title": "llano RGB Cooler",
                    "url": "https://www.amazon.com/dp/B0EXAMPLE02",
                    "sourceType": "seed",
                    "Price": "$39.99",
                    "Noise Level (dB)": "≤70dB",
                    "Laptop Size Compatibility": "15.6 to 19 inches",
                    "Cooling Performance": "High",
                    "constraintViolated": False,
                    "constraintReason": "",
                },
                position=main.GraphPosition(x=0, y=0),
            )
        ],
        edges=[],
    )

    canonical = main._canonicalize_graph_for_frontend(graph)
    session = main._graph_to_unified_session(canonical, "Compare the laptop cooling pads")
    node = session.graph.nodes[0]

    assert node.metadata["priceUsd"] == 39.99
    assert "70dB" in node.metadata["Noise Level (dB)"]
    assert node.metadata["Laptop Size Compatibility"] == "15.6 to 19 inches"
    assert any("Noise Level" in metric["label"] for metric in node.metadata["metrics"])
    assert "distanceMiles" not in node.metadata
    assert "bedrooms" not in node.metadata
    assert "bathrooms" not in node.metadata
