"""Pure height geometry for the normalized, shared module diagram.

The allocated floor area remains a partition of one shared rectangle. Heights
only describe its display extrusion; they never replace stored design efforts.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from decimal import Decimal
from fractions import Fraction

from .core import GranuleError

MAX_HEIGHT_RATIO = 0.35


def _number(value, label):
    if type(value) not in (int, float, Fraction, Decimal):
        raise GranuleError(f"{label} must be a finite number")
    try:
        result = float(value)
    except (ValueError, OverflowError) as exc:
        raise GranuleError(f"{label} must be a finite number") from exc
    if not math.isfinite(result):
        raise GranuleError(f"{label} must be a finite number")
    return result


def _span(span):
    value = _number(span, "Diagram span")
    if value <= 0:
        raise GranuleError("Diagram span must be positive")
    return value


def maximum_height(span):
    """Return a fixed camera envelope independent of current module shares."""
    return _span(span) * MAX_HEIGHT_RATIO


def proportional_heights(shares, span):
    """Return linearly increasing extrusion heights from normalized shares.

    Positive shares must sum to exactly one; an empty or all-zero mapping
    produces no extrusion. A share ``s`` receives ``span * 0.35 * s`` without
    a minimum-height offset. The fixed maximum for a full share is
    ``maximum_height(span)``, allowing the host to fit its camera
    once rather than compensate for a growing module with a shrinking zoom.

    Use the exact ``Fraction`` shares from ``normalized_shares``. Unassigned
    or all-zero efforts have already been identified there as provisional;
    this helper does not resolve efforts, approve allocations or write state.
    """
    size = _span(span)
    if not isinstance(shares, Mapping):
        raise GranuleError("Diagram shares must map module IDs to numbers")
    if any(not isinstance(key, str) or not key.strip() for key in shares):
        raise GranuleError("Diagram module IDs must be non-empty strings")
    exact = {}
    for key in sorted(shares):
        value = shares[key]
        _number(value, "Diagram share")
        exact[key] = value if isinstance(value, Fraction) else Fraction(str(value))
        if not 0 <= exact[key] <= 1:
            raise GranuleError("Diagram shares must be from zero to one")
    if sum(exact.values(), Fraction(0)) not in (0, 1):
        raise GranuleError("Positive diagram shares must sum to one")
    return {
        key: size * MAX_HEIGHT_RATIO * float(share)
        for key, share in exact.items()
    }


def box_vertices(rectangle, height, *, base_z=0.0):
    """Return eight vertices above an XY footprint, or none for no volume.

    The first four vertices trace the ordered bottom perimeter and the next
    four trace the corresponding top perimeter. Extrusion is positive Z;
    a renderer looking from negative Z may reflect that axis consistently.
    Degenerate floor footprints or zero heights have no rendered volume.
    """
    if isinstance(rectangle, (str, bytes)):
        raise GranuleError("Module rectangle must contain four ordered finite coordinates")
    try:
        coordinates = tuple(rectangle)
    except TypeError as exc:
        raise GranuleError("Module rectangle must contain four ordered finite coordinates") from exc
    if len(coordinates) != 4:
        raise GranuleError("Module rectangle must contain four ordered finite coordinates")
    x0, y0, x1, y1 = (_number(value, "Module rectangle coordinate") for value in coordinates)
    if x1 < x0 or y1 < y0:
        raise GranuleError("Module rectangle must contain ordered coordinates")
    extrusion = _number(height, "Module height")
    bottom = _number(base_z, "Module base Z")
    if extrusion < 0:
        raise GranuleError("Module height must be non-negative")
    top = bottom + extrusion
    if not math.isfinite(top):
        raise GranuleError("Module top Z must be finite")
    if x0 == x1 or y0 == y1 or extrusion == 0:
        return ()
    return tuple((x, y, z) for z in (bottom, top)
                 for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1)))
