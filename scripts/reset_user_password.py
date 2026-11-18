"""Recovery: set a temporary password for an InsightPilot user from this PC.

For a forgotten administrator password. Needs access to the project folder
(the same as editing config\\users.json), so it gives no more access than
that. The user must choose a new password at the next sign-in, and the
account is unlocked and re-activated.

    .venv\\Scripts\\python.exe scripts\\reset_user_password.py <username>
    .venv\\Scripts\\python.exe scripts\\reset_user_password.py --list
"""

import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.security import audit, users  # noqa: E402


def main(argv):
    data = users.load()
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 2
    if argv[0] == "--list":
        for name, user in sorted(data["users"].items()):
            print(f"{name:24} {'active' if user['active'] else 'inactive':9} "
                  + ", ".join(user["permissions"]))
        return 0
    name = argv[0].strip().lower()
    if name not in data["users"]:
        print(f"No user '{name}'. Use --list to see them.")
        return 1
    password = getpass.getpass("Temporary password: ")
    if password != getpass.getpass("Repeat: "):
        print("The passwords differ; nothing changed.")
        return 1
    try:
        users.set_password(data, name, password, must_change=True)
    except users.AccessError as exc:
        print(exc)
        return 1
    data["users"][name]["active"] = True
    users.save(data)
    audit.record("password_reset", user=name, by="reset_user_password.py")
    print(f"Done. '{name}' signs in with the temporary password and then chooses a new one.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
