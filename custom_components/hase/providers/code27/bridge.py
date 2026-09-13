"""Optional bridge: forward codeword-prefixed CODE27 chat messages to Home
Assistant's own Assist conversation agent, and relay its answer back into
the character's chat.

CODE27 has no function-calling/tool system of its own that we found in its
local API, so this is intentionally *not* trying to teach the character to
"call" Home Assistant directly. Instead it leans entirely on Assist, which
already understands your entities: we just pass it the text after the
codeword and read back whatever it says it did.

Every step is written both to the integration's logger (Settings -> System
-> Logs) and as a Logbook entry on the device's own "Activity" feed, so the
whole codeword -> Assist -> reply chain is debuggable from the UI.
"""
from __future__ import annotations

import asyncio
import difflib
import logging
import re
from contextvars import ContextVar
from dataclasses import dataclass

from homeassistant.components import conversation
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Context, Event, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import intent

from . import spellbook
from .frontend import Code27FrontendAdapter
from ...const import (DOMAIN)
from .const import (CONF_BRIDGE_ACK_PHRASE, CONF_BRIDGE_AGENT_ID, CONF_BRIDGE_AI_CLEANUP_ENABLED, CONF_BRIDGE_AI_TASK_ENTITY_ID, CONF_BRIDGE_CODEWORD, CONF_BRIDGE_ENABLED, CONF_BRIDGE_ENTITY_CORRECTION_ENABLED, CONF_BRIDGE_HISTORY_CLEANUP_ENABLED, CONF_BRIDGE_RELAY_RESPONSE, CONF_BRIDGE_SPELLBOOK, CONF_BRIDGE_SPELLBOOK_MODE, CONF_BRIDGE_TTS_ENABLED, CONF_BRIDGE_VACUUM_ENABLED, CONF_MACHINE_ID, CORRECTION_TIMEOUT_SECONDS, DEFAULT_SPELLBOOK_MODE, SPELLBOOK_MODE_STRICT)
from ...core.models import ExecutionStatus
from .coordinator import EVENT_USER_MESSAGE, Code27Coordinator, PendingCorrection

_LOGGER = logging.getLogger(__name__)

LOGBOOK_NAME = "CODE27 Assist-Bridge"


@dataclass(frozen=True)
class BridgeRequest:
    """Immutable origin of one command, captured before it enters the queue."""

    entry_id: str
    slot: int
    language: str
    # Only set when this request came from EVENT_USER_MESSAGE (a real
    # device chat turn) - None for anything synthesized without one (e.g.
    # a correction-loop follow-up). Used solely by the optional
    # CONF_BRIDGE_HISTORY_CLEANUP_ENABLED cleanup below. chat_id (not
    # history_id) is what's used to look the message back up later - see
    # Code27Coordinator.async_cleanup_assist_exchange for why.
    chat_id: str | None = None
    created_at: str | None = None


_REQUEST: ContextVar[BridgeRequest | None] = ContextVar("code27_request", default=None)
# Chat-relay writes (CONF_BRIDGE_RELAY_RESPONSE) made while processing the
# current request, collected only when history-cleanup is enabled so that
# _process_bridge_message can hand them to
# Code27Coordinator.async_cleanup_assist_exchange afterwards - see
# _output_speech.
_RELAY_CHAT_IDS: ContextVar[list[str] | None] = ContextVar("code27_relay_chat_ids", default=None)
# Set to True by _forward_to_assist/_handle_vacuum_clean_action/
# _handle_light_all_action - the three points a codeword/spellbook match
# actually turns into a real command attempt (success or not). Plain chat
# that never matches anything never reaches any of them, so this is how
# _process_bridge_message tells "a command was recognized" (clean up the
# exchange) apart from "just conversation" (never touch it).
_COMMAND_RECOGNIZED: ContextVar[bool] = ContextVar("code27_command_recognized", default=False)
_AI_CLEANUP_TIMEOUT = 15.0


def _capture_request(hass, entry, coordinator, event_data=None) -> BridgeRequest:
    data = event_data or {}
    slot = data.get("slot")
    if not isinstance(slot, int):
        slot = coordinator.data.current_slot.get("slot_number", 1)
    language = data.get("language") or coordinator.data.system.get("ai_language")
    return BridgeRequest(
        entry.entry_id,
        slot,
        language or hass.config.language or "en",
        chat_id=data.get("chat_id"),
        created_at=data.get("created_at"),
    )


def _current_request(hass, entry, coordinator) -> BridgeRequest:
    request = _REQUEST.get()
    if request is not None and request.entry_id == entry.entry_id:
        return request
    return _capture_request(hass, entry, coordinator)

_MESSAGES = {
    "de": {
        "ack_detected": "Quittier-Phrase erkannt, Hinweis wird quittiert",
        "ack_fuzzy_detected": 'Quittier-Phrase ungefähr erkannt („{matched}" ähnlich zu „{phrase}"), Hinweis wird quittiert',
        "ack_none_active": "Quittier-Phrase erkannt, aber kein aktiver Hinweis",
        "codeword_detected": 'Codewort erkannt, Befehl: „{command}"',
        "codeword_fuzzy_detected": 'Codewort ungefähr erkannt („{matched}" ähnlich zu „{codeword}"), Befehl: „{command}"',
        "sent_to_assist": 'An Assist gesendet: „{command}“',
        "assist_error": "Fehler bei Assist-Anfrage: {error}",
        "assist_reply": 'Assist-Antwort: „{speech}“',
        "no_reply": "Assist hat keine Textantwort geliefert",
        "chat_write_failed": "Konnte Antwort nicht in Chat schreiben: {error}",
        "chat_write_ok": "Antwort in Chat gesendet",
        "history_cleanup_ok": "Assist-Kommunikation aus Historie entfernt ({deleted}/{expected} Nachrichten)",
        "history_cleanup_failed": "Entfernen aus Historie fehlgeschlagen: {error}",
        "tts_no_voice": "Keine Stimme für den aktiven Charakter gefunden, TTS übersprungen",
        "tts_error": "Fehler beim Starten der Sprachausgabe: {error}",
        "tts_speaking": "Antwort wird laut ausgesprochen …",
        "tts_ok": "Sprachausgabe abgeschlossen",
        "tts_failed": "Sprachausgabe fehlgeschlagen",
        "tts_timeout": "Sprachausgabe-Status unklar (Zeitlimit erreicht)",
        "filler_stripped": 'Floskeln entfernt: „{command}"',
        "ai_cleanup_result": 'KI-Bereinigung: „{command}“',
        "ai_cleanup_error": "KI-Bereinigung fehlgeschlagen, nutze Original: {error}",
        "ai_cleanup_discarded": 'KI-Ergebnis „{ai_result}“ hat keinen Bezug zur Eingabe, verworfen',
        "spellbook_detected": 'Spellbook-Trigger erkannt ({book}): „{trigger}" → „{command}"',
        "spellbook_fuzzy_detected": (
            'Spellbook-Trigger ungefähr erkannt ({book}, „{matched}" ähnlich zu „{trigger}"): '
            '„{command}"'
        ),
        "spellbook_catchphrase_used": (
            'Catchphrase gesprochen: „{catchphrase}" (Assist-Antwort war: „{original}")'
        ),
        "correction_asked": 'Frage nach Korrektur: „Meintest du {candidate}?"',
        "correction_ask": 'Entschuldigung, meinst du vielleicht „{candidate}"?',
        "correction_retry": 'Korrektur-Versuch mit „{candidate}": „{command}"',
        "correction_cancelled": "Korrektur-Dialog abgebrochen",
        "correction_timed_out": "Korrektur-Dialog ohne Antwort abgelaufen",
        "correction_gave_up": "Keine weiteren passenden Kandidaten gefunden, Korrektur beendet",
        "correction_which_device": "Welches Gerät meinst du?",
        "spell_choice_ambiguous": (
            'Zwei gegensätzliche Sprüche passen etwa gleich gut '
            '(„{trigger_a}" vs. „{trigger_b}"), frage lieber nach statt zu raten'
        ),
        "spell_choice_ask": 'Meinst du „{trigger_a}" oder „{trigger_b}"?',
        "spell_choice_picked": 'Als „{trigger}" verstanden: „{command}"',
        "vacuum_action_disabled": "Vacuum-Befehl erkannt, aber die Sauger-Integration ist in den Optionen deaktiviert",
        "vacuum_area_unresolved": 'Bereich „{area}" ist in Home Assistant nicht bekannt, Saug-Befehl verworfen',
        "vacuum_no_match": 'Kein Saugroboter-Raum passend zu „{area}" gefunden, Saug-Befehl verworfen',
        "vacuum_ambiguous": 'Mehrere Saugroboter-Räume passend zu „{area}" gefunden ({candidates}), Saug-Befehl verworfen',
        "vacuum_map_switch": 'Wechsle Karte von {vacuum} auf „{map}"',
        "vacuum_cleaning_started": 'Reinigung von „{room}" gestartet ({vacuum})',
        "vacuum_error": "Fehler beim Ansteuern des Saugroboters: {error}",
        "light_all_done": "Alle Lichter {state} geschaltet",
        "light_all_error": "Fehler beim Schalten aller Lichter: {error}",
    },
    "en": {
        "ack_detected": "Acknowledge phrase detected, clearing the notification",
        "ack_fuzzy_detected": 'Acknowledge phrase approximately detected ("{matched}" close to "{phrase}"), clearing the notification',
        "ack_none_active": "Acknowledge phrase detected, but there was no active notification",
        "codeword_detected": 'Codeword detected, command: "{command}"',
        "codeword_fuzzy_detected": 'Codeword approximately detected ("{matched}" close to "{codeword}"), command: "{command}"',
        "sent_to_assist": 'Sent to Assist: "{command}"',
        "assist_error": "Error asking Assist: {error}",
        "assist_reply": 'Assist reply: "{speech}"',
        "no_reply": "Assist did not return a text reply",
        "chat_write_failed": "Could not write the reply into the chat: {error}",
        "chat_write_ok": "Reply sent into the chat",
        "history_cleanup_ok": "Removed Assist communication from history ({deleted}/{expected} messages)",
        "history_cleanup_failed": "Could not remove from history: {error}",
        "tts_no_voice": "No voice found for the active character, skipping TTS",
        "tts_error": "Error starting text-to-speech: {error}",
        "tts_speaking": "Speaking the reply out loud...",
        "tts_ok": "Speech playback finished",
        "tts_failed": "Speech playback failed",
        "tts_timeout": "Speech playback status unclear (deadline reached)",
        "filler_stripped": 'Filler words removed: "{command}"',
        "ai_cleanup_result": 'AI cleanup: "{command}"',
        "ai_cleanup_error": "AI cleanup failed, using original: {error}",
        "ai_cleanup_discarded": 'AI result "{ai_result}" is unrelated to the input, discarded',
        "spellbook_detected": 'Spellbook trigger detected ({book}): "{trigger}" -> "{command}"',
        "spellbook_fuzzy_detected": (
            'Spellbook trigger approximately detected ({book}, "{matched}" close to '
            '"{trigger}"): "{command}"'
        ),
        "spellbook_catchphrase_used": (
            'Spoke the catchphrase: "{catchphrase}" (Assist\'s reply was: "{original}")'
        ),
        "correction_asked": 'Asking for correction: "Did you mean {candidate}?"',
        "correction_ask": 'Sorry, do you perhaps mean "{candidate}"?',
        "correction_retry": 'Retrying with "{candidate}": "{command}"',
        "correction_cancelled": "Correction dialog cancelled",
        "correction_timed_out": "Correction dialog timed out with no reply",
        "correction_gave_up": "No further matching candidates, correction ended",
        "correction_which_device": "Which device do you mean?",
        "spell_choice_ambiguous": (
            'Two opposite-action spells fit about equally well '
            '("{trigger_a}" vs. "{trigger_b}"), asking instead of guessing'
        ),
        "spell_choice_ask": 'Did you mean "{trigger_a}" or "{trigger_b}"?',
        "spell_choice_picked": 'Understood as "{trigger}": "{command}"',
        "vacuum_action_disabled": "Vacuum command detected, but the vacuum integration is disabled in the options",
        "vacuum_area_unresolved": 'Area "{area}" is not known to Home Assistant, discarding the vacuum command',
        "vacuum_no_match": 'No vacuum room matching "{area}" found, discarding the vacuum command',
        "vacuum_ambiguous": 'Multiple vacuum rooms matching "{area}" found ({candidates}), discarding the vacuum command',
        "vacuum_map_switch": 'Switching {vacuum}\'s map to "{map}"',
        "vacuum_cleaning_started": 'Started cleaning "{room}" ({vacuum})',
        "vacuum_error": "Error commanding the vacuum: {error}",
        "light_all_done": "Switched all lights {state}",
        "light_all_error": "Error switching all lights: {error}",
    },
}

# Stage 1: cheap, deterministic filler-phrase stripping. Handles the common
# "can you please ask it to ..." pattern that confuses Assist's own NLU
# (which expects a fairly direct imperative, not a meta-request). Repeated
# until nothing more matches, since these can stack ("bitte" + "fragen,").
_FILLER_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"^(du,?\s+)?sag(e)?\s+mal,?\s+",
        r"^(kannst|könntest|würdest)\s+du\s+(bitte\s+)?(vielleicht\s+|eventuell\s+)?",
        r"^(vielleicht\s+|eventuell\s+)?(bitte\s+)?(fragen|frage|frag)\s*,?\s*(ob\s+|dass\s+)?(er\s+|sie\s+|es\s+)?(vielleicht\s+|eventuell\s+)?",
        r"^(sage|sag)\s+(ihm|ihr|ihnen)?\s*,?\s*(dass\s+|ob\s+)?",
        r"^(bitte,?\s+)?(er|sie)\s+soll(te)?\s+",
        r"^(can|could|would)\s+you\s+(maybe\s+|possibly\s+)?(please\s+)?(ask|tell)\s+(it\s+to\s+|to\s+|if\s+it\s+(would|could)\s+(be\s+so\s+kind\s+as\s+to\s+)?)?",
        r"^(please\s+)?(ask|tell)\s+(it\s+)?(to\s+|that\s+)?",
        r"^(vielleicht|eventuell)\s+",
        r"^bitte\s+",
        r"^please\s+",
        r"^,\s*",
        r"\s+für\s+mich\??$",
        r"\s+for\s+me\??$",
        r"\s*,?\s*wenn\s+(er|sie|es)\s+so\s+nett\s+ist\??$",
    )
]


def _strip_filler(text: str) -> str:
    stripped = text
    for _ in range(4):  # a handful of passes is plenty; avoid infinite loops
        changed = False
        for pattern in _FILLER_PATTERNS:
            new_text = pattern.sub("", stripped, count=1)
            if new_text != stripped:
                stripped = new_text
                changed = True
        if not changed:
            break
    return stripped.strip(" ,:.-!–—") or text


_STOPWORDS = {
    "der", "die", "das", "den", "dem", "des", "ein", "eine", "einen", "einem",
    "und", "auf", "im", "in", "am", "an", "zu", "bitte", "mal",
    "the", "a", "an", "to", "and", "on", "in", "at", "of", "please",
}


def _shares_words(original: str, cleaned: str) -> bool:
    """Guardrail against a small local model going off the rails and
    echoing one of its own few-shot examples instead of actually
    transforming the input (observed in practice: a completely unrelated
    command came back from the AI Task). If the AI's output doesn't share
    a single meaningful word with what went in, it's almost certainly not
    a rewrite of that input - reject it rather than risk sending a
    plausible-looking but wrong command to Assist."""
    orig_words = {
        w for w in re.findall(r"\w+", original.lower()) if len(w) > 2 and w not in _STOPWORDS
    }
    if not orig_words:
        return True
    cleaned_words = {
        w for w in re.findall(r"\w+", cleaned.lower()) if len(w) > 2 and w not in _STOPWORDS
    }
    return bool(orig_words & cleaned_words)


def _invented_numbers(original: str, cleaned: str) -> bool:
    """Second guardrail: catches the more insidious case where the model
    keeps the right device/room (so _shares_words passes) but borrows a
    numeric value from the nearest few-shot example instead of the actual
    input - e.g. "Schreibtisch an" (no number at all) coming back as
    "Schreibtisch 50 Prozent". Any number in the AI output that doesn't
    appear anywhere in the original text is treated as invented."""
    orig_numbers = set(re.findall(r"\d+", original))
    cleaned_numbers = set(re.findall(r"\d+", cleaned))
    return bool(cleaned_numbers - orig_numbers)


# The action/state word itself - "ausschalten", "an", "aus", "öffnen" - is
# what Assist actually needs to do anything; the room/device name alone is
# useless without it. In German this word is very often the LAST
# meaningful token (separable-verb-particle position: "... aus",
# "... ausschalten", "... auf 50 Prozent stellen"), so a weak local model
# dropping it produces output that still passes _shares_words (room/device
# name intact) while being functionally empty.
_ACTION_WORDS = {
    "an", "aus", "ein", "einschalten", "ausschalten", "anschalten",
    "öffnen", "schließen", "stoppen", "anhalten", "stellen", "dimmen",
    "starten", "hoch", "runter", "hochfahren", "herunterfahren",
    "on", "off", "open", "close", "stop", "set", "dim", "start", "raise", "lower",
}

# Anything outside this set (letters incl. umlauts/ß, digits, whitespace,
# basic punctuation) is decoration a weak model added on its own - most
# visibly emoji, observed in practice from "Qwexpressionator" on a plain
# temperature query ("Licht Wohnzimmer aus (sun emoji)") despite the
# prompt explicitly saying "Output ONLY the extracted command". A rule the
# model already ignored is a signal the rest of its output isn't trustworthy
# either, not just the emoji itself - discard the whole result.
_ALLOWED_CHARS_RE = re.compile(r"^[\w\säöüÄÖÜß.,%°/-]*$", re.UNICODE)


def _dropped_action_word(original: str, cleaned: str) -> bool:
    """Third guardrail: the original contains a recognizable action/state
    word but the cleaned output doesn't contain any - the model kept the
    room/device (passing _shares_words) but silently dropped the one part
    that makes it a command at all. Observed in practice: "Licht
    Zwergenwerkstatt ausschalten" -> "Licht Zwergenwerkstatt"."""
    orig_words = set(re.findall(r"\w+", original.lower()))
    if not (orig_words & _ACTION_WORDS):
        return False  # original had no recognizable action word to preserve
    cleaned_words = set(re.findall(r"\w+", cleaned.lower()))
    return not (cleaned_words & _ACTION_WORDS)


def _has_stray_characters(cleaned: str) -> bool:
    """Fourth guardrail: catches decorative junk (emoji, stray symbols)
    the model added despite being told not to - see _ALLOWED_CHARS_RE."""
    return not _ALLOWED_CHARS_RE.match(cleaned)


# Query markers - if the original was recognizably a question (contains a
# "?" or one of these words), the cleaned output needs to still look like
# one, or at least mention a queryable quantity - otherwise a weak model
# has silently turned a question into something else. Observed in
# practice: "wie warm ist es im Wohnzimmer?" -> "Wohnzimmer warm" (no verb
# dropped, no invented number, no stray character - none of the other
# guardrails catch a plain word-reordering into a fragment).
_QUESTION_WORDS = {
    "wie", "was", "wo", "wann", "wer", "warum", "weshalb", "ist", "sind",
    "how", "what", "where", "when", "who", "why", "is", "are",
}
_QUERY_TOPIC_WORDS = {
    "temperatur", "luftfeuchtigkeit", "feuchtigkeit", "wetter", "zeit",
    "status", "temperature", "humidity", "weather", "time",
}


def _dropped_question_form(original: str, cleaned: str) -> bool:
    """Fifth guardrail: the original was a question (has "?" or a question
    word) but the cleaned output has neither a question word nor a
    recognizable query topic word - it no longer reads as "ask about X",
    just as a bare room/device fragment that will not match any Assist
    query sentence."""
    orig_words = set(re.findall(r"\w+", original.lower()))
    was_question = "?" in original or bool(orig_words & _QUESTION_WORDS)
    if not was_question:
        return False
    cleaned_words = set(re.findall(r"\w+", cleaned.lower()))
    still_looks_like_query = bool(
        cleaned_words & _QUESTION_WORDS or cleaned_words & _QUERY_TOPIC_WORDS
    )
    return not still_looks_like_query


def _msg(hass: HomeAssistant, key: str, **kwargs: object) -> str:
    request = _REQUEST.get()
    lang = (request.language if request else hass.config.language or "en").lower()
    table = _MESSAGES["de"] if lang.startswith("de") else _MESSAGES["en"]
    return table[key].format(**kwargs)


def _parse_codewords(raw: str | None) -> list[str]:
    """Comma-separated codewords, e.g. "Homeassist, Computer" -> both trigger."""
    if not raw:
        return []
    return [w.strip() for w in raw.split(",") if w.strip()]


# Relocated to spellbook.py (shared with the spellbook trigger matcher);
# imported here under their original names so _find_codeword_match below
# doesn't need to change.
_fuzzy_threshold = spellbook.fuzzy_threshold
_tokenize_with_spans = spellbook.tokenize_with_spans

# Small built-in yes/no wordlists for replying to a "Meintest du...?"
# entity-correction question - fixed, not user-configurable, since this
# is core dialog plumbing rather than a per-spellbook concept.
_AFFIRMATIVE_WORDS = {
    "ja", "jup", "jo", "genau", "stimmt", "richtig",
    "yes", "yeah", "correct", "right",
}
_NEGATIVE_WORDS = {
    "nein", "nö", "abbrechen", "abbruch", "stopp", "stop",
    "no", "cancel",
}


def _matches_wordlist(text: str, words: set[str]) -> bool:
    """Whole-message fuzzy match against a small fixed wordlist, same
    fuzzy tolerance as everywhere else in the bridge."""
    stripped = text.strip(" ,.:;!?–—").lower()
    if not stripped:
        return False
    threshold = _fuzzy_threshold(stripped)
    for word in words:
        if difflib.SequenceMatcher(None, stripped, word).ratio() >= threshold:
            return True
    return False


def _find_codeword_match(
    text: str, codewords: list[str]
) -> tuple[int, int, str, str] | None:
    """Find the earliest codeword occurrence in text. Tries an exact
    substring match first; if none is found, falls back to fuzzy word-window
    matching so small typos or mishearings still trigger (e.g. "Hildegard"
    heard/typed as "Hüldegard" or "Hilegard", "Homeassist" as
    "Homaassistenz"). Returns (start, end, matched_text, configured_codeword)
    for the earliest hit, or None."""
    lowered = text.lower()
    tokens: list[tuple[str, int, int]] | None = None
    best: tuple[int, int, str, str] | None = None  # (start, end, matched, codeword)

    for word in codewords:
        idx = lowered.find(word.lower())
        if idx != -1:
            candidate = (idx, idx + len(word), text[idx : idx + len(word)], word)
            if best is None or candidate[0] < best[0]:
                best = candidate
            continue

        cw_words = word.split()
        n = len(cw_words)
        if n == 0:
            continue
        threshold = _fuzzy_threshold(word)
        if tokens is None:
            tokens = _tokenize_with_spans(text)
        for i in range(len(tokens) - n + 1):
            window = tokens[i : i + n]
            window_text = " ".join(w for w, _, _ in window)
            ratio = difflib.SequenceMatcher(
                None, window_text.lower(), word.lower()
            ).ratio()
            if ratio >= threshold:
                start, end = window[0][1], window[-1][2]
                candidate = (start, end, text[start:end], word)
                if best is None or candidate[0] < best[0]:
                    best = candidate
                break

    return best


def _bridge_entity_id(hass: HomeAssistant, entry: ConfigEntry) -> str | None:
    """The entity these logbook entries are attributed to (for the device's
    Activity feed) - the last-user-message sensor, since that's what the
    bridge reacts to."""
    machine_id = entry.data.get(CONF_MACHINE_ID, entry.entry_id)
    registry = er.async_get(hass)
    return registry.async_get_entity_id(
        "sensor", DOMAIN, f"{machine_id}_last_user_message"
    )


def _log_activity(
    hass: HomeAssistant, entry: ConfigEntry, message: str, entity_id: str | None
) -> None:
    _LOGGER.info("%s: %s", LOGBOOK_NAME, message)
    hass.bus.async_fire(
        "logbook_entry",
        {
            "name": LOGBOOK_NAME,
            "message": message,
            "domain": DOMAIN,
            "entity_id": entity_id,
        },
    )


def async_setup_bridge(
    hass: HomeAssistant, entry: ConfigEntry, coordinator: Code27Coordinator
) -> None:
    """Start listening for this entry's chat messages while it's loaded."""

    @callback
    def _handle_user_message(event: Event) -> None:
        if event.data.get("config_entry_id") != entry.entry_id:
            return
        if coordinator.stopping or not entry.options.get(CONF_BRIDGE_ENABLED):
            return

        text = event.data.get("text") or ""
        entity_id = _bridge_entity_id(hass, entry)

        ack_phrases = _parse_codewords(entry.options.get(CONF_BRIDGE_ACK_PHRASE))
        if ack_phrases:
            ack_match = _find_codeword_match(text, ack_phrases)
            if ack_match is not None:
                _, _, ack_matched_text, ack_phrase = ack_match
                if not coordinator.notification_active:
                    _log_activity(hass, entry, _msg(hass, "ack_none_active"), entity_id)
                    return
                if ack_matched_text.lower() == ack_phrase.lower():
                    _log_activity(hass, entry, _msg(hass, "ack_detected"), entity_id)
                else:
                    _log_activity(
                        hass,
                        entry,
                        _msg(
                            hass,
                            "ack_fuzzy_detected",
                            matched=ack_matched_text,
                            phrase=ack_phrase,
                        ),
                        entity_id,
                    )
                coordinator.async_create_owned_task(
                    coordinator.async_acknowledge_notification(),
                    name=f"code27_ack_notification_{entry.entry_id}",
                )
                return

        coordinator.async_create_owned_task(
            _process_bridge_message(
                hass, entry, coordinator, text, entity_id,
                _capture_request(hass, entry, coordinator, event.data),
            ),
            name=f"code27_bridge_message_{entry.entry_id}",
        )

    entry.async_on_unload(hass.bus.async_listen(EVENT_USER_MESSAGE, _handle_user_message))


async def _process_bridge_message(
    hass: HomeAssistant, entry: ConfigEntry, coordinator: Code27Coordinator,
    text: str, entity_id: str | None, request: BridgeRequest | None = None,
) -> None:
    """Serialize conversation ownership per device, including correction replies.

    If CONF_BRIDGE_HISTORY_CLEANUP_ENABLED, and this turn actually
    resolved to a recognized command (not just passing conversation -
    see _COMMAND_RECOGNIZED), schedules removing the whole technical
    exchange from the device's own chat history afterwards - see
    Code27Coordinator.async_cleanup_assist_exchange. Scheduled as its own
    owned task (outside the bridge_lock above) since it polls for the
    device's own reply over several seconds and must not delay the next
    queued command."""
    request = request or _capture_request(hass, entry, coordinator)
    cleanup_enabled = bool(
        entry.options.get(CONF_BRIDGE_HISTORY_CLEANUP_ENABLED, False) and request.chat_id
    )
    request_token = _REQUEST.set(request)
    # Seed with the command's own chat_id up front so _output_speech's
    # relay writes (if any) just append to the same list - see
    # Code27Coordinator.async_cleanup_assist_exchange.
    relay_token = _RELAY_CHAT_IDS.set([request.chat_id]) if cleanup_enabled else None
    recognized_token = _COMMAND_RECOGNIZED.set(False) if cleanup_enabled else None
    try:
        async with coordinator.bridge_lock:
            if coordinator.stopping or not entry.options.get(CONF_BRIDGE_ENABLED):
                return
            await _handle_bridge_message(hass, entry, coordinator, text, entity_id)
    finally:
        seed_chat_ids = _RELAY_CHAT_IDS.get() if cleanup_enabled else None
        recognized = _COMMAND_RECOGNIZED.get() if cleanup_enabled else False
        _REQUEST.reset(request_token)
        if relay_token is not None:
            _RELAY_CHAT_IDS.reset(relay_token)
        if recognized_token is not None:
            _COMMAND_RECOGNIZED.reset(recognized_token)

    if cleanup_enabled and recognized and not coordinator.stopping:
        coordinator.async_create_owned_task(
            _run_history_cleanup(hass, entry, coordinator, request.slot, seed_chat_ids, entity_id),
            name=f"code27_history_cleanup_{entry.entry_id}",
        )


async def _run_history_cleanup(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: Code27Coordinator,
    slot: int,
    seed_chat_ids: list[str],
    entity_id: str | None,
) -> None:
    """Runs async_cleanup_assist_exchange and reports the outcome to the
    same Activity feed/Logbook as everything else the bridge does, so
    CONF_BRIDGE_HISTORY_CLEANUP_ENABLED's effect (or silent no-op, e.g.
    because it couldn't positively resolve a message) is visible without
    digging through the raw HA log."""
    try:
        deleted = await coordinator.async_cleanup_assist_exchange(slot, seed_chat_ids)
    except Exception as err:  # noqa: BLE001
        _LOGGER.warning("%s: history cleanup failed: %s", LOGBOOK_NAME, err)
        _log_activity(hass, entry, _msg(hass, "history_cleanup_failed", error=err), entity_id)
        return
    expected = len(seed_chat_ids) * 2
    _log_activity(
        hass, entry,
        _msg(hass, "history_cleanup_ok", deleted=deleted, expected=expected),
        entity_id,
    )


async def _is_explicit_command(
    hass: HomeAssistant, entry: ConfigEntry, coordinator: Code27Coordinator, text: str
) -> bool:
    if _find_codeword_match(text, _parse_codewords(entry.options.get(CONF_BRIDGE_CODEWORD))):
        return True
    book_id = entry.options.get(CONF_BRIDGE_SPELLBOOK)
    if book_id:
        book = await spellbook.async_get_spellbook(hass, book_id)
        if book is not None:
            lang = _current_request(hass, entry, coordinator).language.lower()[:2]
            return spellbook.match_spellbook(book, text, lang) is not None
    return False


async def _handle_bridge_message(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: Code27Coordinator,
    text: str,
    entity_id: str | None,
) -> None:
    """Everything after the ack-phrase check: an explicit new command ends
    any pending correction. Otherwise interpret a message as a correction
    reply when a dialog is open. Then try the configured spellbook (if
    any), then fall back to - or, in strict mode, skip entirely - the
    classic codeword flow. This has to be async (unlike the ack-phrase
    check) because loading a spellbook involves file I/O, and it has to
    run as a single task rather than several so a spellbook hit can
    actually suppress the codeword fallback instead of both firing
    independently."""
    if coordinator.pending_correction is not None and await _is_explicit_command(
        hass, entry, coordinator, text
    ):
        coordinator.pending_correction = None
        _log_activity(hass, entry, _msg(hass, "correction_cancelled"), entity_id)
    elif await _handle_correction_reply(hass, entry, coordinator, text, entity_id):
        return

    spellbook_id = entry.options.get(CONF_BRIDGE_SPELLBOOK)
    if spellbook_id:
        book = await spellbook.async_get_spellbook(hass, spellbook_id)
        if book is None:
            # A configured spellbook that no longer resolves (file
            # removed, renamed, or never existed) used to fail this
            # check silently and fall straight through to the
            # codeword/Assist path as if no spellbook were configured
            # at all - every spellbook command this entry relies on
            # would silently stop working with nothing in the log to
            # explain why. Found via a second-round audit, prompted by
            # removing the unused star_trek.yaml, but this is a
            # general robustness gap, not specific to that file.
            _LOGGER.warning(
                "%s: configured spellbook %r not found - spellbook commands are disabled "
                "for this entry until it's reconfigured", LOGBOOK_NAME, spellbook_id,
            )
        if book is not None:
            lang = _current_request(hass, entry, coordinator).language.lower()[:2]
            match = spellbook.match_spellbook(book, text, lang)
            if isinstance(match, spellbook.SpellAmbiguity):
                await _begin_spell_choice_correction(hass, entry, coordinator, match, entity_id)
                return
            if match is not None:
                (
                    command, catchphrase, trigger_text, matched_text, is_exact, captures, action,
                ) = match
                if action == "vacuum_clean":
                    _log_activity(
                        hass,
                        entry,
                        _msg(
                            hass,
                            "spellbook_detected" if is_exact else "spellbook_fuzzy_detected",
                            book=book.name,
                            trigger=trigger_text,
                            matched=matched_text,
                            command=f"vacuum_clean({captures.get('area', '')})",
                        ),
                        entity_id,
                    )
                    if entry.options.get(CONF_BRIDGE_VACUUM_ENABLED, False):
                        await _handle_vacuum_clean_action(
                            hass, entry, coordinator, captures.get("area", ""), catchphrase, entity_id
                        )
                    else:
                        _log_activity(hass, entry, _msg(hass, "vacuum_action_disabled"), entity_id)
                    return
                if action in ("light_all_on", "light_all_off"):
                    _log_activity(
                        hass,
                        entry,
                        _msg(
                            hass,
                            "spellbook_detected" if is_exact else "spellbook_fuzzy_detected",
                            book=book.name,
                            trigger=trigger_text,
                            matched=matched_text,
                            command=action,
                        ),
                        entity_id,
                    )
                    await _handle_light_all_action(hass, entry, coordinator, action, catchphrase, entity_id)
                    return
                if is_exact:
                    _log_activity(
                        hass,
                        entry,
                        _msg(
                            hass,
                            "spellbook_detected",
                            book=book.name,
                            trigger=trigger_text,
                            command=command,
                        ),
                        entity_id,
                    )
                else:
                    _log_activity(
                        hass,
                        entry,
                        _msg(
                            hass,
                            "spellbook_fuzzy_detected",
                            book=book.name,
                            matched=matched_text,
                            trigger=trigger_text,
                            command=command,
                        ),
                        entity_id,
                    )
                correction_enabled = entry.options.get(
                    CONF_BRIDGE_ENTITY_CORRECTION_ENABLED, False
                )
                speech, response, sent_command = await _forward_to_assist(
                    hass,
                    entry,
                    coordinator,
                    command,
                    entity_id,
                    catchphrase=catchphrase,
                    skip_ai_cleanup=True,
                    suppress_error_speech=correction_enabled,
                )
                if (
                    correction_enabled
                    and response is not None
                    and response.response_type == intent.IntentResponseType.ERROR
                ):
                    started = await _start_correction_from_spellbook(
                        hass, entry, coordinator, sent_command, captures, catchphrase, entity_id
                    )
                    if not started and speech:
                        await _output_speech(hass, entry, coordinator, speech, entity_id)
                return
        if (
            entry.options.get(CONF_BRIDGE_SPELLBOOK_MODE, DEFAULT_SPELLBOOK_MODE)
            == SPELLBOOK_MODE_STRICT
        ):
            return

    codewords = _parse_codewords(entry.options.get(CONF_BRIDGE_CODEWORD))
    if not codewords:
        return

    match = _find_codeword_match(text, codewords)
    if match is None:
        return
    idx, end_idx, matched_text, word = match

    command = text[end_idx:].strip(" ,:.-!–—")
    if not command:
        return

    if matched_text.lower() == word.lower():
        _log_activity(hass, entry, _msg(hass, "codeword_detected", command=command), entity_id)
    else:
        _log_activity(
            hass,
            entry,
            _msg(
                hass,
                "codeword_fuzzy_detected",
                matched=matched_text,
                codeword=word,
                command=command,
            ),
            entity_id,
        )

    cleaned = _strip_filler(command)
    if cleaned != command:
        _log_activity(hass, entry, _msg(hass, "filler_stripped", command=cleaned), entity_id)
        command = cleaned

    correction_enabled = entry.options.get(CONF_BRIDGE_ENTITY_CORRECTION_ENABLED, False)
    speech, response, sent_command = await _forward_to_assist(
        hass, entry, coordinator, command, entity_id,
        suppress_error_speech=correction_enabled,
    )
    if (
        correction_enabled
        and response is not None
        and response.response_type == intent.IntentResponseType.ERROR
    ):
        started = await _start_correction_from_codeword(hass, entry, coordinator, sent_command, entity_id)
        if not started and speech:
            await _output_speech(hass, entry, coordinator, speech, entity_id)


async def _ask_correction(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: Code27Coordinator,
    candidate: str,
    entity_id: str | None,
) -> None:
    _log_activity(
        hass, entry, _msg(hass, "correction_asked", candidate=candidate), entity_id
    )
    await _output_speech(
        hass, entry, coordinator, _msg(hass, "correction_ask", candidate=candidate), entity_id
    )


async def _begin_spell_choice_correction(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: Code27Coordinator,
    ambiguity: spellbook.SpellAmbiguity,
    entity_id: str | None,
) -> None:
    """Two same-specificity spellbook triggers matched this message
    almost equally well but resolve to opposite actions (see
    spellbook.SpellAmbiguity) - ask which one was meant instead of
    guessing. Reuses the same "at most one open dialog" PendingCorrection
    as entity-name correction, just with spell_option_a/b set instead of
    failed_phrase/tried_candidates - see _consume_spell_choice_reply for
    the other half of this dialog."""
    request = _current_request(hass, entry, coordinator)
    _, _, trigger_a = ambiguity.option_a
    _, _, trigger_b = ambiguity.option_b
    _log_activity(
        hass,
        entry,
        _msg(hass, "spell_choice_ambiguous", trigger_a=trigger_a, trigger_b=trigger_b),
        entity_id,
    )
    coordinator.pending_correction = PendingCorrection(
        slot=request.slot,
        language=request.language,
        original_command="",
        failed_phrase="",
        entity_id=entity_id,
        spell_option_a=ambiguity.option_a,
        spell_option_b=ambiguity.option_b,
    )
    await _output_speech(
        hass,
        entry,
        coordinator,
        _msg(hass, "spell_choice_ask", trigger_a=trigger_a, trigger_b=trigger_b),
        entity_id,
    )


async def _begin_correction(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: Code27Coordinator,
    original_command: str,
    failed_phrase: str,
    candidate: str,
    catchphrase: str | None,
    include_areas: bool,
    entity_id: str | None,
) -> None:
    request = _current_request(hass, entry, coordinator)
    coordinator.pending_correction = PendingCorrection(
        slot=request.slot,
        language=request.language,
        original_command=original_command,
        failed_phrase=failed_phrase,
        last_suggested=candidate,
        catchphrase=catchphrase,
        include_areas=include_areas,
        entity_id=entity_id,
    )
    await _ask_correction(hass, entry, coordinator, candidate, entity_id)


async def _start_correction_from_spellbook(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: Code27Coordinator,
    sent_command: str,
    captures: dict[str, str],
    catchphrase: str | None,
    entity_id: str | None,
) -> bool:
    """After a spellbook-resolved command comes back as an Assist ERROR,
    try to start a correction dialog from the {area}/{target} placeholder
    that was captured - {area} allows area names as candidates (that's
    what the placeholder is for), {target} means a controllable entity
    and does not. Returns whether a dialog was actually started, so the
    caller knows whether it still needs to speak the (suppressed) real
    Assist error itself."""
    if "area" in captures:
        placeholder, include_areas = "area", True
    elif "target" in captures:
        placeholder, include_areas = "target", False
    else:
        return False  # nothing name-like was captured (e.g. "tempus" has no placeholders)

    failed_phrase = captures[placeholder]
    found = spellbook.find_correction_candidate(hass, failed_phrase, [], include_areas)
    if found is None:
        return False

    await _begin_correction(
        hass, entry, coordinator, sent_command, failed_phrase, found[0],
        catchphrase, include_areas, entity_id,
    )
    return True


async def _start_correction_from_codeword(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: Code27Coordinator,
    sent_command: str,
    entity_id: str | None,
) -> bool:
    """After a free-text codeword command comes back as an Assist ERROR,
    guess which word(s) were the bad entity/area name via a fuzzy n-gram
    search, and only start a correction dialog if that guess clears the
    normal acceptance bar. Returns whether a dialog was actually
    started - if not, the plain (suppressed) error still needs speaking.

    include_areas=True (not False) here: unlike the spellbook flow, a
    free-text codeword command has no {area}/{target} placeholder telling
    us which kind of name was meant, and in practice almost every working
    Assist command in this house is area-based ("Licht {area}
    ein/ausschalten"), not addressed to an individual entity by name -
    excluding areas from the guess pool left the correction dialog unable
    to ever land on the right candidate for an area-shaped failure,
    spiraling into a repeated "Welches Gerät meinst du?" that could never
    resolve (confirmed against production: "wie warm ist Wohnzimmer" ->
    "Wohnzimmer warm" -> Assist error -> correction dialog stuck asking
    for a device instead of offering the "Wohnzimmer" area)."""
    extracted = spellbook.extract_named_phrase(hass, sent_command, include_areas=True)
    if extracted is None:
        return False
    failed_phrase, candidate, _ratio = extracted
    await _begin_correction(
        hass, entry, coordinator, sent_command, failed_phrase, candidate,
        None, True, entity_id,
    )
    return True


async def _retry_with_candidate(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: Code27Coordinator,
    pending: PendingCorrection,
    candidate: str,
    entity_id: str | None,
) -> None:
    """Substitute `candidate` for pending.failed_phrase in the original
    failed command and retry through Assist. ACTION_DONE -> success,
    clear the pending correction (the catchphrase, if any, was already
    applied by _forward_to_assist). Anything else -> record the
    candidate as tried and either ask about the next one (suppressing
    this attempt's raw Assist error, same reasoning as the first attempt
    - don't say "das habe ich nicht verstanden" right before asking
    about the next candidate) or, if there's nothing left to suggest,
    give up and speak the real final error instead."""
    # A suggestion can outlive its exposure permission while we wait for "yes".
    verified = spellbook.verify_named_candidate(hass, candidate, pending.include_areas)
    if verified is None or verified[0].casefold() != candidate.casefold():
        coordinator.pending_correction = None
        _log_activity(hass, entry, _msg(hass, "correction_gave_up"), entity_id)
        return
    retried_command = pending.original_command.replace(pending.failed_phrase, candidate, 1)
    _log_activity(
        hass,
        entry,
        _msg(hass, "correction_retry", candidate=candidate, command=retried_command),
        entity_id,
    )
    speech, response, _ = await _forward_to_assist(
        hass,
        entry,
        coordinator,
        retried_command,
        entity_id,
        catchphrase=pending.catchphrase,
        skip_ai_cleanup=True,
        suppress_error_speech=True,
    )

    if response is not None and response.response_type == intent.IntentResponseType.ACTION_DONE:
        coordinator.pending_correction = None
        return

    pending.tried_candidates.append(candidate)
    next_found = spellbook.find_correction_candidate(
        hass, pending.failed_phrase, pending.tried_candidates, pending.include_areas
    )
    if next_found is None:
        coordinator.pending_correction = None
        _log_activity(hass, entry, _msg(hass, "correction_gave_up"), entity_id)
        if speech:
            await _output_speech(hass, entry, coordinator, speech, entity_id)
        return

    pending.last_suggested = next_found[0]
    await _ask_correction(hass, entry, coordinator, next_found[0], entity_id)


async def _handle_correction_reply(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: Code27Coordinator,
    text: str,
    entity_id: str | None,
) -> bool:
    request = _current_request(hass, entry, coordinator)
    pending = coordinator.pending_correction
    if pending is not None and pending.slot is not None and pending.slot != request.slot:
        coordinator.pending_correction = None
        return False
    if pending is not None and pending.language:
        request = BridgeRequest(entry.entry_id, request.slot, pending.language)
    token = _REQUEST.set(request)
    try:
        return await _consume_correction_reply(hass, entry, coordinator, text, entity_id)
    finally:
        _REQUEST.reset(token)


async def _consume_correction_reply(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: Code27Coordinator,
    text: str,
    entity_id: str | None,
) -> bool:
    """If an entity-correction dialog is open, try to interpret `text`
    as a reply to it. Returns True if the message was consumed this way
    (the caller must not process it as anything else). Only the idle
    timeout ever silently ends the dialog - a reply that doesn't parse
    cleanly re-prompts instead of dropping it."""
    pending = coordinator.pending_correction
    if pending is None:
        return False

    if pending.is_expired(CORRECTION_TIMEOUT_SECONDS):
        coordinator.pending_correction = None
        _log_activity(hass, entry, _msg(hass, "correction_timed_out"), entity_id)
        return False

    if pending.spell_option_a is not None:
        return await _consume_spell_choice_reply(hass, entry, coordinator, pending, text, entity_id)

    stripped = text.strip(" ,.:;!?–—")

    if _matches_wordlist(stripped, _AFFIRMATIVE_WORDS):
        if pending.last_suggested is None:
            # Shouldn't normally happen - a candidate is always suggested
            # before a dialog is opened or kept open - but guard anyway.
            coordinator.pending_correction = None
            return False
        pending.touch()
        await _retry_with_candidate(
            hass, entry, coordinator, pending, pending.last_suggested, entity_id
        )
        return True

    if _matches_wordlist(stripped, _NEGATIVE_WORDS):
        _log_activity(hass, entry, _msg(hass, "correction_cancelled"), entity_id)
        coordinator.pending_correction = None
        return True

    # Accept a name or a short correction wrapper, never a name extracted
    # from an otherwise unrelated sentence containing a different action.
    candidate_text = re.sub(
        r"^(?:ich mein(?:e|te)|i mean|i meant)\s+(?:(?:den|die|das|the)\s+)?",
        "", stripped, flags=re.IGNORECASE,
    )
    verified = spellbook.verify_named_candidate(hass, candidate_text, pending.include_areas)
    if verified is not None:
        pending.touch()
        await _retry_with_candidate(hass, entry, coordinator, pending, verified[0], entity_id)
        return True

    # Didn't parse as yes/no or any name - re-prompt instead of dropping
    # the dialog (only the idle timeout above may do that).
    pending.touch()
    await _output_speech(
        hass, entry, coordinator, _msg(hass, "correction_which_device"), entity_id
    )
    return True


# How much higher one spell-choice option's resemblance score must be
# than the other's before a reply counts as clearly picking it, rather
# than being treated as still unclear and re-prompted. Deliberately
# requires a real lean, not just a coin-flip-sized edge, since the whole
# point of this dialog is to avoid guessing between opposite actions.
_SPELL_CHOICE_PICK_MARGIN = 0.1


async def _consume_spell_choice_reply(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: Code27Coordinator,
    pending: PendingCorrection,
    text: str,
    entity_id: str | None,
) -> bool:
    """Interpret a reply to the "Meinst du X oder Y?" spell-choice dialog
    (see _begin_spell_choice_correction). Picks whichever option's
    trigger or resolved command text the reply resembles more closely
    (e.g. repeating "entzünde" or "lösche" back); a reply that doesn't
    clearly favor either one re-prompts instead of guessing, same
    philosophy as the entity-correction dialog above. Deliberately has
    no special "yes" handling - unlike a single suggested candidate,
    there's no one thing "yes" could mean between two options, so a
    reply is judged purely on which wording it resembles more."""
    stripped = text.strip(" ,.:;!?–—")

    if _matches_wordlist(stripped, _NEGATIVE_WORDS):
        _log_activity(hass, entry, _msg(hass, "correction_cancelled"), entity_id)
        coordinator.pending_correction = None
        return True

    command_a, catchphrase_a, trigger_a = pending.spell_option_a
    command_b, catchphrase_b, trigger_b = pending.spell_option_b
    lowered = stripped.lower()
    score_a = max(
        difflib.SequenceMatcher(None, lowered, trigger_a.lower()).ratio(),
        difflib.SequenceMatcher(None, lowered, command_a.lower()).ratio(),
    )
    score_b = max(
        difflib.SequenceMatcher(None, lowered, trigger_b.lower()).ratio(),
        difflib.SequenceMatcher(None, lowered, command_b.lower()).ratio(),
    )

    if abs(score_a - score_b) < _SPELL_CHOICE_PICK_MARGIN:
        pending.touch()
        await _output_speech(
            hass,
            entry,
            coordinator,
            _msg(hass, "spell_choice_ask", trigger_a=trigger_a, trigger_b=trigger_b),
            entity_id,
        )
        return True

    coordinator.pending_correction = None
    picked_command, picked_catchphrase, picked_trigger = (
        pending.spell_option_a if score_a > score_b else pending.spell_option_b
    )
    _log_activity(
        hass,
        entry,
        _msg(hass, "spell_choice_picked", trigger=picked_trigger, command=picked_command),
        entity_id,
    )
    await _forward_to_assist(
        hass,
        entry,
        coordinator,
        picked_command,
        entity_id,
        catchphrase=picked_catchphrase,
        skip_ai_cleanup=True,
    )
    return True


_AI_CLEANUP_PROMPT = (
    "You are a command extractor for Home Assistant.\n"
    "\n"
    "Convert the input into the shortest possible Home Assistant command.\n"
    "\n"
    "RULES:\n"
    "- Keep the output in the SAME LANGUAGE as the input.\n"
    "- Remove greetings and forms of address.\n"
    "- Remove politeness.\n"
    "- Remove filler words.\n"
    "- Remove indirect phrasing (e.g. 'could you ask Home Assistant to ...').\n"
    "- Remove conditionals and subjunctive phrasing.\n"
    "- Remove unnecessary verbs and clauses.\n"
    "- Preserve device names, room names, numbers and units exactly.\n"
    "- Do NOT translate names.\n"
    "- Do NOT invent information.\n"
    "- Do NOT answer the request.\n"
    "- Do NOT use complete sentences.\n"
    "- Output maximum 8 words.\n"
    "- Output ONLY the extracted command.\n"
    "\n"
    "Two input shapes exist - handle them differently:\n"
    "\n"
    "1) COMMAND (imperative - do something): strip the politeness/filler\n"
    "   wrapper and keep ONLY action, device/room, and required values.\n"
    "\n"
    "2) QUERY (interrogative - asking for information, e.g. temperature,\n"
    "   humidity, state, whether something is on): this is NOT a command\n"
    "   to remove. Keep it as a question. Strip only the wrapper\n"
    "   (greeting, name, politeness, filler) and shorten the question\n"
    "   itself to 'QuestionWord ... Room/Device', e.g. 'Temperatur\n"
    "   {{room}}' or 'Wie warm ist {{room}}'. Never turn a query into an\n"
    "   imperative verb that isn't in the input (a question about\n"
    "   temperature is NOT a command to change the temperature).\n"
    "\n"
    "EXAMPLES:\n"
    "\n"
    "Input:\n"
    "Würdest du bitte das Licht im Wohnzimmer ausschalten?\n"
    "Output:\n"
    "Licht Wohnzimmer ausschalten\n"
    "\n"
    "Input:\n"
    "Kannst du vielleicht die Lampe am Schreibtisch auf 50 Prozent stellen?\n"
    "Output:\n"
    "Lampe Schreibtisch 50 Prozent\n"
    "\n"
    "Input:\n"
    "Du, sag mal, würdest du Home Assistant vielleicht fragen, ob er "
    "eventuell das Licht am Schreibtisch auf 80 Prozent dimmen würde, wenn "
    "er so nett ist?\n"
    "Output:\n"
    "Licht Schreibtisch 80 Prozent\n"
    "\n"
    "Input:\n"
    "Kannst du vielleicht die Umgebungsfarbe auf 80 Prozent dimmen?\n"
    "Output:\n"
    "Umgebungsfarbe 80 Prozent\n"
    "\n"
    "Input:\n"
    "Hildegard, wie warm ist es im Wohnzimmer?\n"
    "Output:\n"
    "Wie warm ist Wohnzimmer\n"
    "\n"
    "Input:\n"
    "Sag mal, weißt du zufällig, wie die Luftfeuchtigkeit im Kinderzimmer "
    "Anika gerade ist?\n"
    "Output:\n"
    "Luftfeuchtigkeit Kinderzimmer Anika\n"
    "\n"
    "Input:\n"
    "Could you please ask Home Assistant if it would maybe turn off the "
    "light in the living room?\n"
    "Output:\n"
    "Living room light off\n"
    "\n"
    "Input:\n"
    "Hey, could you maybe ask Home Assistant to set the desk light to 50 "
    "percent if that's okay?\n"
    "Output:\n"
    "Desk light 50 percent\n"
    "\n"
    "Input:\n"
    "Would you mind asking Home Assistant if it could perhaps turn the "
    "kitchen light on?\n"
    "Output:\n"
    "Kitchen light on\n"
    "\n"
    "Input:\n"
    "Could you ask Home Assistant to set the bedroom temperature to 21 "
    "degrees, please?\n"
    "Output:\n"
    "Bedroom temperature 21 degrees\n"
    "\n"
    "Input:\n"
    "Hey, do you happen to know how warm it currently is in the living "
    "room?\n"
    "Output:\n"
    "How warm is living room\n"
    "\n"
    "Input:\n"
    "{command}\n"
    "\n"
    "Output:"
)


async def _ai_cleanup(
    hass: HomeAssistant, entry: ConfigEntry, command: str, entity_id: str | None
) -> str:
    """Optional stage 2: run the command through a configured AI Task entity
    (e.g. a local Ollama model) to clean up phrasing before handing it to
    Assist. Assist still does all the actual entity/action understanding -
    this only tidies up how the request is worded. Falls back to the
    stage-1-cleaned command on any failure."""
    if not entry.options.get(CONF_BRIDGE_AI_CLEANUP_ENABLED):
        return command

    task_entity_id = entry.options.get(CONF_BRIDGE_AI_TASK_ENTITY_ID)
    if not task_entity_id:
        return command

    try:
        async with asyncio.timeout(_AI_CLEANUP_TIMEOUT):
            result = await hass.services.async_call(
                "ai_task", "generate_data",
                {
                    "task_name": "code27_bridge_cleanup",
                    "instructions": _AI_CLEANUP_PROMPT.format(command=command),
                    "entity_id": task_entity_id,
                },
                blocking=True, return_response=True,
            )
        data = result.get("data") if isinstance(result, dict) else None
        text = data
        if isinstance(data, dict):
            text = data.get("text") or data.get("result")
        if not isinstance(text, str):
            return command
        cleaned = text.strip().strip('"')
    except Exception as err:  # Cancellation must propagate on entry unload.
        reason = f"timeout after {_AI_CLEANUP_TIMEOUT:g}s" if isinstance(err, TimeoutError) else str(err)
        _LOGGER.warning("%s: AI Task cleanup failed: %s", LOGBOOK_NAME, reason)
        _log_activity(hass, entry, _msg(hass, "ai_cleanup_error", error=reason), entity_id)
        return command

    if not cleaned or cleaned == command:
        return command

    if (
        not _shares_words(command, cleaned)
        or _invented_numbers(command, cleaned)
        or _dropped_action_word(command, cleaned)
        or _has_stray_characters(cleaned)
        or _dropped_question_form(command, cleaned)
    ):
        _LOGGER.warning(
            "%s: AI cleanup result %r looks unrelated to input %r, discarding",
            LOGBOOK_NAME,
            cleaned,
            command,
        )
        _log_activity(
            hass, entry, _msg(hass, "ai_cleanup_discarded", ai_result=cleaned), entity_id
        )
        return command

    _log_activity(hass, entry, _msg(hass, "ai_cleanup_result", command=cleaned), entity_id)
    return cleaned


async def _ask_assist(
    hass: HomeAssistant,
    entry: ConfigEntry,
    command: str,
    entity_id: str | None,
    language: str | None = None,
) -> tuple[str | None, object | None, bool]:
    """Send `command` to the configured Assist agent and log the raw
    exchange. Returns (speech, response, continue_conversation) - speech
    is None if Assist raised, returned no response, or had nothing to
    say; response is the raw IntentResponse (or None on a hard failure)
    so callers can inspect response.response_type, e.g. to decide
    whether an entity-correction retry is worth attempting;
    continue_conversation is the ConversationResult's own flag - True
    means the agent (e.g. a spellbooker entry mid-clarification) is
    asking a follow-up and expects THIS reply to be answered as a
    continuation, something this bridge's one-shot, conversation_id=None
    model has no way to actually honor (found via a second-round audit:
    the agent-config option can point at any conversation agent,
    spellbooker entries included, and the caller used to have no way to
    even notice this case exists). Shared by the plain one-shot path
    (_forward_to_assist) and the entity-correction retry loop, which
    needs to call Assist more than once for the same logical command."""
    # Leaving agent_id unset resolves to Home Assistant's own built-in
    # conversation agent (conversation.async_converse's actual default),
    # not whatever pipeline the user picked as "preferred" in Settings ->
    # Voice assistants - confirmed by reproduction: switching the
    # preferred Assist pipeline while leaving this option blank still
    # calls the built-in agent. Fine for the common single-agent setup
    # this targets, just not the "your favorite" behavior the option's
    # own help text used to (incorrectly) promise - see strings.json.
    agent_id = entry.options.get(CONF_BRIDGE_AGENT_ID) or None

    _log_activity(hass, entry, _msg(hass, "sent_to_assist", command=command), entity_id)

    try:
        result = await conversation.async_converse(
            hass,
            text=command,
            conversation_id=None,
            context=Context(),
            agent_id=agent_id,
            language=language,
        )
    except Exception as err:  # noqa: BLE001 - never let this take the entry down
        _LOGGER.warning("%s: could not process %r: %s", LOGBOOK_NAME, command, err)
        _log_activity(hass, entry, _msg(hass, "assist_error", error=err), entity_id)
        return None, None, False

    continue_conversation = bool(getattr(result, "continue_conversation", False))
    speech = None
    response = getattr(result, "response", None)
    if response is not None and getattr(response, "speech", None):
        speech = response.speech.get("plain", {}).get("speech")

    if not speech:
        _log_activity(hass, entry, _msg(hass, "no_reply"), entity_id)
        return None, response, continue_conversation

    _log_activity(hass, entry, _msg(hass, "assist_reply", speech=speech), entity_id)
    return speech, response, continue_conversation


async def _output_speech(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: Code27Coordinator,
    text: str,
    entity_id: str | None,
    request: BridgeRequest | None = None,
) -> None:
    """Speak/relay `text` through whichever of TTS/chat-relay the entry
    has enabled - the same output channel a normal Assist reply already
    goes through. Shared by the plain reply path, spellbook catchphrases,
    and the entity-correction loop's "Meintest du...?" questions, so
    there's exactly one place that knows how CODE27 output works."""
    request = request or _current_request(hass, entry, coordinator)
    slot = request.slot

    if entry.options.get(CONF_BRIDGE_TTS_ENABLED, False):
        await async_speak_text(hass, entry, coordinator, text, entity_id, expected_slot=slot)

    if not entry.options.get(CONF_BRIDGE_RELAY_RESPONSE, False):
        return

    try:
        chat_id = await coordinator.async_send_chat_reply(text, slot, request.language)
    except Exception as err:  # noqa: BLE001
        _LOGGER.warning("%s: could not write reply into chat: %s", LOGBOOK_NAME, err)
        _log_activity(hass, entry, _msg(hass, "chat_write_failed", error=err), entity_id)
    else:
        _log_activity(hass, entry, _msg(hass, "chat_write_ok"), entity_id)
        relay_chat_ids = _RELAY_CHAT_IDS.get()
        if chat_id is not None and relay_chat_ids is not None:
            relay_chat_ids.append(chat_id)


async def _forward_to_assist(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: Code27Coordinator,
    command: str,
    entity_id: str | None,
    catchphrase: str | None = None,
    skip_ai_cleanup: bool = False,
    suppress_error_speech: bool = False,
) -> tuple[str | None, object | None, str]:
    """One-shot: clean up (unless skipped), ask Assist, apply the
    catchphrase substitution rule, and speak/relay the result. Returns
    (speech, response, command) - command is the exact text that was
    actually sent to Assist (post AI-cleanup, if that ran), which the
    entity-correction loop needs to know what to substring-replace when
    retrying with a corrected name.

    suppress_error_speech=True skips speaking/relaying an ERROR reply
    (still returns it) - used when the caller is about to follow up with
    its own "Meintest du...?" question instead, so the device doesn't say
    "das habe ich nicht verstanden" immediately followed by a
    correction question (they can even audibly overlap). The caller is
    responsible for speaking the suppressed `speech` itself if it turns
    out no correction question follows after all."""
    _COMMAND_RECOGNIZED.set(True)
    request = _current_request(hass, entry, coordinator)
    if not skip_ai_cleanup:
        command = await _ai_cleanup(hass, entry, command, entity_id)

    if coordinator.stopping:
        return None, None, command
    speech, response, continue_conversation = await _ask_assist(
        hass, entry, command, entity_id, language=request.language
    )
    if not speech or coordinator.stopping:
        return speech, response, command

    is_error = response is not None and response.response_type == intent.IntentResponseType.ERROR
    if is_error and suppress_error_speech:
        return speech, response, command

    # A spellbook catchphrase only ever stands in for a confirmed
    # ACTION_DONE - never for QUERY_ANSWER (Assist merely answered a
    # question, it didn't perform the spell's action) and never for
    # ERROR. Both of those must always speak Assist's real reply.
    # continue_conversation=True is a THIRD case that needs the same
    # exclusion: the configured agent (any conversation agent can be
    # set here, a spellbooker entry included) is asking a follow-up
    # question and it reports as ACTION_DONE too, since HA has no
    # distinct response_type for "asking something back" - this bridge
    # has no conversation_id continuity to actually answer that
    # follow-up with anyway (see _ask_assist's own docstring), but the
    # least it must not do is speak an unrelated catchphrase OVER the
    # real question the user needs to hear to answer it at all.
    if (
        catchphrase
        and response is not None
        and response.response_type == intent.IntentResponseType.ACTION_DONE
        and not continue_conversation
    ):
        _log_activity(
            hass,
            entry,
            _msg(hass, "spellbook_catchphrase_used", catchphrase=catchphrase, original=speech),
            entity_id,
        )
        speech = catchphrase

    await _output_speech(hass, entry, coordinator, speech, entity_id, request=request)
    return speech, response, command


async def _handle_vacuum_clean_action(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: Code27Coordinator,
    area_alias: str,
    catchphrase: str | None,
    entity_id: str | None,
) -> None:
    """Resolve a "vacuum_clean" spellbook command's captured {area} and
    start cleaning just that room.

    Since Etappe 5, execution goes through core.executor.ActionExecutor
    via providers.code27.frontend.Code27FrontendAdapter - the same shared path
    HA-Voice frontends use - instead of calling Home Assistant directly.
    The native-vacuum.clean_area-then-provider-fallback two-tier logic
    and the "never guess between multiple matching rooms" rule (a wrong
    room is a real physical mistake, not just a bad reply) now live in
    spellbooker_ha.py's HaServiceCaller.clean_vacuum_area, proven
    identical via Etappe 4's tests. This function's only job left is
    mapping the returned ExecutionResult back onto the exact same
    activity-log/speech messages callers saw before the migration."""
    _COMMAND_RECOGNIZED.set(True)
    request = _current_request(hass, entry, coordinator)
    execution = await Code27FrontendAdapter(hass).clean_vacuum_area(area_alias)

    if execution.status is ExecutionStatus.INVALID and execution.reason_code == "target_not_found":
        _log_activity(hass, entry, _msg(hass, "vacuum_area_unresolved", area=area_alias), entity_id)
        return
    if execution.status is ExecutionStatus.INVALID and execution.reason_code == "no_matching_targets":
        _log_activity(hass, entry, _msg(hass, "vacuum_no_match", area=area_alias), entity_id)
        return
    if execution.status is ExecutionStatus.FAILED and execution.reason_code == "ambiguous_vacuum_match":
        _log_activity(
            hass,
            entry,
            _msg(
                hass, "vacuum_ambiguous", area=area_alias,
                candidates=execution.data.get("candidates_description") or "",
            ),
            entity_id,
        )
        return
    if execution.status is not ExecutionStatus.SUCCESS:
        _LOGGER.warning("%s: vacuum clean command failed: %s", LOGBOOK_NAME, execution.reason_code)
        error_speech = _msg(hass, "vacuum_error", error=execution.reason_code)
        _log_activity(hass, entry, error_speech, entity_id)
        await _output_speech(hass, entry, coordinator, error_speech, entity_id, request=request)
        return

    room_name = execution.data.get("room_name") or area_alias
    vacuum_entity_id = execution.data.get("vacuum_entity_id") or ""
    _log_activity(
        hass, entry, _msg(hass, "vacuum_cleaning_started", room=room_name, vacuum=vacuum_entity_id), entity_id
    )
    speech = catchphrase or _msg(hass, "vacuum_cleaning_started", room=room_name, vacuum=vacuum_entity_id)
    await _output_speech(hass, entry, coordinator, speech, entity_id, request=request)


async def _handle_light_all_action(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: Code27Coordinator,
    action: str,
    catchphrase: str | None,
    entity_id: str | None,
) -> None:
    """action: "light_all_on"/"light_all_off".

    Since Etappe 5, execution goes through core.executor.ActionExecutor
    (light.set_power + target all_allowed) via
    providers.code27.frontend.Code27FrontendAdapter, instead of calling
    light.turn_on/turn_off directly with the legacy entity_id: "all"
    sentinel. CODE27's own frontend is unrestricted (see
    providers/code27/frontend.py), so "all_allowed" still reaches every exposed
    light - same effective reach as the old sentinel, confirmed live
    against testzwerg. No working Assist sentence for "all lights, no
    area" was ever found on this HA version (see the git history of
    this function for that investigation) - that's why this bypasses
    Assist's NLU for just this one command rather than depending on a
    sentence that doesn't work."""
    _COMMAND_RECOGNIZED.set(True)
    request = _current_request(hass, entry, coordinator)
    execution = await Code27FrontendAdapter(hass).set_all_lights(on=action == "light_all_on")

    if execution.status is not ExecutionStatus.SUCCESS:
        _LOGGER.warning("%s: light_all action failed: %s", LOGBOOK_NAME, execution.reason_code)
        error_speech = _msg(hass, "light_all_error", error=execution.reason_code)
        _log_activity(hass, entry, error_speech, entity_id)
        await _output_speech(hass, entry, coordinator, error_speech, entity_id, request=request)
        return

    lang = request.language.lower()
    on_word = "an" if lang.startswith("de") else "on"
    off_word = "aus" if lang.startswith("de") else "off"
    state_word = on_word if action == "light_all_on" else off_word
    _log_activity(hass, entry, _msg(hass, "light_all_done", state=state_word), entity_id)
    speech = catchphrase or _msg(hass, "light_all_done", state=state_word)
    await _output_speech(hass, entry, coordinator, speech, entity_id, request=request)


_TTS_POLL_INTERVAL = 0.35
_TTS_TIMEOUT = 15.0  # matches the device web UI's own audition timeout


async def async_speak_text(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: Code27Coordinator,
    text: str,
    entity_id: str | None = None,
    *, expected_slot: int | None = None,
) -> None:
    """Speak arbitrary text out loud on the device via the TTS preview
    endpoint (`tts_audition`), using the active character's configured
    voice. The device speaks in whichever character is currently active
    regardless of the voice_id/group_key we send, so there is no way to
    pick a different character's voice for a one-off announcement - always
    resolve the active slot rather than accepting an override that would
    silently do nothing.

    Shared entry point: the Assist bridge calls this for the "speak the
    reply" option above, and the `hase.speak` service calls it directly
    (e.g. from Node-RED) - independent of the Assist bridge being enabled
    at all. Never touches the chat, only plays audio on the device."""
    if entity_id is None:
        entity_id = _bridge_entity_id(hass, entry)

    slot = coordinator.data.current_slot.get("slot_number", 1)
    if expected_slot is not None:
        # TTS has no slot-addressed endpoint. Do not speak an old request
        # through a newly selected character, or change the user's selection.
        try:
            active = await coordinator.client.get_current_slot()
        except Exception as err:
            _LOGGER.debug("Cannot verify character for bridge speech: %s", err)
            return
        if not isinstance(active, dict) or active.get("slot_number") != expected_slot or coordinator.stopping:
            _LOGGER.debug("Skipping bridge speech after character switch")
            return
        slot = expected_slot
    settings = next(
        (s for s in coordinator.data.character_slots if s.get("slot_number") == slot),
        None,
    ) or {}
    voice_id = settings.get("voice_id")
    if not voice_id:
        _log_activity(hass, entry, _msg(hass, "tts_no_voice"), entity_id)
        return

    try:
        await coordinator.client.tts_audition(voice_id, text, settings.get("group_key") or "")
    except Exception as err:  # noqa: BLE001 - never let this take the entry down
        _LOGGER.warning("%s: could not start TTS: %s", LOGBOOK_NAME, err)
        _log_activity(hass, entry, _msg(hass, "tts_error", error=err), entity_id)
        return

    _log_activity(hass, entry, _msg(hass, "tts_speaking"), entity_id)

    # A fixed poll *count* (the previous approach) is not a real deadline:
    # each get_tts_audition_state() call is itself allowed up to its own
    # HTTP timeout (10s, see api.py), so N polls could in the worst case
    # take N * (poll_interval + 10s) - 43 polls, over 7 minutes, not the
    # "~15s" the old comment claimed. asyncio.timeout() bounds the *whole*
    # polling phase by wall-clock time instead, regardless of how many
    # iterations that takes or how slow any individual call is.
    try:
        async with asyncio.timeout(_TTS_TIMEOUT):
            while True:
                await asyncio.sleep(_TTS_POLL_INTERVAL)
                try:
                    state = (await coordinator.client.get_tts_audition_state()).get("state")
                except Exception:  # noqa: BLE001 - a missed poll shouldn't abort the wait
                    continue
                state = (state or "").upper()
                if state == "ENDED":
                    _log_activity(hass, entry, _msg(hass, "tts_ok"), entity_id)
                    return
                if state == "FAILED":
                    _log_activity(hass, entry, _msg(hass, "tts_failed"), entity_id)
                    return
    except TimeoutError:
        # Never silently stop - the previous version fell off the end of
        # its loop here with no log line at all, leaving "Antwort wird
        # laut ausgesprochen ..." as the last thing anyone ever saw for a
        # playback that in fact never confirmed ENDED or FAILED.
        _log_activity(hass, entry, _msg(hass, "tts_timeout"), entity_id)
