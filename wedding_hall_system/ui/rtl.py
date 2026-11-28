"""Live RTL switching for the bilingual UI (post-Phase 8, reverted polish).

The app is fully bilingual (Arabic default) but Gradio has no ``rtl``
flag, so the whole-page direction must be driven from a signal the
server controls. Two mechanisms live here so the app launcher and the
tests share one source of truth:

1. **Primary — pure CSS.** ``build_app`` renders a hidden ``gr.HTML``
   direction signal (``elem_id="whms-dir-signal"``) whose value is
   ``dir_signal_html(lang)`` — a ``<span data-lang="ar|en">``. Every
   handler that can change the language (login, remember-me restore,
   the language toggle, logout) updates that component. ``RTL_CSS``
   (passed to ``launch(css=...)``) flips the direction of the app
   container with ``:has()`` on the signal, and (A-88) moves Gradio's
   floating form labels to the right in Arabic — the container flip
   alone leaves them pinned to the left. No colors, no background,
   no footer rules: the app wears Gradio's default theme, so the
   "Built with Gradio" footer and its light/dark settings gear stay
   visible (A-85).

   Why CSS, not JS: the first attempt drove direction from
   ``RTL_JS`` alone (a ``MutationObserver`` through the
   ``<gradio-app>`` shadow DOM setting ``<html dir>``). The script
   was present in the served ``/config`` and the toggle flipped the
   signal server-side, yet direction did NOT flip in the user's real
   browser — while the CSS injection channel demonstrably worked
   (the old theme's yellow top rule rendered). The JS stays only as
   a secondary belt-and-braces; the CSS carries the fix (A-86).

2. **Secondary — ``RTL_JS``.** Runs once on page load (``launch(js=...)``),
   finds the signal through ``gradio-app``'s ``shadowRoot``, sets
   ``document.documentElement.dir`` (and ``lang``) from it, and
   attaches a ``MutationObserver`` so every later toggle flips the
   whole page direction live. A short polling fallback covers the
   case where the shadow DOM is not mounted yet when the script
   first runs.

Direction is an inherited CSS property, so setting it on
``.gradio-container`` flips every Gradio component inside it (flex
layouts reverse automatically). The ``dir_wrap`` helper in
``ui/i18n.py`` still wraps dynamic Markdown — it is harmless and
stays as a second layer.

A-88: the container flip does NOT move Gradio's floating form
labels. The frontend CSS pins them to the left
(``label.float { left: var(--block-label-margin); position: absolute
}``) and the ``<label data-testid="block-label">`` element itself
carries ``dir="ltr"`` from the component's ``rtl`` prop (default
``False`` — the app never sets it), so every Textbox/Dropdown/
Number/Checkbox label stays left-aligned in Arabic even though the
page is RTL. ``RTL_CSS`` therefore also carries label rules scoped
under the same ``ar`` signal: they mirror Gradio's own
``label[dir=rtl]`` treatment (position the chip on the right,
``direction: rtl``, ``text-align: right``), and right-align text in
text-like inputs. Number inputs are deliberately excluded — digits
and spinners stay LTR.
"""
from __future__ import annotations

from .i18n import dir_for

# -- direction CSS (injected into the <gradio-app> shadow DOM) ----------
# Selectors must live inside the shadow tree: `.gradio-container`
# works, `body` / `html` do not. The signal span itself is empty, so
# it renders nothing visible; no rule hides it.
# Two direction rules + the A-88 form-label/input rules below — still
# no colors, no background, no footer rules: the app wears Gradio's
# default theme, so the "Built with Gradio" footer and its light/dark
# settings gear stay visible (A-85).
RTL_CSS = """
.gradio-container:has(#whms-dir-signal [data-lang="ar"]) { direction: rtl; }
.gradio-container:has(#whms-dir-signal [data-lang="en"]) { direction: ltr; }
.gradio-container:has(#whms-dir-signal [data-lang="ar"]) label[data-testid="block-label"] { direction: rtl; text-align: right; border: var(--block-label-border-width) solid var(--block-label-border-color); border-bottom-left-radius: var(--block-radius); border-bottom-right-radius: var(--block-label-radius); border-top-left-radius: var(--block-label-radius); border-top: none; border-right: none; }
.gradio-container:has(#whms-dir-signal [data-lang="ar"]) label[data-testid="block-label"].float { left: auto; right: var(--block-label-margin); }
.gradio-container:has(#whms-dir-signal [data-lang="ar"]) label[data-testid="block-label"]:not(.float) { margin-left: 0; margin-right: var(--block-label-margin); }
.gradio-container:has(#whms-dir-signal [data-lang="ar"]) label[data-testid="block-label"] span { margin-right: 0; margin-left: var(--size-2); }
.gradio-container:has(#whms-dir-signal [data-lang="ar"]) input[type="text"], .gradio-container:has(#whms-dir-signal [data-lang="ar"]) input[type="password"], .gradio-container:has(#whms-dir-signal [data-lang="ar"]) input[type="email"], .gradio-container:has(#whms-dir-signal [data-lang="ar"]) textarea { text-align: right; }
"""

# -- live RTL switching (secondary to the CSS above) ----------------------
# Runs once on page load (launch(js=...)). The signal element lives
# inside the <gradio-app> shadow DOM, so plain getElementById cannot
# find it — go through shadowRoot, and poll briefly if the app is not
# mounted yet when the script first runs.
RTL_JS = """() => {
  const applyDir = (lang) => {
    if (!lang) return;
    document.documentElement.setAttribute("dir", lang === "ar" ? "rtl" : "ltr");
    document.documentElement.setAttribute("lang", lang);
  };
  const findSignal = () => {
    const app = document.querySelector("gradio-app");
    const root = app && app.shadowRoot;
    return root ? root.getElementById("whms-dir-signal") : null;
  };
  const readLang = (el) => {
    const span = el ? el.querySelector("[data-lang]") : null;
    return span ? span.getAttribute("data-lang") : null;
  };
  const wire = () => {
    const el = findSignal();
    if (!el) return false;
    applyDir(readLang(el));
    new MutationObserver(() => applyDir(readLang(el))).observe(el, {
      childList: true, subtree: true, characterData: true, attributes: true,
    });
    return true;
  };
  if (!wire()) {
    const timer = setInterval(() => { if (wire()) clearInterval(timer); }, 300);
    setTimeout(() => clearInterval(timer), 20000);
  }
}"""


def dir_signal_html(lang: str) -> str:
    """Hidden direction signal: the value of the ``whms-dir-signal``
    component. RTL_CSS (and RTL_JS) read ``data-lang`` from it and flip
    the page direction."""
    return f'<span data-lang="{lang}" data-dir="{dir_for(lang)}"></span>'


def launch_kwargs() -> dict:
    """Keyword arguments for ``Blocks.launch`` (Gradio 6 moved css /
    js from ``gr.Blocks(...)`` to ``launch()`` — passing them to
    Blocks is a TypeError in 6.32). No ``theme`` key: the app uses
    Gradio's default theme, which keeps the "Built with Gradio"
    footer and its light/dark settings gear (A-85). Used by
    ``__main__.main``."""
    return {"css": RTL_CSS, "js": RTL_JS}
