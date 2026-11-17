"""Turn recipient phrases into email addresses, from the admin's list only.

The address book lives in the settings (Admin -> Actions): entries with a
name, email, role and aliases ("western manager"). A phrase resolves when it
matches an entry's email, full name, first name (if only one entry has it),
an alias or the role. An address typed in full is accepted only if its
domain is on the allowed-domain list or it is in the book. "me" is the
signed-in Microsoft account. Anything else stays unresolved, and the draft
cannot be approved until a recipient is chosen.
"""

import re

SELF_WORDS = {"me", "myself", "my inbox", "my email", "my mailbox", "self"}
_EMAIL = re.compile(r"^[A-Za-z0-9._%+'-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")


def _norm(text):
    text = re.sub(r"[^a-z0-9@._' -]", " ", (text or "").lower())
    text = re.sub(r"\b(?:the|our|my|mr|mrs|ms|dr)\b\.?", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def domain_allowed(address, allowed_domains):
    domain = address.rsplit("@", 1)[-1].lower()
    return any(domain == d or domain.endswith("." + d) for d in allowed_domains)


def is_email(text):
    return bool(_EMAIL.match((text or "").strip()))


def resolve(phrases, book, allowed_domains=(), self_address=""):
    """Return ``(recipients, unresolved)``.

    ``recipients`` is a list of ``{"name", "email"}`` without duplicates;
    ``unresolved`` lists the phrases (with a reason) that matched nothing.
    """

    recipients, unresolved, seen = [], [], set()

    def add(name, email):
        if email.lower() not in seen:
            seen.add(email.lower())
            recipients.append({"name": name, "email": email.lower()})

    for phrase in phrases:
        key = _norm(phrase)
        if not key:
            continue
        if phrase.strip().lower() in SELF_WORDS or key in SELF_WORDS:
            if self_address:
                add("Me", self_address)
            else:
                unresolved.append({"phrase": phrase, "reason": "your own address "
                                   "is not known (set the Power BI user in Admin)"})
            continue
        if is_email(phrase):
            entry = next((e for e in book if e["email"] == phrase.strip().lower()), None)
            if entry:
                add(entry["name"], entry["email"])
            elif domain_allowed(phrase, allowed_domains):
                add(phrase.strip(), phrase.strip())
            else:
                unresolved.append({"phrase": phrase, "reason": "not in the address "
                                   "book and not on an allowed domain"})
            continue
        matches = _match(key, book)
        if len(matches) == 1:
            add(matches[0]["name"], matches[0]["email"])
        elif len(matches) > 1:
            unresolved.append({"phrase": phrase, "reason": "matches "
                               + ", ".join(m["name"] for m in matches)
                               + "; choose one"})
        else:
            unresolved.append({"phrase": phrase, "reason": "not in the address book"})
    return recipients, unresolved


def _match(key, book):
    exact = [e for e in book if key in {
        _norm(e["name"]), _norm(e["email"]), _norm(e.get("role", "")),
        *(_norm(a) for a in e.get("aliases", []))} - {""}]
    if exact:
        return exact
    first = [e for e in book if _norm(e["name"]).split(" ")[:1] == [key]]
    if first:
        return first
    # "western region manager" vs role "Western Region Manager" handled
    # above; "western manager" vs role "Western Region Manager": every word
    # of the phrase appears in the role or an alias.
    words = set(key.split())
    if len(words) >= 2:
        return [e for e in book if any(
            words <= set(_norm(text).split())
            for text in [e.get("role", ""), *e.get("aliases", [])] if text)]
    return []


def allowed(address, book, allowed_domains=(), self_address=""):
    """True when ``address`` may receive mail: in the book, on an allowed
    domain, or the user's own address."""

    address = (address or "").strip().lower()
    if not is_email(address):
        return False
    if self_address and address == self_address.lower():
        return True
    return (any(e["email"] == address for e in book)
            or domain_allowed(address, allowed_domains))
