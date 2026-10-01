"""Exact display shares and a compact, shared rectangular module partition.

Display shares never replace the independently saved design efforts.  An
unassigned, applicable effort uses an explicitly provisional 0.50 weight;
all-zero applicable weights use an explicitly provisional equal partition.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from decimal import Decimal
from fractions import Fraction

from .core import GranuleError, _design_effort_units

PERCENT_UNITS = 10_000
PLACEHOLDER_EFFORT_UNITS = 50


def _module_ids(mapping):
    if not isinstance(mapping, Mapping):
        raise GranuleError("Diagram values must be a mapping of module IDs")
    ids = list(mapping)
    if any(not isinstance(key, str) or not key.strip() for key in ids):
        raise GranuleError("Diagram module IDs must be non-empty strings")
    return tuple(sorted(ids))


def normalized_shares(efforts, applicable=None):
    """Return exact visual shares, rounded labels, and provisional indicators.

    ``shares`` contains ``Fraction`` values with a sum of one whenever at least
    one module is applicable. ``percent_units`` uses hundredths of a percent
    and largest remainders, so labels sum to exactly 100.00%. Ties use module
    ID order. ``weights`` are validated effort hundredths, including the 50
    placeholder for unassigned applicable modules and zero for inapplicable
    modules. ``provisional`` identifies unassigned applicable IDs; ``zero_total``
    flags the equal-area preview when all applicable weights are explicitly
    zero. No-applicable and empty diagrams have no area or implied allocation.

    Input values are JSON numbers on the existing 0.01 design-effort grid, or
    ``None``. This helper never mutates its inputs or writes a human allocation.
    """
    ids = _module_ids(efforts)
    if applicable is None:
        active = set(ids)
    else:
        if isinstance(applicable, (str, bytes)):
            raise GranuleError("Applicable modules must be a collection of module IDs")
        try:
            active = set(applicable)
        except TypeError as exc:
            raise GranuleError("Applicable modules must be a collection of module IDs") from exc
        if any(not isinstance(key, str) or key not in efforts for key in active):
            raise GranuleError("Applicable modules must refer to existing module IDs")

    weights, provisional = {}, []
    for key in ids:
        value = efforts[key]
        units = PLACEHOLDER_EFFORT_UNITS if value is None else _design_effort_units(value)
        weights[key] = units if key in active else 0
        if value is None and key in active:
            provisional.append(key)
    total = sum(weights.values())
    zero_total = bool(active) and total == 0
    shares = {
        key: (Fraction(1, len(active)) if zero_total and key in active else
              Fraction(weights[key], total) if total else Fraction(0))
        for key in ids
    }
    percent_units = {key: (shares[key] * PERCENT_UNITS).__floor__() for key in ids}
    remaining = (PERCENT_UNITS if active else 0) - sum(percent_units.values())
    by_remainder = sorted(ids, key=lambda key: (-(shares[key] * PERCENT_UNITS - percent_units[key]), key))
    for key in by_remainder[:remaining]:
        percent_units[key] += 1
    return {"shares": shares, "percent_units": percent_units, "weights": weights,
            "provisional": tuple(provisional), "zero_total": zero_total,
            "applicable": tuple(sorted(active))}


def _fraction(value):
    if isinstance(value, Fraction):
        return value
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise GranuleError("Diagram shares must be finite non-negative numbers")
        return Fraction(value)
    if type(value) not in (int, float) or not math.isfinite(value):
        raise GranuleError("Diagram shares must be finite non-negative numbers")
    return Fraction(str(value))


def treemap_rectangles(shares, width, height):
    """Partition one rectangle into deterministic, proportional module cells.

    Positive shares must sum to exactly one. A balanced binary partition cuts
    the longest side at each step, creating compact rectangles without gaps or
    overlap. Coordinates are calculated with ``Fraction`` before conversion to
    ordinary drawing floats. Zero-share IDs receive distinct, zero-area anchors
    along the bottom boundary, suitable for separate selectable UI markers;
    such markers must not be presented as an allocated module area.
    """
    ids = _module_ids(shares)
    for label, value in (("width", width), ("height", height)):
        if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
            raise GranuleError(f"Diagram {label} must be a finite positive number")
    values = {key: _fraction(shares[key]) for key in ids}
    if any(value < 0 or value > 1 for value in values.values()):
        raise GranuleError("Diagram shares must be from zero to one")
    total = sum(values.values(), Fraction(0))
    if total not in (0, 1):
        raise GranuleError("Positive diagram shares must sum to one")
    width_q, height_q = Fraction(str(width)), Fraction(str(height))
    rectangles = {}

    def partition(keys, x0, y0, x1, y1):
        if len(keys) == 1:
            rectangles[keys[0]] = (x0, y0, x1, y1)
            return
        whole = sum((values[key] for key in keys), Fraction(0))
        prefix, best = Fraction(0), None
        for index, key in enumerate(keys[:-1], 1):
            prefix += values[key]
            candidate = (abs(2 * prefix - whole), index, prefix)
            if best is None or candidate < best:
                best = candidate
        _, index, first = best
        ratio = first / whole
        if x1 - x0 >= y1 - y0:
            edge = x0 + (x1 - x0) * ratio
            partition(keys[:index], x0, y0, edge, y1)
            partition(keys[index:], edge, y0, x1, y1)
        else:
            edge = y0 + (y1 - y0) * ratio
            partition(keys[:index], x0, y0, x1, edge)
            partition(keys[index:], x0, edge, x1, y1)

    positive = sorted((key for key in ids if values[key]), key=lambda key: (-values[key], key))
    if positive:
        partition(positive, Fraction(0), Fraction(0), width_q, height_q)
    zeros = [key for key in ids if not values[key]]
    for index, key in enumerate(zeros):
        x = width_q * Fraction(2 * index + 1, 2 * len(zeros))
        rectangles[key] = (x, height_q, x, height_q)
    return {key: tuple(float(value) for value in rectangles[key]) for key in ids}
