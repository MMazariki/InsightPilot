"""Sign-in screens and the account menu (Streamlit).

``require()`` is called once near the top of ``src/main.py``. It returns
the signed-in user, or draws one of these screens and stops the run:

* **Create the administrator account** when no user exists yet (first run).
  That account gets every permission and every document folder.
* **Sign in** otherwise.
* **Choose a new password** when an administrator created the account or
  reset its password.

The session holds only the username. The user is read again from the user
store on every run, so a deactivated account or a removed permission takes
effect on the user's next click. Sessions end after the idle time in the
policy.

Reloading the tab keeps the user signed in: at sign-in the browser gets a
random token in a cookie (``ip_session``, this site only); a new session
with a valid token signs in without the password (``users.resume_session``).
"""

import json
import time

import streamlit as st

from src.security import audit, users

SESSION_KEY = "auth_user"
SEEN_KEY = "auth_seen"
TOKEN_KEY = "auth_token"
COOKIE = "ip_session"
COOKIE_DAYS = 30            # the server-side expiry (idle time) decides earlier
SLIDE_SECONDS = 300         # how often an active session extends its token


def _cookie_token():
    try:
        return st.context.cookies.get(COOKIE)
    except Exception:
        return None


def _emit_cookie():
    """Write or clear the cookie in the browser (pending from a run that
    ended in ``st.rerun``, so it is sent from a run that completes)."""

    pending = st.session_state.pop("auth_cookie", None)
    if pending is None:
        return
    if pending:
        value = (f"{COOKIE}={pending}; Path=/; Max-Age={COOKIE_DAYS * 86400}; "
                 "SameSite=Strict")
    else:
        value = f"{COOKIE}=; Path=/; Max-Age=0; SameSite=Strict"
    st.html(f"<script>document.cookie = {json.dumps(value)};</script>",
            unsafe_allow_javascript=True)


def _remember(data, name):
    token = users.create_session(data, name)
    users.save(data)
    st.session_state[TOKEN_KEY] = token
    st.session_state.auth_cookie = token
    st.session_state.auth_slid = time.time()


def _card():
    left, middle, right = st.columns([1, 1.25, 1])
    return middle.container(border=True, key="auth_card")


def _sign_in(data):
    with _card():
        st.html('<div class="ip-card-title" style="font-size:1.25rem">Sign in</div>'
                '<div class="ip-card-sub">Use the account an administrator created '
                'for you.</div>')
        notice = st.session_state.pop("auth_notice", None)
        if notice:
            st.info(notice, icon=":material/info:")
        with st.form("auth_sign_in", border=False):
            username = st.text_input("Username", key="auth_username",
                                     autocomplete="username")
            password = st.text_input("Password", type="password", key="auth_password",
                                     autocomplete="current-password")
            submitted = st.form_submit_button("Sign in", type="primary",
                                              icon=":material/login:",
                                              width="stretch")
        if submitted:
            name, event, message = users.authenticate(data, username, password)
            users.save(data)
            audit.record(event, user=(name or str(username or "").strip().lower())[:64])
            if name:
                _start_session(name)
                _remember(data, name)
                st.rerun()
            st.error(message, icon=":material/error:")


def _setup(data):
    with _card():
        st.html('<div class="ip-card-title" style="font-size:1.25rem">Create the '
                'administrator account</div><div class="ip-card-sub">No account '
                'exists yet. This first account gets every permission and access to '
                'every document folder; it can then create the other users.</div>')
        with st.form("auth_setup", border=False):
            username = st.text_input("Username", key="auth_setup_username",
                                     placeholder="e.g. admin", autocomplete="username")
            display = st.text_input("Display name", key="auth_setup_display")
            password = st.text_input("Password", type="password", key="auth_setup_pw",
                                     autocomplete="new-password",
                                     help=f"At least {data['policy']['min_password_length']} "
                                          "characters, with letters and a digit or symbol.")
            confirm = st.text_input("Repeat the password", type="password",
                                    key="auth_setup_pw2", autocomplete="new-password")
            submitted = st.form_submit_button("Create the account and sign in",
                                              type="primary", icon=":material/person_add:",
                                              width="stretch")
        if submitted:
            if password != confirm:
                st.error("The two passwords differ.", icon=":material/error:")
                return
            try:
                # Re-read: another browser may have finished the setup meanwhile.
                data = users.load()
                name = users.create_first_admin(data, username, password, display)
            except users.AccessError as exc:
                st.error(str(exc), icon=":material/error:")
                return
            users.save(data)
            audit.record("setup", user=name, by=name)
            _start_session(name)
            _remember(data, name)
            st.rerun()


def change_password_form(data, username, key, forced=False):
    """A form to change the signed-in user's own password. True when changed."""

    with st.form(key, border=False):
        current = st.text_input("Current password", type="password",
                                key=f"{key}_current", autocomplete="current-password")
        new = st.text_input("New password", type="password", key=f"{key}_new",
                            autocomplete="new-password",
                            help=f"At least {data['policy']['min_password_length']} "
                                 "characters, with letters and a digit or symbol.")
        confirm = st.text_input("Repeat the new password", type="password",
                                key=f"{key}_confirm", autocomplete="new-password")
        submitted = st.form_submit_button("Change password", type="primary",
                                          icon=":material/key:",
                                          width="stretch" if forced else "content")
    if not submitted:
        return False
    user = users.get(data, username)
    if user is None or not users.verify_password(current, user["password"]):
        st.error("The current password is wrong.", icon=":material/error:")
        return False
    if new != confirm:
        st.error("The two new passwords differ.", icon=":material/error:")
        return False
    try:
        users.set_password(data, username, new)
    except users.AccessError as exc:
        st.error(str(exc), icon=":material/error:")
        return False
    audit.record("password_changed", user=username, by=username)
    # The change ended every remembered sign-in (other browsers too); this
    # browser gets a new one.
    _remember(data, username)
    return True


def _forced_change(data, username):
    with _card():
        st.html('<div class="ip-card-title" style="font-size:1.25rem">Choose a new '
                'password</div><div class="ip-card-sub">Your password was set by an '
                'administrator. Choose your own before you continue.</div>')
        if change_password_form(data, username, "auth_forced", forced=True):
            st.session_state.auth_notice = "Password changed."
            st.rerun()
        if st.button("Sign out", key="auth_forced_out", icon=":material/logout:"):
            sign_out()


def _resume(data):
    """Sign in a new session (a reloaded tab) from its cookie, if valid."""

    token = _cookie_token()
    if not token or st.session_state.get("auth_cookie_tried") == token:
        return None
    st.session_state.auth_cookie_tried = token
    name, changed = users.resume_session(data, token)
    if changed:
        users.save(data)
    if not name:
        st.session_state.auth_cookie = ""      # stale cookie: clear it
        return None
    _start_session(name)
    st.session_state[TOKEN_KEY] = token
    st.session_state.auth_slid = time.time()
    audit.record("resumed", user=name)
    return name


def _start_session(name):
    for key in ("auth_password", "auth_setup_pw", "auth_setup_pw2"):
        st.session_state.pop(key, None)
    st.session_state[SESSION_KEY] = name
    st.session_state[SEEN_KEY] = time.time()


def sign_out(event="signed_out"):
    name = st.session_state.get(SESSION_KEY)
    token = st.session_state.get(TOKEN_KEY)
    if token:
        data = users.load()
        if users.end_session(data, token):
            users.save(data)
    agent = st.session_state.get("agent")
    if agent is not None:
        try:
            agent.close()
        except Exception:
            pass
    if name:
        audit.record(event, user=name, by=name)
    # Drop everything from the session: chats, drafts, answers.
    for key in list(st.session_state.keys()):
        del st.session_state[key]
    st.session_state.auth_cookie = ""          # clear it in the browser
    if event == "session_expired":
        st.session_state.auth_notice = "You were signed out after a period of inactivity."
    st.rerun()


def require():
    """The signed-in user (public dict with ``username``), or a screen and
    ``st.stop()``."""

    _emit_cookie()
    data = users.load()
    if not users.has_users(data):
        st.session_state.pop(SESSION_KEY, None)
        _setup(data)
        st.stop()

    name = st.session_state.get(SESSION_KEY)
    if not name:
        name = _resume(data)
    elif st.session_state.get(TOKEN_KEY) and (
            time.time() - st.session_state.get("auth_slid", 0) > SLIDE_SECONDS):
        # An active session keeps its remembered sign-in alive.
        st.session_state.auth_slid = time.time()
        if users.resume_session(data, st.session_state[TOKEN_KEY])[1]:
            users.save(data)
    user = users.get(data, name) if name else None
    if name and (user is None or not user["active"]):
        sign_out("signed_out")
    if user is None:
        _sign_in(data)
        st.stop()

    idle = data["policy"]["session_idle_minutes"] * 60
    if time.time() - st.session_state.get(SEEN_KEY, 0) > idle:
        sign_out("session_expired")
    st.session_state[SEEN_KEY] = time.time()

    if user["must_change_password"]:
        _forced_change(data, name)
        st.stop()
    return {**users.public(user), "username": name}


def account_menu(user, key):
    """A popover with the user's name, password change and Sign out."""

    with st.popover(user["display_name"] or user["username"], icon=":material/person:",
                    width="stretch"):
        st.caption(f"Signed in as **{user['username']}**")
        notice = st.session_state.pop("auth_notice", None)
        if notice:
            st.success(notice, icon=":material/check_circle:")
        with st.expander("Change password"):
            if change_password_form(users.load(), user["username"], f"{key}_pw"):
                st.session_state.auth_notice = "Password changed."
                st.rerun()
        if st.button("Sign out", key=f"{key}_out", icon=":material/logout:",
                     width="stretch"):
            sign_out()
