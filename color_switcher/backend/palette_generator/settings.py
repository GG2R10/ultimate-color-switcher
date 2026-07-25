#!/usr/bin/env python3
"""
settings.py — Persisted palette-generation defaults (config.json's
``palette_generation`` key) plus the shuffle "next" anchor resolution, which
is just bookkeeping over that same persisted state.
"""

from .. import config as config_module

_GENERATION_SETTINGS_KEY = "palette_generation"
DEFAULT_GENERATION_SETTINGS = {
    "mode": "contrast",
    "saturate": False,
    "scoring": "default",
    "custom_percentages": None,
    "weighted_contrast": True,
    "ying_yang": False,
    "shuffle_enabled": False,
    "shuffle_mode": "manual",  # "manual" (uses shuffle_value) or "next" (auto-increments last_shuffle)
    "shuffle_value": 0,
    "overfetch": 0,
    "last_shuffle": -1,  # anchor for shuffle_mode "next" -- see resolve_shuffle_index
}


def read_generation_settings(config) -> dict:
    """Persisted defaults for the GUI's "Generar paleta desde imagen…"
    (mode, saturate). Falls back to DEFAULT_GENERATION_SETTINGS (not
    persisted) when config.json has no palette_generation key."""
    stored = config_module.read_config_json(config).get(_GENERATION_SETTINGS_KEY)
    if stored:
        merged = dict(DEFAULT_GENERATION_SETTINGS)
        merged.update(stored)
        return merged
    return dict(DEFAULT_GENERATION_SETTINGS)


def write_generation_settings(config, settings: dict) -> None:
    """Persist palette-generation defaults into config.json, preserving every other key."""
    raw = config_module.read_config_json(config)
    raw[_GENERATION_SETTINGS_KEY] = settings
    config_module.write_config_json(config, raw)


def resolve_shuffle_index(shuffle, n_colors: int, overfetch: int = 0, config=None) -> int:
    """Turn a `shuffle` choice (a non-negative int, or the literal string
    "next") into the concrete `skip` select_primary needs.

    "next" continues from wherever generation last left off:
    `(last_shuffle + 1) % pool_bound`, where `pool_bound = n_colors +
    overfetch` is the size of the candidate pool the caller asked for (the
    real post-filter pool can end up smaller for a given image --
    select_primary clamps to it, which only means a few different `next`
    values land on the same last candidate before the modulo wraps back to
    0; it can't get "stuck" the way a plain clamp-without-wraparound would).
    This is what makes `ucs ... --shuffle next` useful from a script that
    calls it repeatedly to cycle through variants.

    An explicit int is used as-is (not modulo'd -- `(a+1) % m` and
    `((a % m)+1) % m` agree either way, so a literal "big" value here
    doesn't break a later "next").

    Whenever `config` is given, the resolved value is persisted as the new
    `last_shuffle` anchor -- both for "next" (so the sequence advances) and
    for an explicit int (so a manual override becomes the new anchor
    "next" continues from)."""
    pool_bound = max(n_colors + overfetch, 1)
    if shuffle == "next":
        last = read_generation_settings(config).get("last_shuffle", -1) if config is not None else -1
        resolved = (last + 1) % pool_bound
    else:
        resolved = int(shuffle)
        if resolved < 0:
            raise ValueError(f"shuffle no puede ser negativo: {shuffle!r}")

    if config is not None:
        settings = read_generation_settings(config)
        settings["last_shuffle"] = resolved
        write_generation_settings(config, settings)

    return resolved
