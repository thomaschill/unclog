"""Per-finding-type detector modules.

Each module exports a single ``detect(state, activity, thresholds, *, now)``
function returning a ``list[Finding]``. The top-level
:func:`unclog.findings.detect` runs them in order.
"""
