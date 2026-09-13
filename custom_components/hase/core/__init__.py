"""Spellbook Engine core.

Everything in this package is deliberately HA-independent: no
`homeassistant` import, anywhere, in any module. It knows how to match
text against a spellbook and how to validate/execute a small catalog of
typed operations against interfaces (TargetResolver, ServiceCaller) it
is handed - never against Home Assistant directly.

The HA-facing side (the conversation.py platform, the real
TargetResolver/ServiceCaller implementations, config_flow/entry
wiring) lives one level up, in custom_components/hase/, and is the
only part of this feature that imports homeassistant.
"""
