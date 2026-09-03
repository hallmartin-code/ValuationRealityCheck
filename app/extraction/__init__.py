"""Deck-to-facts extraction, and the composition of the normalised company profile.

The model reads the deck exactly once, in `engine`. The three extractor modules
own their field catalogues and the normalisation of their own answers;
`profile_builder` composes them under section 2's precedence order.
"""

from . import (  # noqa: F401
    company_extractor,
    engine,
    financial_extractor,
    financing_extractor,
    profile_builder,
)

extract = engine.extract
build_profile = profile_builder.build
load_comparables = financing_extractor.load_comparables
