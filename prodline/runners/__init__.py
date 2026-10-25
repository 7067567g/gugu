"""Runners (P1.3+).

``accept.py`` is the P1.3 aggregator: it turns receipt files plus the source
evidence into an ``acceptance-report/v1`` document and enforces
``provenance == real`` before a stage can PASS.  ``receipt_check`` lives under
``prodline/gates`` because it is a gate, not an aggregator.

Future runners (soak, etc.) land in the A/B areas; importing this package must
not drag in product code.
"""
