"""Documented shape of the message order-service publishes to the orders queue.

Used to generate the contract knowledge document. It describes what the consumer
(simulator/pricing_service) requires; tests/test_knowledge_loader.py checks it against a real body.
"""

from pydantic import BaseModel, Field


class OrderMessage(BaseModel):
    """One order on the `orders` queue (JSON, persistent delivery)."""

    order_id: str = Field(description="UUID assigned by order-service at submission")
    sku: str = Field(description="Item being ordered; must exist in the pricing service's price list")
    quantity: int = Field(gt=0, description="Number of units")
    customer_id: str
    quoted_price: float = Field(description="Unit price pricing-service quoted at submission; the consumer re-checks it")
