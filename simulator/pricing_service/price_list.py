"""Static price list for the simulated pricing service."""

SKU_PRICES: dict[str, float] = {
    "SKU-1001": 19.99,
    "SKU-1002": 49.50,
    "SKU-1003": 5.25,
    "SKU-1004": 120.00,
}


def get_price(sku: str) -> float:
    try:
        return SKU_PRICES[sku]
    except KeyError:
        raise KeyError(f"unknown sku {sku}") from None


def price_order(order: dict) -> float:
    """Validate a queued order against the current price list and return its total.

    Raises ValueError if the sku is unknown or the price the order service
    quoted no longer matches (price drift between services).
    """
    sku = order["sku"]
    try:
        current = get_price(sku)
    except KeyError as exc:
        raise ValueError(str(exc)) from exc
    if abs(current - order["quoted_price"]) > 0.001:
        raise ValueError(f"price mismatch for {sku}: quoted {order['quoted_price']}, current {current}")
    return round(current * order["quantity"], 2)
