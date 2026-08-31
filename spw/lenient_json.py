from __future__ import annotations

import json
from typing import Any

_WHITESPACE = " \t\r\n"


def _strip_comments(text: str) -> str:
    """Remove `#` and `//` line comments, but never inside a quoted string."""

    result: list[str] = []
    in_string = False
    escape = False
    i = 0
    length = len(text)
    while i < length:
        char = text[i]
        if in_string:
            result.append(char)
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            i += 1
            continue
        if char == '"':
            in_string = True
            result.append(char)
            i += 1
            continue
        if char == "#" or (char == "/" and i + 1 < length and text[i + 1] == "/"):
            while i < length and text[i] not in "\r\n":
                i += 1
            continue
        result.append(char)
        i += 1
    return "".join(result)


def _convert_single_quoted_strings(text: str) -> str:
    """Rewrite `'...'` string tokens (outside double-quoted strings) as `"..."`.

    Observed in real `mod_info.json` files for scalar-looking values, e.g.
    `{"major": '1', "minor": '5'}`. An apostrophe inside a normal
    double-quoted string (e.g. an author's name) is left untouched because
    conversion only happens while not already inside a `"..."` span.
    """

    result: list[str] = []
    in_double = False
    i = 0
    length = len(text)
    while i < length:
        char = text[i]
        if in_double:
            result.append(char)
            if char == "\\" and i + 1 < length:
                result.append(text[i + 1])
                i += 2
                continue
            if char == '"':
                in_double = False
            i += 1
            continue
        if char == '"':
            in_double = True
            result.append(char)
            i += 1
            continue
        if char == "'":
            j = i + 1
            content: list[str] = []
            while j < length and text[j] != "'":
                if text[j] == "\\" and j + 1 < length:
                    content.append(text[j])
                    content.append(text[j + 1])
                    j += 2
                    continue
                content.append(text[j])
                j += 1
            inner = "".join(content).replace("\\", "\\\\").replace('"', '\\"')
            result.append(f'"{inner}"')
            i = j + 1
            continue
        result.append(char)
        i += 1
    return "".join(result)


def _strip_trailing_commas(text: str) -> str:
    """Remove a comma that is immediately followed (ignoring whitespace) by `}` or `]`, outside strings."""

    result: list[str] = []
    in_string = False
    escape = False
    i = 0
    length = len(text)
    while i < length:
        char = text[i]
        if in_string:
            result.append(char)
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            i += 1
            continue
        if char == '"':
            in_string = True
            result.append(char)
            i += 1
            continue
        if char == ",":
            lookahead = i + 1
            while lookahead < length and text[lookahead] in _WHITESPACE:
                lookahead += 1
            if lookahead < length and text[lookahead] in "}]":
                i += 1
                continue
        result.append(char)
        i += 1
    return "".join(result)


def loads(text: str) -> Any:
    """Parse JSON that tolerates `#`/`//` line comments and trailing commas.

    Starsector's own `mod_info.json` files are written by hand and read by
    a lenient in-game parser: `#` and `//` comments, trailing commas before
    `}`/`]`, and single-quoted scalar values are all common in real
    installations (observed directly against a live installation with 165
    mod directories, where two-thirds of `mod_info.json` files used at
    least one of these). Strict `json.loads` rejects all of them as
    invalid, which would misreport a large majority of real mods as having
    broken metadata. This first tries strict parsing (the common,
    well-formed case is not slowed down by unnecessary rewriting) and
    falls back to a string-aware rewrite before parsing again.
    """

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Comments must be stripped before single-quote conversion: an
    # apostrophe in ordinary comment text (e.g. "Starsector's parser")
    # would otherwise be misread as the start of a quoted string, since
    # single-quote conversion has no notion of `#`/`//` comments of its
    # own.
    without_comments = _strip_comments(text)
    without_single_quotes = _convert_single_quoted_strings(without_comments)
    return json.loads(_strip_trailing_commas(without_single_quotes))
