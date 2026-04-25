import asyncio
import json
import re
from typing import Any

import main


PRICE_MIN = 1000
PRICE_MAX = 1500
EXPECTED_SEED_COUNT = 5
EXPECTED_DISCOVERED_COUNT = 5


def _coerce_number(value: Any) -> float | None:
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        match = re.search(r"(\d+(?:\.\d+)?)", value.replace(",", ""))
        if match:
            return float(match.group(1))
    return None


def _get_data_value(data: dict[str, Any], *keys: str) -> Any:
    normalized = {key.lower().replace(" ", "").replace("_", ""): value for key, value in data.items()}
    for key in keys:
        lookup = key.lower().replace(" ", "").replace("_", "")
        if lookup in normalized:
            return normalized[lookup]
    return None


async def main_async() -> None:
    payload = main.SynthesizeRequest(
        user_prompt=(
            "Find more laptops like these. Infer the constraints from the five seed laptops, "
            "keep those seed laptops in the graph, then discover additional qualifying laptops. "
            "Prioritize performance-focused laptops, prefer the same price band if the pattern is clear, "
            "use recent internet discussions if they add signal, and stop when there is enough evidence "
            "instead of chasing an arbitrary count."
        ),
        firecrawl_query_budget=4,
        active_tabs=[
            main.ActiveTab(
                url="https://example.com/seed-legion",
                summary=(
                    "Lenovo Legion Pro 5i Gen 9. Price $1399. Battery life 5.8 hours. "
                    "HX-class CPU and strong gaming / creator performance."
                ),
            ),
            main.ActiveTab(
                url="https://example.com/seed-tuf",
                summary=(
                    "ASUS TUF Gaming A15. Price $1149. Battery life 6.8 hours. "
                    "Performance-focused gaming laptop with strong value."
                ),
            ),
            main.ActiveTab(
                url="https://example.com/seed-victus",
                summary=(
                    "HP Victus 16. Price $1249. Battery life 6.6 hours. "
                    "Balanced performance laptop for gaming and productivity."
                ),
            ),
            main.ActiveTab(
                url="https://example.com/seed-nitro",
                summary=(
                    "Acer Nitro V 16 AI. Price $1099. Battery life 7.1 hours. "
                    "Budget-friendly performance laptop with gaming focus."
                ),
            ),
            main.ActiveTab(
                url="https://example.com/seed-zephyrus",
                summary=(
                    "ASUS ROG Zephyrus G16. Price $1499. Battery life 7.8 hours. "
                    "Thin high-performance laptop with creator and gaming focus."
                ),
            ),
        ],
    )

    graph = await main.synthesize(payload)

    checks = []
    for node in graph.nodes:
        data = node.data
        price = _coerce_number(_get_data_value(data, "priceUsd", "Price USD", "Price"))
        focus = " ".join(
            str(value or "")
            for value in [
                _get_data_value(data, "title", "Name"),
                _get_data_value(data, "Performance Focus", "performanceFocus"),
                _get_data_value(data, "GPU/CPU", "GPU", "CPU", "CPU Model", "GPU Model"),
                _get_data_value(data, "Constraint Check"),
                _get_data_value(data, "AI Reason", "aiReason"),
                _get_data_value(data, "Discussion Signal"),
            ]
        )
        url = _get_data_value(data, "url", "sourceUrl", "Source URL")
        source_type = str(_get_data_value(data, "sourceType") or "")
        checks.append(
            {
                "id": node.id,
                "title": _get_data_value(data, "title", "Name") or node.id,
                "price": price,
                "source_type": source_type,
                "has_link": bool(url),
                "in_price_range": price is not None and PRICE_MIN <= price <= PRICE_MAX,
                "performance_focused": any(
                    token in focus.lower()
                    for token in ["performance", "gpu", "cpu", "gaming", "creator", "rtx", "render", "hx", "ultra"]
                ),
                "has_discussion_signal": bool(_get_data_value(data, "Discussion Signal")),
            }
        )

    seed_nodes = [check for check in checks if check["source_type"] == "seed"]
    discovered_nodes = [check for check in checks if check["source_type"] == "discovered"]
    qualifying_discovered = [
        check
        for check in discovered_nodes
        if check["in_price_range"] and check["performance_focused"] and check["has_link"]
    ]

    print(
        json.dumps(
            {
                "node_count": len(graph.nodes),
                "seed_count": len(seed_nodes),
                "discovered_count": len(discovered_nodes),
                "qualifying_discovered_count": len(qualifying_discovered),
                "passed": (
                    len(seed_nodes) >= EXPECTED_SEED_COUNT
                    and len(qualifying_discovered) >= EXPECTED_DISCOVERED_COUNT
                ),
                "checks": checks,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    asyncio.run(main_async())
