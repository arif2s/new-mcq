"""
Exposes layout profiles and policies, dynamically linking to parsers.indra.profiles.
"""
from parsers.indra.profiles import (
    ColumnMode,
    CoordinateZone,
    FurnitureSuppressionPolicy,
    HeadingWeightPolicy,
    LAYOUT_PROFILES,
    LayoutProfile,
    PageGeometryPolicy,
    StrategyKey,
)

__all__ = [
    "ColumnMode",
    "CoordinateZone",
    "FurnitureSuppressionPolicy",
    "HeadingWeightPolicy",
    "LAYOUT_PROFILES",
    "LayoutProfile",
    "PageGeometryPolicy",
    "StrategyKey",
]
