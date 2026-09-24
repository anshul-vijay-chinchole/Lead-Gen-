"""Base class for contact finders.

``find(company)`` returns candidate decision-makers at ``company`` (it must not
mutate the company). Use the playbook's ``buyers.titles`` / ``seniorities`` /
``departments`` as search hints. Return people WITHOUT email too if the
provider knows the name/title but not the address (the ``pattern`` finder or a
later finder can fill it). ``Contact.source`` must be set to ``self.name``.
"""
from __future__ import annotations

from typing import List

from ..context import Adapter
from ..models import Company, Contact


class ContactFinder(Adapter):
    name = "finder"

    def find(self, company: Company) -> List[Contact]:  # pragma: no cover - interface
        raise NotImplementedError

    def complete(self, company: Company, contact: Contact) -> Contact:
        """Optionally fill in a missing email for a known person. Default: unchanged."""
        return contact
