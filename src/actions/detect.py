"""Recognise action requests in a chat message, with rules only.

An action request starts with an action verb ("email ...", "post ...",
"create a task ...", "export ..."), after optional politeness ("please",
"can you"). Questions ("how many ...", "show ...", "share of sales by
region") are never actions. No model is involved: the model cannot invent
an action, a recipient or a message.

``parse(text, today)`` returns ``None`` for a question, or a dict:

* ``kind``        email | teams_post | task | export
* ``question``    a data question inside the request ("email net sales by
                  region to Sara" -> "net sales by region"), else ``None``
* ``recipients``  recipient phrases as written ("Sara", "the western manager")
* ``note``        the user's own note ("... saying please review"), or ""
* ``format``      xlsx | csv (export, and the email attachment)
* ``per_row``     one task per row ("a task for every store ...")
* ``row_filter``  ``{"op": "<"|">"|"<="|">=", "value": float, "percent": bool}``
* ``task_text``   what the task says ("call the Riyadh manager"), or ""
* ``due``         a ``datetime.date`` or ``None``
"""

import datetime as dt
import re

_POLITE = re.compile(
    r"^(?:(?:ok(?:ay)?|now|and|then|also|please|pls|kindly|hey|"
    r"can you|could you|would you|will you|i want you to|i'd like you to|"
    r"i would like you to|go ahead and|let's|lets)\b[\s,]*)+", re.I)

# A reference to the answer already on screen.
REFERENCE = re.compile(
    r"^(?:it|this|that|these|those|them|above|the above|"
    r"(?:this|that|the|these|those|my|our)\s+(?:last\s+|latest\s+|previous\s+)?"
    r"(?:table|answer|result|results|chart|graph|numbers|figures|data|list|"
    r"report|analysis|summary|insight|insights|breakdown|ranking)"
    r"(?:\s+(?:above|below))?)$", re.I)

_TASK_WORDS = r"\b(?:tasks?|to-?dos?|todos?|reminders?|follow[- ]?ups?)\b"
_FILE_WORDS = r"\b(?:excel|xlsx|csv|spreadsheet|workbook)\b"
_NOTE = re.compile(
    r"\s*(?:,\s*)?(?:\b(?:saying|that says|and say|and tell (?:them|him|her)|"
    r"with (?:a |the )?(?:note|message)|note|message)\b\s*:?\s*|:\s+)(?P<note>.+)$",
    re.I)
_FILTER = re.compile(
    r"\b(?P<op>below|under|less than|lower than|above|over|more than|"
    r"greater than|higher than|at least|at most)\s+(?P<value>-?\d+(?:\.\d+)?)"
    r"\s*(?P<pct>%|percent)?", re.I)
_OPS = {"below": "<", "under": "<", "less than": "<", "lower than": "<",
        "above": ">", "over": ">", "more than": ">", "greater than": ">",
        "higher than": ">", "at least": ">=", "at most": "<="}
_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday",
             "saturday", "sunday"]
_DUE = re.compile(
    r"\s*\b(?:by|due|before|on|for)\s+(?P<when>today|tomorrow|tonight|"
    r"next week|end of (?:the )?(?:week|month)|(?:next\s+)?(?:" + "|".join(_WEEKDAYS)
    + r")|\d{4}-\d{2}-\d{2})\b|\s*\bin\s+(?P<days>\d{1,3})\s+days?\b", re.I)


def _strip_polite(text):
    return _POLITE.sub("", text.strip()).strip()


def _clean(text):
    return re.sub(r"\s+", " ", text).strip(" .,!?;:")


def is_reference(text):
    text = _clean(text)
    return not text or bool(REFERENCE.match(text))


def kind_of(text):
    """The action kind a message asks for, or ``None`` for a question."""

    body = _strip_polite(text).lower()
    first = re.match(r"[a-z-]+", body)
    if not first:
        return None
    verb = first.group(0)
    rest = body[len(verb):]
    if not rest.strip(" .!?"):
        return None                          # a bare "Send" or "Email" is not a request
    if verb in ("export", "download"):
        return "export"
    if verb == "save":
        return "export" if (re.search(_FILE_WORDS, rest)
                            or re.match(r"\s+(?:it|this|that|the)\b", rest)) else None
    if verb == "remind":
        return "task"
    if verb in ("create", "add", "make", "set", "open", "raise", "log"):
        if re.search(_TASK_WORDS, rest):
            return "task"
        if verb in ("create", "make") and re.search(_FILE_WORDS, rest):
            return "export"
        return None
    if verb in ("give",) and re.search(_FILE_WORDS, rest):
        return "export"
    teams = bool(re.search(r"\b(?:teams|channel)\b", rest))
    if verb == "post":
        # "post-holiday sales" is not a request; "post this ..." is.
        return "teams_post" if re.match(r"\s+\S", rest) else None
    if verb in ("share", "send", "forward", "publish"):
        if verb == "share" and re.match(r"\s+of\b", rest):
            return None                      # "share of sales by region"
        if teams:
            return "teams_post"
        if verb == "publish":
            return None
        if verb == "share" and not re.search(r"\b(?:with|to)\b", rest):
            return None
        return "email"
    if verb in ("email", "e-mail", "mail"):
        return "email"
    if verb in ("notify", "inform", "tell", "let", "cc"):
        if verb == "let" and not re.search(r"\bknow\b", rest):
            return None
        return "teams_post" if teams else "email"
    return None


def _note(text):
    match = _NOTE.search(text)
    if not match:
        return text, ""
    return text[:match.start()], _clean(match.group("note"))


def _due(text, today):
    match = _DUE.search(text)
    if not match:
        return text, None
    rest = (text[:match.start()] + text[match.end():]).strip()
    if match.group("days"):
        return rest, today + dt.timedelta(days=int(match.group("days")))
    when = match.group("when").lower()
    if when in ("today", "tonight"):
        return rest, today
    if when == "tomorrow":
        return rest, today + dt.timedelta(days=1)
    if when == "next week":
        return rest, today + dt.timedelta(days=7 - today.weekday())
    if when.startswith("end of"):
        if when.endswith("month"):
            first_next = (today.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
            return rest, first_next - dt.timedelta(days=1)
        return rest, today + dt.timedelta(days=(4 - today.weekday()) % 7)
    if re.match(r"\d{4}-", when):
        try:
            return rest, dt.date.fromisoformat(when)
        except ValueError:
            return text, None
    day = when.split()[-1]
    ahead = (_WEEKDAYS.index(day) - today.weekday()) % 7 or 7
    if when.startswith("next "):
        ahead += 0 if ahead >= 7 else 7
    return rest, today + dt.timedelta(days=ahead)


def _split_recipients(text):
    text = _clean(text)
    if not text:
        return []
    parts = re.split(r"\s*(?:,|;|&|\band\b|\bplus\b)\s*", text)
    return [_clean(p) for p in parts if _clean(p)]


def _row_filter(text):
    match = _FILTER.search(text)
    if not match:
        return None
    return {"op": _OPS[match.group("op").lower()], "value": float(match.group("value")),
            "percent": bool(match.group("pct"))}


def parse(text, today=None):
    """See the module docstring. ``None`` means "not an action request"."""

    kind = kind_of(text or "")
    if kind is None:
        return None
    today = today or dt.date.today()
    body = _strip_polite(text)
    verb = re.match(r"[A-Za-z-]+", body).group(0)
    rest = body[len(verb):].strip()
    rest, note = _note(rest)
    result = {"kind": kind, "question": None, "recipients": [], "note": note,
              "format": "csv" if re.search(r"\bcsv\b", text, re.I) else "xlsx",
              "per_row": False, "row_filter": None, "task_text": "", "due": None}

    if kind == "task":
        rest, result["due"] = _due(rest, today)
        result["per_row"] = bool(re.search(r"\b(?:every|each|per)\b", rest, re.I))
        result["row_filter"] = _row_filter(rest)
        # "remind me to X", "create a task to X", "add a reminder: X"
        what = re.search(r"\bto\s+(?P<what>(?!(?:me|my)\b).+?)"
                         r"(?=\s+\b(?:for|per)\s+(?:every|each)\b|$)", rest, re.I)
        if what:
            text_ = _clean(what.group("what"))
            if not re.match(r"^(?:my\s+)?(?:to-?do|tasks?)(?:\s+list)?$", text_, re.I):
                result["task_text"] = text_
        return result

    if kind == "export":
        rest = re.sub(r"\b(?:as|to|into|in)\s+(?:an?\s+)?(?:excel|xlsx|csv)"
                      r"(?:\s+(?:file|sheet|spreadsheet|workbook))?\b", "", rest, flags=re.I)
        rest = re.sub(r"\b(?:an?\s+)?(?:excel|xlsx|csv)\s+(?:file|sheet|spreadsheet|"
                      r"workbook)\s+(?:of|with|for)\b", "", rest, flags=re.I)
        rest = re.sub(r"^(?:me\s+)?(?:an?\s+)?(?:excel|xlsx|csv|spreadsheet|"
                      r"workbook)(?:\s+(?:file|sheet))?\s*(?:of|with|for)?\b", "",
                      rest, flags=re.I)
        if not is_reference(rest):
            result["question"] = _clean(rest)
        return result

    # email / teams_post: "<verb> <what> to|with <who>", "let <who> know",
    # "notify <who> (about <what>)".
    attach = re.search(r"\b(?:as|with)\s+(?:an?\s+)?(?:excel|xlsx|csv)(?:\s+"
                       r"(?:file|attachment))?\b", rest, re.I)
    if attach:
        rest = (rest[:attach.start()] + rest[attach.end():]).strip()
    what, who = rest, ""
    if verb.lower() in ("let", "tell", "notify", "inform", "cc"):
        match = re.match(r"(?P<who>.+?)\s+(?:know\s+)?(?:about|of|regarding)\s+"
                         r"(?P<what>.+)$", rest, re.I)
        if match:
            who, what = match.group("who"), match.group("what")
        else:
            who, what = re.sub(r"\s+know$", "", rest, flags=re.I), ""
    else:
        match = re.match(r"(?P<what>.*?)\s*\b(?:to|with)\s+(?P<who>.+)$", rest, re.I)
        if match:
            what, who = match.group("what"), match.group("who")
        elif re.match(r"^(?:me|myself)\b", rest, re.I):
            who, what = "me", rest.split(None, 1)[1] if " " in rest else ""
    if kind == "teams_post":
        # The channel is fixed in Admin; channel words are not recipients.
        who = ""
        what = re.sub(r"\b(?:on|in|into|to)\s+(?:the\s+)?(?:\w+\s+){0,3}?"
                      r"(?:teams|channel)(?:\s+channel)?\b.*$", "", what, flags=re.I)
    result["recipients"] = _split_recipients(re.sub(r"\b(?:the|our|my)\s+", "", who,
                                                    flags=re.I)) if who else []
    if what and not is_reference(what):
        result["question"] = _clean(what)
    return result
