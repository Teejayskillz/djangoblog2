"""
Test-only compatibility shim (never imported by the running application).

Python 3.14 changed behaviour that Django 5.0's test client depends on:
``BaseContext.__copy__`` uses ``copy(super())``, which produces a broken object,
so ``response.context`` handling raises
``AttributeError: 'super' object has no attribute 'dicts'``.

We replace the copy with an explicit one that rebuilds the **real subclass**
(``RenderContext`` must stay a ``RenderContext`` or ``push_state`` disappears --
that regression was caught by ``core.test_comments``).

Loaded from ``manage.py`` only when the command is ``test`` so it cannot affect
production, which runs Python 3.11 where none of this applies.
"""

from __future__ import annotations

import sys

APPLIED = False


def apply() -> bool:
    global APPLIED
    if APPLIED or sys.version_info < (3, 14):
        return False

    from django.template.context import BaseContext

    def _patched_context_copy(self):
        duplicate = self.__class__.__new__(self.__class__)
        duplicate.__dict__.update(self.__dict__)
        duplicate.dicts = [mapping.copy() for mapping in self.dicts]
        return duplicate

    BaseContext.__copy__ = _patched_context_copy
    APPLIED = True
    return True


apply()
