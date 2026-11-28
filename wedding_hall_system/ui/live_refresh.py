"""Login follow-ups and dropdown freshness (A-94/A-95).

Two related problems live here:

1. **Auto-load on login (A-94).** Every tab's list starts empty and
   only fills when the user clicks its refresh button. Builders
   register ``"on_login"`` ``(fn, outputs)`` pairs (mirroring the
   ``"renders"`` pattern) and ``app.py`` chains them with ``.then()``
   after login / password submit / page-load restore. ``login_follow_up``
   wraps any token-only refresh handler so the chain is safe: with no
   valid session — anonymous load, failed login — or when the caller's
   role may not see the list, the outputs are left untouched
   (``gr.update()``) instead of popping an error right after sign-in.
   The manual refresh button still shows the real error when the user
   clicks it. (A-91 introduced the pattern for the halls list only.)

2. **Stale dropdown choices (A-95).** Hall/package/event-type/service
   dropdowns were built once from choices computed at app build, so a
   hall created, deleted or deactivated afterwards never showed up
   (or never disappeared) on the other tabs. ``ChoiceRegistry``
   collects ``(component, choices_fn)`` pairs from every builder —
   one helper per dropdown that rebuilds its current choices — and
   ``make_refresh_choices_handler`` returns a single ``(fn, outputs)``
   pair that refreshes ALL of them. ``app.py`` chains it on login
   (via ``"on_login"``) and after every catalog/operations mutation
   (via the builders' ``"choice_mutation_events"``). Choices only are
   refreshed — a selection the user already made in a form is left
   alone, even if that choice has since disappeared (the service
   still validates it on submit).
"""
from __future__ import annotations

import gradio as gr

from ..utils.exceptions import ApplicationError
from .i18n import lang_for_token


def login_follow_up(auth, fn, name: str, n_outputs: int = 1):
    """Wrap a token-only refresh handler into a login follow-up.

    ``fn(token)`` returns the handler's outputs (one value, or a
    tuple/list for multi-output handlers). The wrapper is
    session-tolerant (no valid session -> bare ``gr.update()`` per
    output) and permission-tolerant (a denied list read leaves the
    outputs untouched instead of raising at login). ``name`` becomes
    the wrapper's ``__name__`` so the handler is findable by api_name
    in tests and over the API — every wrapper must have a distinct
    name, or Gradio deduplicates it (``..._1``).
    """
    def wrapper(token):
        if auth.validate_session(token) is None:
            if n_outputs > 1:
                return (gr.update(),) * n_outputs
            return gr.update()
        try:
            return fn(token)
        except (ApplicationError, gr.Error):
            if n_outputs > 1:
                return (gr.update(),) * n_outputs
            return gr.update()

    wrapper.__name__ = name
    return wrapper


class ChoiceRegistry:
    """(component, choices_fn) pairs whose options must stay fresh.

    ``choices_fn(lang)`` rebuilds the whole option list in the
    requested language — the same shape ``LabelRegistry.dropdown_data``
    uses, so a dropdown_data component can pass its lambda straight
    in. Static dropdowns/checkbox groups register an equivalent
    lambda. The registry is created per ``build_app`` call and filled
    by the builders' ``"choice_entries"`` results; it is never a
    module-level singleton (tests build the app repeatedly).
    """

    def __init__(self) -> None:
        self._entries: list = []

    def register(self, component, choices_fn) -> None:
        self._entries.append((component, choices_fn))

    @property
    def components(self) -> list:
        return [comp for comp, _ in self._entries]

    def updates(self, lang: str) -> list:
        return [gr.update(choices=fn(lang)) for _, fn in self._entries]


def make_refresh_choices_handler(db, auth, registry: ChoiceRegistry):
    """Return the ``(fn, outputs)`` pair that refreshes every
    registered dropdown/checkbox. Session- and permission-tolerant
    like ``login_follow_up``: anonymous loads leave the choices
    untouched. The handler is named ``do_refresh_choices_on_login``
    so it is findable by api_name."""
    def do_refresh_choices_on_login(token):
        if auth.validate_session(token) is None:
            return [gr.update() for _ in registry.components]
        try:
            lang = lang_for_token(db, auth, token)
            return registry.updates(lang)
        except (ApplicationError, gr.Error):
            return [gr.update() for _ in registry.components]

    return do_refresh_choices_on_login, registry.components
