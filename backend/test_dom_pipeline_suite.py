from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

import main


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
