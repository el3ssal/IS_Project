"""Gradio theme, custom CSS and live RTL switching (post-Phase 8 polish).

Two pieces live here so the app launcher and the tests share one source
of truth:

1. **Theme** (Ahmed's taste, from memory): a light, eye-comfortable
   warm white — never pure white — with petroleum (teal) as the primary
   accent, indigo-blue as the secondary accent and a thin yellow rule
   as a highlight. Subtle dotted paper background, roomy spacing,
   large radius, large text. Fonts are a system stack with Arabic
   coverage (``Segoe UI`` / ``Noto Kufi Arabic`` / ``Tahoma``) so the
   app still renders fully offline — no Google Fonts download at
   runtime (the venue LAN deployment must not depend on the internet).
   Built on ``gr.themes.Soft`` with ``.set()`` overrides; no new
   dependency.

2. **Live RTL**: Gradio has no ``rtl`` flag — the page direction is
   the ``<html dir>`` attribute, and the app itself renders inside the
   ``<gradio-app>`` shadow DOM, so ``document.getElementById`` cannot
   see its components. The mechanism that works:

   - ``build_app`` renders a hidden ``gr.HTML`` component
     (``elem_id="whms-dir-signal"``) whose value is
     ``dir_signal_html(lang)`` — a ``<span data-lang="ar|en">``.
     Every handler that can change the language (login, remember-me
     restore, the language toggle, logout) updates that component.
   - ``RTL_JS`` (passed to ``launch(js=...)``) runs once on page load,
     finds the signal through ``gradio-app``'s ``shadowRoot``, sets
     ``document.documentElement.dir`` (and ``lang``) from it, and
     attaches a ``MutationObserver`` to the signal element, so every
     later toggle flips the whole page direction live. A short polling
     fallback covers the case where the shadow DOM is not mounted yet
     when the script first runs.

   Direction is inherited through the shadow boundary, so setting it
   on ``<html>`` flips every Gradio component (flex layouts reverse
   automatically). The ``dir_wrap`` helper in ``ui/i18n.py`` still
   wraps dynamic Markdown — it is harmless and stays as a second layer.
"""
from __future__ import annotations

import gradio as gr
from gradio.themes.base import Base as GradioThemeBase

from .i18n import dir_for

# -- palette (Ahmed's taste) -------------------------------------------
# Warm eye-comfortable white, never pure white (#FFFFFF is used nowhere
# as a surface here — primary buttons keep white *text* only).
BODY_BG = "#FAF8F2"
BODY_BG_DARK = "#1C1917"
BLOCK_BG = "#FFFDF8"
BLOCK_BG_DARK = "#292524"
# Petroleum (teal-700) primary, teal-600 on hover, indigo-700 secondary.
PETROLEUM = "#0F766E"
PETROLEUM_HOVER = "#0D9488"
INDIGO = "#4338CA"
# Yellow highlight: focus ring + the top accent rule in APP_CSS.
YELLOW = "#EAB308"
AMBER_FOCUS = "#D97706"


def build_theme() -> GradioThemeBase:
    """The venue theme: Soft base, petroleum/indigo hues, warm whites."""
    return gr.themes.Soft(
        primary_hue="teal",
        secondary_hue="indigo",
        neutral_hue="stone",
        text_size="lg",
        spacing_size="lg",
        radius_size="lg",
        # System stack with Arabic coverage — no runtime font download.
        font=["Segoe UI", "Noto Kufi Arabic", "Tahoma", "Arial",
              "sans-serif"],
        font_mono=["Cascadia Code", "Consolas", "Courier New",
                   "monospace"],
    ).set(
        body_background_fill=BODY_BG,
        body_background_fill_dark=BODY_BG_DARK,
        body_text_color="#292524",
        body_text_color_dark="#FAFAF9",
        # Transparent container so the dotted APP_CSS pattern shows over
        # the body background (gradio-themes skill: this is the endorsed
        # way to make the container itself transparent).
        background_fill_primary="transparent",
        background_fill_primary_dark="transparent",
        block_background_fill=BLOCK_BG,
        block_background_fill_dark=BLOCK_BG_DARK,
        block_border_color="#E7E5E4",
        block_border_color_dark="#44403C",
        button_primary_background_fill=PETROLEUM,
        button_primary_background_fill_hover=PETROLEUM_HOVER,
        button_primary_text_color="#FFFFFF",
        button_primary_border_color=PETROLEUM,
        link_text_color=PETROLEUM,
        link_text_color_hover=PETROLEUM_HOVER,
        checkbox_background_color_selected=PETROLEUM,
        checkbox_border_color_selected=PETROLEUM,
        input_border_color_focus=AMBER_FOCUS,
    )


# -- custom CSS (injected into the <gradio-app> shadow DOM) -------------
# Selectors must live inside the shadow tree: `.gradio-container` works,
# `body` does not (the theme's body_background_fill paints <body>).
APP_CSS = """
/* Dotted paper background: subtle petroleum dots, 26px grid. */
.gradio-container {
  background-image: radial-gradient(rgba(15, 118, 110, 0.10) 1px, transparent 1.3px);
  background-size: 26px 26px;
  box-shadow: inset 0 4px 0 #EAB308;
}
/* The RTL direction signal carries no visible content. */
#whms-dir-signal { display: none !important; }
/* Cleaner venue UI: hide the "Built with Gradio" footer. */
footer { display: none !important; }
"""

# -- live RTL switching ---------------------------------------------------
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
    component. RTL_JS reads ``data-lang`` from it and flips the page."""
    return f'<span data-lang="{lang}" data-dir="{dir_for(lang)}"></span>'


def launch_kwargs() -> dict:
    """Keyword arguments for ``Blocks.launch`` (Gradio 6 moved theme /
    css / js from ``gr.Blocks(...)`` to ``launch()`` — passing them to
    Blocks is a TypeError in 6.32). Used by ``__main__.main``."""
    return {"theme": build_theme(), "css": APP_CSS, "js": RTL_JS}
