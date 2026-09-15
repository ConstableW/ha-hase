"""Language-only book fields and deterministic fallback shared by Studio/runtime."""
import re
from copy import deepcopy

TEXT_FIELDS = ("name", "description", "trigger", "output", "catchphrase")
LIST_FIELDS = ("triggers", "examples")


def resolve(value, requested="en", default=None, technical=None):
    """Return value, selected language and reason; never choose an arbitrary language."""
    if not isinstance(value, dict):
        return (value, None, "shared") if value else (technical, None, "technical")
    available = {k: v for k, v in value.items() if v}
    def key_for(language):
        normalized = str(language or "").lower().replace("_", "-")
        return next((k for k in available if k.lower().replace("_", "-") == normalized), None) or next(
            (k for k in available if k.lower() == normalized.split("-")[0]), None)
    for language, reason in ((requested, "requested"), ("en", "english"), (default, "default")):
        key = key_for(language)
        if key:
            return available[key], key, reason
    if len(available) == 1:
        key = next(iter(available))
        return available[key], key, "only"
    return technical, None, "technical"


def text(value, requested="en", default=None, technical=None):
    return resolve(value, requested, default, technical)[0]


def default_language(book):
    explicit = book.get("default_language") or book.get("language")
    if explicit:
        return explicit
    found = {key for obj in language_objects(book) if isinstance(obj, dict) for field in TEXT_FIELDS + LIST_FIELDS
             if isinstance(obj.get(field), dict) for key in obj[field]}
    return next(iter(found)) if len(found) == 1 else "en"


def language_objects(book):
    yield book
    commands = book.get("commands", [])
    if not isinstance(commands, list):
        return
    for spell in commands:
        yield spell
        if isinstance(spell, dict) and isinstance(spell.get("expert"), dict):
            yield spell["expert"]


def languages(book):
    found = set(book.get("languages", []))
    for obj in language_objects(book):
        for field in (*TEXT_FIELDS, *LIST_FIELDS):
            if isinstance(obj.get(field), dict):
                found.update(obj[field])
    found.add(default_language(book))
    return sorted(found)


def manage_language(book, action, language, source=None):
    if not isinstance(language, str) or not re.fullmatch(r"[a-z]{2,3}(?:-[a-z0-9]{2,8})*", language):
        raise ValueError("Invalid language code")
    result = deepcopy(book)
    current = languages(book)
    default = default_language(book)
    if action == "default":
        if language not in current:
            raise ValueError("Language does not exist")
        # Changing the default must not relabel legacy, single-language content.
        for obj in language_objects(result):
            for field in TEXT_FIELDS + LIST_FIELDS:
                if field in obj and not isinstance(obj[field], dict):
                    obj[field] = {default: obj[field]}
        result["default_language"] = language
        result["language"] = language
        return result
    if action == "remove" and (language == default or len(current) == 1):
        raise ValueError("Choose another default language before removing this language")
    if action == "remove" and language not in current:
        raise ValueError("Language does not exist")
    if action == "add" and (language in current or source and source not in current):
        raise ValueError("Language already exists or template language is missing")
    if action not in {"add", "remove"}:
        raise ValueError("Unknown language action")
    for obj in language_objects(result):
        for field in (*TEXT_FIELDS, *LIST_FIELDS):
            if field not in obj:
                continue
            value = obj[field]
            if not isinstance(value, dict):
                value = {default: value}
                obj[field] = value
            if action == "remove":
                value.pop(language, None)
            elif source and source in value:
                value[language] = deepcopy(value[source])
    result["languages"] = sorted((set(current) | {language}) if action == "add" else set(current) - {language})
    result["default_language"] = default
    return result


def translation_report(book, requested):
    rows = []
    default = default_language(book)
    for obj in language_objects(book):
        for field in TEXT_FIELDS + LIST_FIELDS:
            if field not in obj:
                continue
            _, selected, reason = resolve(obj[field], requested, default)
            rows.append({"id": obj.get("id", book["id"]), "field": field,
                         "requested": requested, "used": selected, "reason": reason})
    return rows
