from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

import main
import summarizer


def _dom_payload() -> dict:
    return {
        "user_prompt": "Graph the laptop coolers I have been looking at.",
        "firecrawl_query_budget": 1,
        "tabs": [
            {
                "url": "https://www.amazon.com/dp/B09B7CWT63",
                "title": "IETS GT500",
                "dom": "<html><body><h1>IETS GT500</h1><span>$79.99</span></body></html>",
            },
            {
                "url": "https://www.amazon.com/dp/B0C69BVWGB",
                "title": "llano RGB Cooler",
                "dom": "<html><body><h1>llano RGB</h1><span>$89.99</span></body></html>",
            },
        ],
    }


def _mock_summary(title: str, price: str) -> dict:
    return {
        "page_type": "product",
        "title": title,
        "one_sentence_summary": f"{title} laptop cooler listing.",
        "key_points": [f"Price shown as {price}", "Cooling-focused accessory"],
        "entities": [{"name": title, "type": "product", "evidence": title}],
        "facts": [{"label": "Price", "value": price, "evidence": price}],
    }


def _pipeline_graph() -> main.ReactFlowGraphData:
    return main.ReactFlowGraphData(
        nodes=[
            main.ReactFlowNode(
                id="cooler-a",
                type="research",
                data={
                    "title": "IETS GT500",
                    "sourceType": "seed",
                    "constraintViolated": False,
                    "constraintReason": "",
                },
                position=main.GraphPosition(x=0, y=0),
            )
        ],
        edges=[],
    )


@pytest.mark.asyncio
async def test_synthesize_from_dom_calls_summarizer_then_synthesize() -> None:
    synthesize_mock = AsyncMock(return_value=_pipeline_graph())

    with (
        patch("main.summarize_html", side_effect=lambda html: _mock_summary("IETS GT500", "$79.99") if "GT500" in html else _mock_summary("llano RGB Cooler", "$89.99")),
        patch("main.synthesize", synthesize_mock),
    ):
        transport = ASGITransport(app=main.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/api/v1/synthesize-from-dom", json=_dom_payload())

    assert response.status_code == 200
    assert response.json()["nodes"][0]["id"] == "cooler-a"

    synthesize_mock.assert_awaited_once()
    synthesize_payload = synthesize_mock.await_args.args[0]
    assert synthesize_payload.user_prompt == "Graph the laptop coolers I have been looking at."
    assert len(synthesize_payload.active_tabs) == 2
    assert "Facts: Price: $79.99" in synthesize_payload.active_tabs[0].summary


@pytest.mark.asyncio
async def test_synthesize_graph_prompt_keeps_price_extraction_separate_from_constraints() -> None:
    structured = AsyncMock(
        return_value=main.ReactFlowGraphData(
            nodes=[],
            edges=[],
        )
    )

    with patch("main._structured_llm_call", structured):
        await main._synthesize_graph(
            user_prompt="Compare the laptop cooling pads",
            user_constraint="under $50",
            rubric=main.ComparisonRubric(
                domain="products",
                fields=["Price USD", "Cooling Performance", "Review Consensus"],
                inferred_constraints=[],
                default_ordering="best value first",
                seed_patterns=[],
            ),
            context="Facts: Price: $96.00",
        )

    kwargs = structured.await_args.kwargs
    assert "Never change factual fields to satisfy the constraint" in kwargs["system_prompt"]
    assert "extract the displayed dollar amount directly" in kwargs["user_prompt"]
    assert "multiple constraints" in kwargs["user_prompt"]


@pytest.mark.asyncio
async def test_synthesize_from_dom_uses_fallback_when_summarizer_fails() -> None:
    synthesize_mock = AsyncMock(return_value=_pipeline_graph())
    with (
        patch("main.summarize_html", side_effect=main.SummarizerError("OpenRouter unavailable")),
        patch("main.synthesize", synthesize_mock),
    ):
        transport = ASGITransport(app=main.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/api/v1/synthesize-from-dom", json=_dom_payload())

    assert response.status_code == 200
    synthesize_mock.assert_awaited_once()
    synthesize_payload = synthesize_mock.await_args.args[0]
    assert "Unable to summarize page." not in synthesize_payload.active_tabs[0].summary


@pytest.mark.asyncio
async def test_synthesize_from_extension_history_uses_stored_dom_entries() -> None:
    main.extension_history.clear()
    main.extension_history["https://www.amazon.com/dp/B09B7CWT63"] = {
        "url": "https://www.amazon.com/dp/B09B7CWT63",
        "title": "IETS GT500",
        "dom": "<html><body>IETS GT500</body></html>",
        "timestamp": 9999999999,
    }

    synthesize_from_dom_mock = AsyncMock(return_value=_pipeline_graph())
    with patch("main.synthesize_from_dom", synthesize_from_dom_mock):
        transport = ASGITransport(app=main.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/v1/synthesize-from-extension-history",
                json={
                    "user_prompt": "Graph what I viewed",
                    "user_constraint": "Under $50",
                    "firecrawl_query_budget": 0,
                    "max_tabs": 10,
                },
            )

    assert response.status_code == 200
    synthesize_from_dom_mock.assert_awaited_once()
    forwarded = synthesize_from_dom_mock.await_args.args[0]
    assert forwarded.user_constraint == "Under $50"
    assert len(forwarded.tabs) == 1
    assert str(forwarded.tabs[0].url) == "https://www.amazon.com/dp/B09B7CWT63"


@pytest.mark.asyncio
async def test_synthesize_from_extension_history_forwards_enable_discovery_flag() -> None:
    main.extension_history.clear()
    main.extension_history["https://www.amazon.com/dp/B09B7CWT63"] = {
        "url": "https://www.amazon.com/dp/B09B7CWT63",
        "title": "IETS GT500",
        "dom": "<html><body>IETS GT500</body></html>",
        "timestamp": 9999999999,
    }

    synthesize_from_dom_mock = AsyncMock(return_value=_pipeline_graph())
    with patch("main.synthesize_from_dom", synthesize_from_dom_mock):
        transport = ASGITransport(app=main.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/v1/synthesize-from-extension-history",
                json={
                    "user_prompt": "Graph what I viewed",
                    "firecrawl_query_budget": 0,
                    "max_tabs": 10,
                    "enable_discovery": False,
                },
            )

    assert response.status_code == 200
    synthesize_from_dom_mock.assert_awaited_once()
    forwarded = synthesize_from_dom_mock.await_args.args[0]
    assert forwarded.enable_discovery is False


@pytest.mark.asyncio
async def test_extension_preferences_round_trip_includes_discovery_flag() -> None:
    transport = ASGITransport(app=main.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        update_response = await client.post(
            "/api/v1/extension/preferences",
            json={
                "user_prompt": "Compare the cooling pads I viewed",
                "enable_discovery": False,
            },
        )
        fetch_response = await client.get("/api/v1/extension/preferences")

    assert update_response.status_code == 200
    assert fetch_response.status_code == 200
    assert update_response.json()["enable_discovery"] is False
    assert fetch_response.json()["user_prompt"] == "Compare the cooling pads I viewed"
    assert fetch_response.json()["enable_discovery"] is False


def test_reconcile_seed_nodes_marks_active_tab_urls_as_seed() -> None:
    graph = main.ReactFlowGraphData(
        nodes=[
            main.ReactFlowNode(
                id="seed-item",
                type="research",
                data={
                    "title": "Captured cooler",
                    "url": "https://www.amazon.com/dp/B0EXAMPLE01?th=1",
                    "sourceType": "discovered",
                    "statusLabel": "enriched",
                    "constraintViolated": False,
                    "constraintReason": "",
                },
                position=main.GraphPosition(x=0, y=0),
            )
        ],
        edges=[],
    )

    reconciled = main._reconcile_seed_nodes(
        graph,
        [
            main.ActiveTab(
                url="https://www.amazon.com/dp/B0EXAMPLE01",
                summary="Captured from extension history",
            )
        ],
    )

    node = reconciled.nodes[0]
    assert node.data.sourceType == "seed"
    assert node.data.statusLabel == "captured"


def test_apply_constraint_to_session_persists_user_constraint() -> None:
    session = main.UnifiedSession(
        session_id="ses_test",
        query="Compare the cooling pads",
        user_constraint="",
        domain="products",
        status="ready",
        rubric_fields=["Price USD"],
        graph=main.SessionGraph(
            rubric_fields=["Price USD"],
            nodes=[
                main.SessionGraphNode(
                    id="cooler-a",
                    type="item",
                    url="https://example.com/a",
                    source="Captured",
                    title="Cooler A",
                    subtitle="$79",
                    status="ready",
                    group="options",
                    tags=[],
                    metadata={"priceUsd": 79, "aiRank": 1, "constraintViolated": False},
                    summary="A cooler",
                )
            ],
            edges=[],
        ),
        matrix=main.SessionMatrix(
            rubric="Product Comparison",
            columns=[main.SessionMatrixColumn(key="price", label="Price", type="text")],
            rows=[main.SessionMatrixRow(node_id="cooler-a", cells={"price": main.SessionMatrixCell(display="$79")})],
        ),
        digest=main.SessionDigest(
            theme="Product comparison",
            theme_signals=["cooling", "pads"],
            stats=main.SessionDigestStats(total=1, ready=1, extracting=0, pending=0),
            entries=[
                main.SessionDigestEntry(
                    node_id="cooler-a",
                    relevance=0.9,
                    summary="A cooler",
                    signals=[],
                    source_note="Captured",
                )
            ],
        ),
    )

    updated = main._apply_constraint_to_session(session, "under 50 dollars")

    assert updated.user_constraint == "under 50 dollars"
    assert any(signal == "constraint: under 50 dollars" for signal in updated.digest.theme_signals)
    assert updated.graph.nodes[0].status == "ready"


def test_drop_discovered_nodes_keeps_only_captured_seed_nodes() -> None:
    graph = main.ReactFlowGraphData(
        nodes=[
            main.ReactFlowNode(
                id="seed-a",
                type="research",
                data={
                    "title": "Captured cooler",
                    "sourceType": "seed",
                    "constraintViolated": False,
                    "constraintReason": "",
                },
                position=main.GraphPosition(x=0, y=0),
            ),
            main.ReactFlowNode(
                id="disc-b",
                type="research",
                data={
                    "title": "AI result",
                    "sourceType": "discovered",
                    "constraintViolated": False,
                    "constraintReason": "",
                },
                position=main.GraphPosition(x=320, y=0),
            ),
        ],
        edges=[
            main.ReactFlowEdge(id="edge-a-b", source="seed-a", target="disc-b"),
        ],
    )

    filtered = main._drop_discovered_nodes(graph)

    assert [node.id for node in filtered.nodes] == ["seed-a"]
    assert filtered.edges == []


def test_ensure_seed_nodes_present_backfills_missing_captured_tabs() -> None:
    graph = main.ReactFlowGraphData(
        nodes=[
            main.ReactFlowNode(
                id="cooler-a",
                type="research",
                data={
                    "title": "IETS GT500",
                    "url": "https://www.amazon.com/dp/B09B7CWT63",
                    "sourceType": "seed",
                    "aiRank": 1,
                    "aiReason": "Present",
                    "summary": "Captured tab A",
                    "constraintViolated": False,
                    "constraintReason": "",
                },
                position=main.GraphPosition(x=0, y=0),
            )
        ],
        edges=[],
    )

    ensured = main._ensure_seed_nodes_present(
        graph,
        [
            main.ActiveTab(
                url="https://www.amazon.com/dp/B09B7CWT63",
                summary="Title: IETS GT500 | Summary: Captured tab A",
            ),
            main.ActiveTab(
                url="https://www.amazon.com/dp/B0C69BVWGB",
                summary="Title: llano RGB Cooler | Summary: Captured tab B",
            ),
            main.ActiveTab(
                url="https://www.amazon.com/dp/B0ABCDEF12",
                summary="Title: KLIM Wind | Summary: Captured tab C",
            ),
        ],
    )

    assert len(ensured.nodes) == 3
    assert {
        str(node.data.url)
        for node in ensured.nodes
    } == {
        "https://www.amazon.com/dp/B09B7CWT63",
        "https://www.amazon.com/dp/B0C69BVWGB",
        "https://www.amazon.com/dp/B0ABCDEF12",
    }
    assert all(node.data.sourceType == "seed" for node in ensured.nodes)


def test_select_relevant_extension_entries_keeps_multiple_relevant_large_tabs() -> None:
    entries = [
        {
            "url": "https://www.amazon.com/dp/B09B7CWT63",
            "title": "IETS GT500 Laptop Cooler",
            "dom": "x" * 210000,
            "timestamp": 3,
        },
        {
            "url": "https://www.amazon.com/dp/B0C69BVWGB",
            "title": "llano RGB Laptop Cooler",
            "dom": "x" * 210000,
            "timestamp": 2,
        },
        {
            "url": "https://www.amazon.com/dp/B0ABCDEF12",
            "title": "KLIM Wind Laptop Cooling Pad",
            "dom": "x" * 210000,
            "timestamp": 1,
        },
    ]

    selected = main._select_relevant_extension_entries(
        entries,
        "Compare the laptop coolers I have been looking at on Amazon",
        3,
    )

    assert len(selected) == 3
    assert {entry["title"] for entry in selected} == {
        "IETS GT500 Laptop Cooler",
        "llano RGB Laptop Cooler",
        "KLIM Wind Laptop Cooling Pad",
    }


def test_entry_relevance_score_no_longer_uses_domain_specific_non_amazon_heuristics() -> None:
    stand_score = main._entry_relevance_score(
        {
            "url": "https://example.com/ergonomic-laptop-stand",
            "title": "Ergonomic Laptop Stand",
            "dom": "<html></html>",
        },
        "Compare the laptop coolers I have been looking at",
    )
    zillow_score = main._entry_relevance_score(
        {
            "url": "https://www.zillow.com/homedetails/example",
            "title": "Zillow listing",
            "dom": "<html></html>",
        },
        "Find rental options near campus",
    )

    assert stand_score == 5
    assert zillow_score == 0


def test_apply_constraint_to_data_dict_accepts_natural_budget_phrases() -> None:
    flagged = main._apply_constraint_to_data_dict(
        {"priceUsd": 79, "combinedScore": 90, "constraintViolated": False, "constraintReason": ""},
        "at most 50 dollars",
    )
    allowed = main._apply_constraint_to_data_dict(
        {"priceUsd": 39, "combinedScore": 90, "constraintViolated": False, "constraintReason": ""},
        "up to $50",
    )
    min_required = main._apply_constraint_to_data_dict(
        {"priceUsd": 39, "combinedScore": 90, "constraintViolated": False, "constraintReason": ""},
        "at least 50",
    )

    assert flagged["constraintViolated"] is True
    assert flagged["constraintReason"] == "Above budget cap of $50"
    assert allowed["constraintViolated"] is False
    assert min_required["constraintViolated"] is True
    assert min_required["constraintReason"] == "Below minimum budget of $50"


def test_apply_constraint_to_data_dict_respects_price_inequality_text() -> None:
    above_threshold = main._apply_constraint_to_data_dict(
        {
            "Price": "Typically >$50",
            "combinedScore": 90,
            "constraintViolated": False,
            "constraintReason": "",
        },
        "under $50",
    )
    below_threshold = main._apply_constraint_to_data_dict(
        {
            "Price": "Usually <$50",
            "combinedScore": 90,
            "constraintViolated": False,
            "constraintReason": "",
        },
        "at least 50 dollars",
    )

    assert above_threshold["constraintViolated"] is True
    assert above_threshold["constraintReason"] == "Above budget cap of $50"
    assert below_threshold["constraintViolated"] is True
    assert below_threshold["constraintReason"] == "Below minimum budget of $50"


@pytest.mark.asyncio
async def test_synthesize_disables_firecrawl_when_discovery_is_off() -> None:
    payload = main.SynthesizeRequest(
        user_prompt="Compare the cooling pads I viewed",
        firecrawl_query_budget=4,
        enable_discovery=False,
        active_tabs=[
            main.ActiveTab(
                url="https://example.com/cooler-a",
                summary="Cooling pad A for $39",
            )
        ],
    )

    with (
        patch("main._build_rubric", AsyncMock(return_value=main.ComparisonRubric(
            domain="products",
            fields=["Price USD", "Noise Level"],
            inferred_constraints=[],
            default_ordering="lowest price first",
            seed_patterns=[],
        ))),
        patch("main._evaluate_context", AsyncMock(return_value=main.EvaluationState(
            is_complete=True,
            should_search_more=False,
            missing_fields=[],
            search_queries=[],
            stop_reason="Enough context",
        ))),
        patch("main._synthesize_graph", AsyncMock(return_value=_pipeline_graph())),
        patch("main._run_firecrawl_search", AsyncMock()) as firecrawl_mock,
    ):
        await main.synthesize(payload)

    firecrawl_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_synthesize_adds_similarity_intent_when_discovery_is_on() -> None:
    payload = main.SynthesizeRequest(
        user_prompt="Compare the tennis shoes I viewed",
        firecrawl_query_budget=0,
        enable_discovery=True,
        active_tabs=[
            main.ActiveTab(
                url="https://example.com/shoe-a",
                summary="Tennis shoe A for $79",
            )
        ],
    )

    build_rubric_mock = AsyncMock(return_value=main.ComparisonRubric(
        domain="products",
        fields=["Price USD", "Stability"],
        inferred_constraints=[],
        default_ordering="best overall first",
        seed_patterns=[],
    ))
    synthesize_graph_mock = AsyncMock(return_value=_pipeline_graph())

    with (
        patch("main._build_rubric", build_rubric_mock),
        patch("main._synthesize_graph", synthesize_graph_mock),
    ):
        await main.synthesize(payload)

    rubric_payload = build_rubric_mock.await_args.args[0]
    synthesize_prompt = synthesize_graph_mock.await_args.kwargs["user_prompt"]

    assert rubric_payload.user_prompt.endswith("The user wants to find more similar products.")
    assert "find more similar products" in synthesize_prompt.lower()
    assert payload.user_prompt == "Compare the tennis shoes I viewed"


def test_session_metadata_uses_metrics_and_raw_llm_fields() -> None:
    metadata = main._session_metadata_for_node(
        {
            "priceUsd": 79,
            "combinedScore": 92,
            "aiRank": 1,
            "sourceType": "seed",
            "statusLabel": "captured",
            "kindLabel": "item",
            "constraintViolated": False,
            "metrics": [
                {"label": "Noise Level", "value": "70 dB"},
                {"label": "Laptop Size Compatibility", "value": "15.6 to 19 in"},
            ],
            "rawData": {
                "Noise Level": "70 dB",
                "Laptop Size Compatibility": "15.6 to 19 in",
                "Airflow": "Strong",
            },
        }
    )

    assert metadata["priceUsd"] == 79
    assert metadata["sourceType"] == "seed"
    assert metadata["Noise Level"] == "70 dB"
    assert metadata["Laptop Size Compatibility"] == "15.6 to 19 in"
    assert metadata["Airflow"] == "Strong"
    assert metadata["metrics"][0]["label"] == "Noise Level"
    assert "noiseDisplay" not in metadata
    assert "coolingPerformance" not in metadata


def test_session_metadata_preserves_review_fields_from_llm_output() -> None:
    metadata = main._session_metadata_for_node(
        {
            "sourceType": "seed",
            "statusLabel": "captured",
            "kindLabel": "item",
            "constraintViolated": False,
            "metrics": [
                {"label": "Review Consensus", "value": "Generally positive"},
                {"label": "Common Complaints", "value": "Fan noise at max speed"},
            ],
            "rawData": {
                "Review Consensus": "Generally positive",
                "Common Complaints": "Fan noise at max speed",
            },
        }
    )

    assert metadata["Review Consensus"] == "Generally positive"
    assert metadata["Common Complaints"] == "Fan noise at max speed"
    assert any(metric["label"] == "Review Consensus" for metric in metadata["metrics"])


def test_session_metadata_always_includes_review_consensus_field() -> None:
    metadata = main._session_metadata_for_node(
        {
            "sourceType": "seed",
            "statusLabel": "captured",
            "kindLabel": "item",
            "constraintViolated": False,
            "rawData": {
                "Price": "$39.99",
            },
        }
    )

    assert metadata["Review Consensus"] == "Unknown"


def test_ensure_review_field_in_rubric_inserts_review_consensus() -> None:
    rubric = main.ComparisonRubric(
        domain="products",
        fields=["Price USD", "Noise Level", "Cooling Power"],
        inferred_constraints=[],
        default_ordering="best fit",
        seed_patterns=[],
    )

    ensured = main._ensure_review_field_in_rubric(rubric)

    assert "Review Consensus" in ensured.fields
    assert ensured.fields[1] == "Review Consensus"


def test_ensure_review_field_in_rubric_moves_review_consensus_into_visible_fields() -> None:
    rubric = main.ComparisonRubric(
        domain="products",
        fields=["Price USD", "Noise Level", "Cooling Power", "Build Quality", "Portability", "Review Consensus"],
        inferred_constraints=[],
        default_ordering="best fit",
        seed_patterns=[],
    )

    ensured = main._ensure_review_field_in_rubric(rubric)

    assert ensured.fields[1] == "Review Consensus"
    assert "Review Consensus" in ensured.fields[:5]


def test_build_backend_metrics_prioritizes_review_consensus() -> None:
    metrics = main._build_backend_metrics(
        {
            "Price USD": "$79",
            "Noise Level": "70 dB",
            "Review Consensus": "Mostly positive with some fan-noise complaints",
            "Review Sentiment": "Positive",
            "Cooling Power": "High",
        }
    )

    assert metrics[0]["label"] == "Review Consensus"
    assert all(metric["label"] != "Review Sentiment" for metric in metrics)


def test_session_metadata_drops_review_sentiment_when_consensus_exists() -> None:
    metadata = main._session_metadata_for_node(
        {
            "sourceType": "seed",
            "statusLabel": "captured",
            "kindLabel": "item",
            "constraintViolated": False,
            "metrics": [
                {"label": "Review Consensus", "value": "Mostly positive"},
                {"label": "Review Sentiment", "value": "Positive"},
            ],
            "rawData": {
                "Review Consensus": "Mostly positive",
                "Review Sentiment": "Positive",
            },
        }
    )

    assert metadata["Review Consensus"] == "Mostly positive"
    assert "Review Sentiment" not in metadata
    assert all(metric["label"] != "Review Sentiment" for metric in metadata["metrics"])


def test_filter_graph_for_prompt_does_not_apply_domain_specific_pruning() -> None:
    graph = main.ReactFlowGraphData(
        nodes=[
            main.ReactFlowNode(
                id="concept-node",
                type="research",
                data={
                    "title": "Concept explainer",
                    "sourceType": "discovered",
                    "constraintViolated": False,
                    "constraintReason": "",
                },
                position=main.GraphPosition(x=0, y=0),
            )
        ],
        edges=[],
    )

    filtered = main._filter_graph_for_prompt(graph, "Compare laptop coolers")

    assert len(filtered.nodes) == 1
    assert filtered.nodes[0].id == "concept-node"


def test_filter_graph_for_prompt_drops_inaccessible_or_low_info_nodes() -> None:
    graph = main.ReactFlowGraphData(
        nodes=[
            main.ReactFlowNode(
                id="blocked-node",
                type="research",
                data={
                    "title": "Amazon login wall",
                    "url": "https://example.com/blocked",
                    "summary": "Access denied. Sign in to continue.",
                    "sourceType": "discovered",
                    "constraintViolated": False,
                    "constraintReason": "",
                },
                position=main.GraphPosition(x=0, y=0),
            ),
            main.ReactFlowNode(
                id="good-node",
                type="research",
                data={
                    "title": "Useful cooler",
                    "url": "https://example.com/cooler",
                    "summary": "Reliable cooling pad with clear specs and enough evidence to compare.",
                    "Price": "$79.99",
                    "sourceType": "seed",
                    "constraintViolated": False,
                    "constraintReason": "",
                },
                position=main.GraphPosition(x=320, y=0),
            ),
        ],
        edges=[],
    )

    canonical = main._canonicalize_graph_for_frontend(graph)
    filtered = main._filter_graph_for_prompt(canonical, "Compare laptop coolers")

    assert [node.id for node in filtered.nodes] == ["good-node"]


def test_filter_graph_for_prompt_drops_nodes_missing_most_rubric_fields() -> None:
    graph = main.ReactFlowGraphData(
        rubric_fields=["Price USD", "Review Consensus", "Cushioning Technology", "Stability Features", "Outsole Durability"],
        nodes=[
            main.ReactFlowNode(
                id="sparse-node",
                type="research",
                data={
                    "title": "Sparse shoe",
                    "url": "https://example.com/sparse",
                    "summary": "Has some detail but not enough structured comparison data.",
                    "attributes": [
                        {"label": "Price USD", "value": "$74.95"},
                        {"label": "Review Consensus", "value": "Unknown"},
                        {"label": "Cushioning Technology", "value": "Unknown"},
                        {"label": "Stability Features", "value": "Unknown"},
                        {"label": "Outsole Durability", "value": "Unknown"},
                    ],
                    "sourceType": "seed",
                    "constraintViolated": False,
                    "constraintReason": "",
                },
                position=main.GraphPosition(x=0, y=0),
            ),
            main.ReactFlowNode(
                id="kept-node",
                type="research",
                data={
                    "title": "Covered shoe",
                    "url": "https://example.com/covered",
                    "summary": "Well-covered shoe with enough comparable fields present to keep.",
                    "attributes": [
                        {"label": "Price USD", "value": "$89.95"},
                        {"label": "Review Consensus", "value": "4.4/5 stars from 2,666 reviews"},
                        {"label": "Cushioning Technology", "value": "GEL technology"},
                        {"label": "Stability Features", "value": "TRUSSTIC technology"},
                        {"label": "Outsole Durability", "value": "Synthetic leather toe overlays"},
                    ],
                    "sourceType": "seed",
                    "constraintViolated": False,
                    "constraintReason": "",
                },
                position=main.GraphPosition(x=320, y=0),
            ),
        ],
        edges=[],
    )

    filtered = main._filter_graph_for_prompt(graph, "Compare tennis shoes")

    assert [node.id for node in filtered.nodes] == ["kept-node"]


def test_canonicalize_graph_for_frontend_extracts_price_from_summary_text() -> None:
    graph = main.ReactFlowGraphData(
        nodes=[
            main.ReactFlowNode(
                id="summary-price-node",
                type="research",
                data={
                    "title": "llano V12 Gaming Laptop Cooling Pad",
                    "url": "https://www.amazon.com/dp/B0EXAMPLE96",
                    "summary": "Title: llano V12 | Facts: Price: $96.00 | Cooling performance is strong.",
                    "sourceType": "seed",
                    "constraintViolated": False,
                    "constraintReason": "",
                },
                position=main.GraphPosition(x=0, y=0),
            )
        ],
        edges=[],
    )

    canonical = main._canonicalize_graph_for_frontend(graph)

    assert canonical.nodes[0].data.priceUsd == 96.0


def test_extract_semantic_text_includes_span_based_amazon_price() -> None:
    html = """
    <html>
      <head><title>ASICS Men's Gel-Dedicate 8 Tennis Shoes</title></head>
      <body>
        <h1>ASICS Men's Gel-Dedicate 8 Tennis Shoes</h1>
        <div id="corePriceDisplay_desktop_feature_div">
          <span class="a-price aok-align-center">
            <span class="a-offscreen">$74.95</span>
            <span aria-hidden="true"><span class="a-price-symbol">$</span><span class="a-price-whole">74</span><span class="a-price-decimal">.</span><span class="a-price-fraction">95</span></span>
          </span>
        </div>
      </body>
    </html>
    """

    semantic_text = summarizer._extract_semantic_text(html, 5000)

    assert "ASICS Men's Gel-Dedicate 8 Tennis Shoes" in semantic_text
    assert "$74.95" in semantic_text
