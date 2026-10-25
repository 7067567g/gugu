"""gugu prodline contract layer (P).

``prodline/**`` is classified **P** in ``arch/architecture.yaml``: it runs only
during build / install / acceptance / evidence collection, and product
processes must never import it.

Round P1.1 ships only the contract skeleton:

* ``pipeline.yaml`` -- the five stable stages and the stage -> receipt mapping;
* ``schemas/``     -- the six ``v1`` receipt JSON Schemas.

Stage implementations (``stages/``) land in P1.2 and runners (``runners/``)
land in P1.3.  Keeping this module import-free is intentional: importing the
contract layer must not drag in product code.
"""

from __future__ import annotations

#: Version of the receipt contract carried by this package.  A breaking change
#: to any receipt schema requires a new ``v2`` schema, not an edit to ``v1``.
RECEIPT_CONTRACT_VERSION = 1

__all__ = ["RECEIPT_CONTRACT_VERSION"]
