"""The user store: accounts, passwords, permissions and folder access.

Stored in ``config/users.json`` (gitignored, like the other settings):

    {"version": 1,
     "policy": {"min_password_length": 10, ...},
     "users": {"sara": {"display_name": "Sara Ali",
                        "password": "scrypt$16384$8$1$<salt>$<hash>",
                        "permissions": ["power_bi", "documents"],
                        "folders": ["102"], "all_folders": false,
                        "active": true, "must_change_password": true,
                        "failed_attempts": 0, "locked_until": null,
                        "created": "...", "last_sign_in": "..."}}}

Passwords are hashed with scrypt (standard library) and a random salt per
user; they are never stored or logged in clear. Every function here takes
the loaded ``data`` dict and changes it; the caller saves it with
``save``. Rules that keep the app manageable are enforced here, not in the
page: usernames are unique, at least one active account can always manage
users, and nobody can delete or lock out their own account.
"""

import base64
import copy
import datetime as dt
import hashlib
import hmac
import json
import os
import re
import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
USERS_FILE = ROOT / "config" / "users.json"

# The parameters an administrator assigns to each user. The keys are stored;
# the labels are shown on the Users page.
PERMISSIONS = {
    "power_bi": "Ask Power BI questions (the main chat and report)",
    "documents": "Use the Documents page (only the folders assigned below)",
    "actions": "Email, reminders and exports from the chat",
    "evaluation": "Evaluation dashboard",
    "admin_settings": "Admin settings page (models, checks, Power BI, actions)",
    "manage_users": "Users and folder access page",
}
ALL_PERMISSIONS = tuple(PERMISSIONS)

POLICY_DEFAULTS = {
    "min_password_length": 10,
    "max_failed_attempts": 5,
    "lockout_minutes": 15,
    "session_idle_minutes": 480,
}
POLICY_LIMITS = {
    "min_password_length": (8, 128),
    "max_failed_attempts": (3, 20),
    "lockout_minutes": (1, 1440),
    "session_idle_minutes": (5, 10080),
}

USERNAME = re.compile(r"[a-z0-9][a-z0-9._@-]{1,63}")
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2 ** 14, 8, 1
FOLDER_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")

# A hash to check against when the username does not exist, so a wrong
# username takes as long as a wrong password.
_DUMMY = None


class AccessError(ValueError):
    """A change to users that is not allowed (shown to the admin as is)."""


# ------------------------------------------------------------ passwords

def _b64(raw):
    return base64.b64encode(raw).decode("ascii")


def hash_password(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=SCRYPT_N,
                            r=SCRYPT_R, p=SCRYPT_P, maxmem=64 * 1024 * 1024, dklen=32)
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${_b64(salt)}${_b64(digest)}"


def verify_password(password, stored):
    try:
        scheme, n, r, p, salt, digest = str(stored).split("$")
        if scheme != "scrypt":
            return False
        expected = base64.b64decode(digest)
        actual = hashlib.scrypt(str(password).encode("utf-8"),
                                salt=base64.b64decode(salt), n=int(n), r=int(r),
                                p=int(p), maxmem=64 * 1024 * 1024, dklen=len(expected))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


def password_problem(password, username="", policy=None):
    """Why ``password`` is not acceptable, or ``None``."""

    policy = {**POLICY_DEFAULTS, **(policy or {})}
    password = password or ""
    if len(password) < policy["min_password_length"]:
        return f"The password needs at least {policy['min_password_length']} characters."
    if len(password) > 256:
        return "The password is too long (256 characters at most)."
    if not (re.search(r"[A-Za-z]", password) and re.search(r"[^A-Za-z]", password)):
        return "The password needs letters and at least one digit or symbol."
    if username and username.lower() in password.lower():
        return "The password must not contain the username."
    return None


# ----------------------------------------------------------------- store

def empty():
    return {"version": 1, "policy": dict(POLICY_DEFAULTS), "users": {}}


def load(path=None):
    path = Path(path) if path else USERS_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return empty()
    except (OSError, ValueError) as exc:
        # A damaged file must not open the app to everyone: refuse to run.
        raise RuntimeError(f"The users file {path} cannot be read ({exc}). "
                           "Restore it from a backup or remove it to set up "
                           "the first administrator again.") from exc
    out = empty()
    out["policy"] = clean_policy(data.get("policy") or {})
    for name, user in (data.get("users") or {}).items():
        out["users"][name] = _normal_user(user)
    return out


def save(data, path=None):
    path = Path(path) if path else USERS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(temp, path)


def stamp(path=None):
    path = Path(path) if path else USERS_FILE
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return None


def clean_policy(policy):
    out = dict(POLICY_DEFAULTS)
    for key, (low, high) in POLICY_LIMITS.items():
        try:
            value = int(policy.get(key, out[key]))
        except (TypeError, ValueError):
            continue
        out[key] = min(max(value, low), high)
    return out


def _normal_user(user):
    return {
        "display_name": str(user.get("display_name") or ""),
        "password": str(user.get("password") or ""),
        "permissions": [p for p in ALL_PERMISSIONS if p in (user.get("permissions") or [])],
        "folders": sorted({str(f) for f in user.get("folders") or []
                           if FOLDER_ID.fullmatch(str(f))}),
        "all_folders": bool(user.get("all_folders")),
        "active": bool(user.get("active", True)),
        "must_change_password": bool(user.get("must_change_password")),
        "failed_attempts": int(user.get("failed_attempts") or 0),
        "locked_until": user.get("locked_until"),
        "created": user.get("created"),
        "last_sign_in": user.get("last_sign_in"),
    }


def _now(now=None):
    return now or dt.datetime.now()


def has_users(data):
    return bool(data["users"])


def clean_username(name):
    text = str(name or "").strip().lower()
    if not USERNAME.fullmatch(text):
        raise AccessError("A username has 2-64 characters: letters, digits, "
                          "'.', '_', '-' or '@', starting with a letter or digit.")
    return text


def get(data, username):
    return data["users"].get(str(username or "").strip().lower())


def _managers(data):
    return [n for n, u in data["users"].items()
            if u["active"] and "manage_users" in u["permissions"]]


def _require_manager_left(data, before):
    if not _managers(data):
        data["users"].clear()
        data["users"].update(before)
        raise AccessError("At least one active account must keep the "
                          "'Users and folder access' permission.")


# ----------------------------------------------------------------- users

def create_user(data, username, password, display_name="", permissions=(),
                folders=(), all_folders=False, must_change=True, now=None):
    username = clean_username(username)
    if username in data["users"]:
        raise AccessError(f"The username '{username}' is already taken.")
    problem = password_problem(password, username, data["policy"])
    if problem:
        raise AccessError(problem)
    unknown = set(permissions) - set(ALL_PERMISSIONS)
    if unknown:
        raise AccessError("Unknown permission: " + ", ".join(sorted(unknown)))
    data["users"][username] = _normal_user({
        "display_name": display_name.strip()[:80] or username,
        "password": hash_password(password),
        "permissions": list(permissions),
        "folders": _clean_folders(folders),
        "all_folders": all_folders,
        "must_change_password": must_change,
        "created": _now(now).isoformat(timespec="seconds"),
    })
    return username


def create_first_admin(data, username, password, display_name="", now=None):
    if has_users(data):
        raise AccessError("An administrator already exists.")
    return create_user(data, username, password, display_name, ALL_PERMISSIONS,
                       all_folders=True, must_change=False, now=now)


def update_user(data, username, acting, display_name=None, permissions=None,
                active=None, all_folders=None, folders=None):
    user = get(data, username)
    if user is None:
        raise AccessError(f"There is no user '{username}'.")
    before = copy.deepcopy(data["users"])
    if display_name is not None:
        user["display_name"] = display_name.strip()[:80] or username
    if permissions is not None:
        unknown = set(permissions) - set(ALL_PERMISSIONS)
        if unknown:
            raise AccessError("Unknown permission: " + ", ".join(sorted(unknown)))
        if username == acting and "manage_users" not in permissions:
            raise AccessError("You cannot remove your own 'Users and folder "
                              "access' permission. Ask another administrator.")
        user["permissions"] = [p for p in ALL_PERMISSIONS if p in permissions]
    if active is not None:
        if username == acting and not active:
            raise AccessError("You cannot deactivate your own account.")
        user["active"] = bool(active)
        if active:
            user["failed_attempts"], user["locked_until"] = 0, None
    if all_folders is not None:
        user["all_folders"] = bool(all_folders)
    if folders is not None:
        user["folders"] = _clean_folders(folders)
    _require_manager_left(data, before)
    return user


def delete_user(data, username, acting):
    if username == acting:
        raise AccessError("You cannot delete your own account.")
    if username not in data["users"]:
        raise AccessError(f"There is no user '{username}'.")
    before = copy.deepcopy(data["users"])
    del data["users"][username]
    _require_manager_left(data, before)


def set_password(data, username, password, must_change=False):
    user = get(data, username)
    if user is None:
        raise AccessError(f"There is no user '{username}'.")
    problem = password_problem(password, username, data["policy"])
    if problem:
        raise AccessError(problem)
    if user["password"] and verify_password(password, user["password"]):
        raise AccessError("Choose a password different from the current one.")
    user["password"] = hash_password(password)
    user["must_change_password"] = bool(must_change)
    user["failed_attempts"], user["locked_until"] = 0, None


def unlock(data, username):
    user = get(data, username)
    if user:
        user["failed_attempts"], user["locked_until"] = 0, None


def locked(user, now=None):
    until = user.get("locked_until")
    if not until:
        return False
    try:
        return dt.datetime.fromisoformat(until) > _now(now)
    except ValueError:
        return False


def authenticate(data, username, password, now=None):
    """Check a sign-in. Returns ``(username or None, event, message)``;
    ``event`` is a security-log event. Changes the failure counters in
    ``data`` (the caller saves)."""

    global _DUMMY
    now = _now(now)
    try:
        name = clean_username(username)
    except AccessError:
        name = ""
    user = data["users"].get(name)
    generic = "Wrong username or password."
    if user is None:
        _DUMMY = _DUMMY or hash_password(secrets.token_hex(8))
        verify_password(password, _DUMMY)
        return None, "sign_in_failed", generic
    if locked(user, now):
        return None, "sign_in_failed", ("Too many failed attempts: this account is "
                                        "locked for a while. Try again later or ask "
                                        "an administrator to unlock it.")
    if not verify_password(password, user["password"]):
        user["failed_attempts"] += 1
        policy = data["policy"]
        if user["failed_attempts"] >= policy["max_failed_attempts"]:
            user["locked_until"] = (now + dt.timedelta(
                minutes=policy["lockout_minutes"])).isoformat(timespec="seconds")
            user["failed_attempts"] = 0
            return None, "locked", (f"Too many failed attempts: the account is locked "
                                    f"for {policy['lockout_minutes']} minutes.")
        return None, "sign_in_failed", generic
    if not user["active"]:
        return None, "sign_in_failed", "This account is deactivated. Ask an administrator."
    user["failed_attempts"], user["locked_until"] = 0, None
    user["last_sign_in"] = now.isoformat(timespec="seconds")
    return name, "signed_in", ""


# ------------------------------------------------------- permissions

def can(user, permission):
    return bool(user and user.get("active") and permission in user.get("permissions", []))


def _clean_folders(folders):
    out = set()
    for folder in folders:
        folder = str(folder).strip()
        if not FOLDER_ID.fullmatch(folder):
            raise AccessError(f"'{folder}' is not a valid folder number.")
        out.add(folder)
    return sorted(out)


def folder_allowed(user, folder_id):
    if not can(user, "documents") or not folder_id:
        return False
    return user.get("all_folders") or folder_id in user.get("folders", [])


def allowed_folders(user, existing):
    """The folders ``user`` may open: of ``existing`` (folders on disk) for
    'all folders', else the assigned ones (also those not created yet)."""

    if not can(user, "documents"):
        return []
    if user.get("all_folders"):
        return sorted(set(existing), key=_folder_sort)
    return sorted(user.get("folders", []), key=_folder_sort)


def _folder_sort(folder):
    return (0, int(folder), folder) if folder.isdigit() else (1, 0, folder.lower())


def folder_users(data, folder_id):
    """Usernames that may open ``folder_id`` (by assignment or 'all')."""
    return [n for n, u in sorted(data["users"].items()) if folder_allowed(u, folder_id)]


def set_folder_access(data, folder_id, usernames):
    """Give exactly ``usernames`` the folder by assignment (users with 'all
    folders' keep it anyway). Returns ``(added, removed)``."""

    folder_id = _clean_folders([folder_id])[0]
    wanted = {str(u).lower() for u in usernames}
    unknown = wanted - set(data["users"])
    if unknown:
        raise AccessError("Unknown user: " + ", ".join(sorted(unknown)))
    added, removed = [], []
    for name, user in data["users"].items():
        has = folder_id in user["folders"]
        if name in wanted and not has:
            user["folders"] = sorted(user["folders"] + [folder_id])
            added.append(name)
        elif name not in wanted and has:
            user["folders"] = [f for f in user["folders"] if f != folder_id]
            removed.append(name)
    return added, removed


def public(user):
    """A copy without the password hash (for the page and the session)."""
    return {k: v for k, v in user.items() if k != "password"}
