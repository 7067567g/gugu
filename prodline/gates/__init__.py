"""P-layer gates (P1.3).

``receipt_check`` validates receipt documents and their sha256 chain;
``arch_boundaries`` consumes the existing arch-gate's JSON receipt and turns
first-party parse errors into a gate failure (F2).  Neither module is imported
by product code.
"""

from __future__ import annotations

__all__: list[str] = []
