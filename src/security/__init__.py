"""Sign-in, users, permissions and document-folder access.

* ``users``  - the user store (``config/users.json``), password hashing,
  permissions, folder access, lockout. No Streamlit.
* ``audit``  - append-only security log (``logs/security.jsonl``).
* ``login``  - the sign-in, first-admin and change-password screens and the
  account menu (Streamlit).
"""
