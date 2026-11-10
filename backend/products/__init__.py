"""Structured product payoff library."""

from .registry import price_product, PRODUCT_TYPES
from .universal_exotic import (
    UniversalExoticProduct,
    get_preset_product,
    list_presets,
    price_universal,
)

__all__ = [
    "price_product",
    "PRODUCT_TYPES",
    "UniversalExoticProduct",
    "get_preset_product",
    "list_presets",
    "price_universal",
]
