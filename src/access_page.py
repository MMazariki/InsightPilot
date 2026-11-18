"""Users and folder access page (``view == "access"``).

Needs the ``manage_users`` permission. Tabs:

* **Users**: add users, set their permissions (the parameters that open
  the Admin page, the Documents page, actions, ...), deactivate, unlock,
  reset a password, delete.
* **Document folders**: which users may open which folder of PDFs
  (``documents\\<folder>``), as a grid; create a new folder.
* **Sign-in policy**: password length, lockout, idle sign-out.
* **Security log**: sign-ins, failures and every change made here.
"""

import secrets
import string

import streamlit as st

from src.security import audit, users

SHORT_LABELS = {
    "power_bi": "Power BI",
    "documents": "Documents",
    "actions": "Actions",
    "evaluation": "Evaluation",
    "admin_settings": "Admin page",
    "manage_users": "Users & folders",
}


def _docs_root(settings):
    from src.rag.service import resolve_folder

    return resolve_folder(settings["documents"].get("folder"))


def disk_folders(settings):
    """Folder ids under the documents folder with their PDF count."""

    root = _docs_root(settings)
    out = {}
    if root.is_dir():
        for path in root.iterdir():
            if path.is_dir() and users.FOLDER_ID.fullmatch(path.name):
                out[path.name] = sum(1 for _ in path.rglob("*.pdf"))
    return out


def _temporary_password():
    alphabet = string.ascii_letters + string.digits
    while True:
        text = "".join(secrets.choice(alphabet) for _ in range(14))
        if any(c.isdigit() for c in text) and any(c.isalpha() for c in text):
            return text


def _apply(change, done, event, me, **detail):
    """Run ``change(data)`` on fresh data, save, log; errors shown."""

    data = users.load()
    try:
        result = change(data)
    except users.AccessError as exc:
        st.error(str(exc), icon=":material/error:")
        return None
    users.save(data)
    audit.record(event, by=me, **detail)
    st.session_state.access_notice = done
    return result if result is not None else True


# ---------------------------------------------------------------- users

def _users_tab(data, me, folders):
    rows = []
    for name, user in sorted(data["users"].items()):
        rows.append({
            "Username": name,
            "Name": user["display_name"],
            "Active": user["active"],
            "Permissions": ", ".join(SHORT_LABELS[p] for p in user["permissions"]) or "none",
            "Folders": ("all" if user["all_folders"] else ", ".join(user["folders"]) or "none")
            if "documents" in user["permissions"] else "-",
            "Locked": users.locked(user),
            "Must change password": user["must_change_password"],
            "Last sign-in": (user["last_sign_in"] or "never").replace("T", " "),
        })
    st.dataframe(rows, hide_index=True, width="stretch")

    folder_choices = sorted(set(folders) | {f for u in data["users"].values()
                                             for f in u["folders"]},
                            key=users._folder_sort)

    with st.expander("Add a user", icon=":material/person_add:"):
        with st.form("acc_add", clear_on_submit=False, border=False):
            a, b = st.columns(2)
            username = a.text_input("Username", key="acc_new_username")
            display = b.text_input("Display name", key="acc_new_display")
            st.caption("Permissions")
            chosen = [p for p, label in users.PERMISSIONS.items()
                      if st.checkbox(label, key=f"acc_new_perm_{p}",
                                     value=p in ("power_bi", "documents"))]
            all_folders = st.checkbox("Every document folder (now and future)",
                                      key="acc_new_all_folders")
            picked = st.multiselect("Document folders", folder_choices,
                                    key="acc_new_folders",
                                    placeholder="Choose the folders this user may open")
            create = st.form_submit_button("Create user", type="primary",
                                           icon=":material/person_add:")
        if create:
            password = _temporary_password()
            name = _apply(lambda d: users.create_user(
                d, username, password, display, chosen, picked, all_folders,
                must_change=True), "", "user_created", me,
                user=str(username).strip().lower()[:64], permissions=chosen,
                folders="all" if all_folders else picked)
            if name:
                st.session_state.access_notice = (
                    f"User **{name}** created. Temporary password: `{password}` "
                    "(shown once). They choose their own at first sign-in.")
                st.rerun()

    names = sorted(data["users"])
    if not names:
        return
    st.markdown("**Edit a user**")
    selected = st.selectbox("User", names, key="acc_edit_user",
                            format_func=lambda n: f"{n} ({data['users'][n]['display_name']})")
    user = data["users"][selected]
    prefix = f"acc_edit_{selected}"
    with st.form(f"{prefix}_form", border=True):
        a, b = st.columns([2, 1])
        display = a.text_input("Display name", value=user["display_name"],
                               key=f"{prefix}_display")
        active = b.checkbox("Active (can sign in)", value=user["active"],
                            key=f"{prefix}_active", disabled=selected == me)
        st.caption("Permissions")
        chosen = [p for p, label in users.PERMISSIONS.items()
                  if st.checkbox(label, value=p in user["permissions"],
                                 key=f"{prefix}_perm_{p}",
                                 disabled=selected == me and p == "manage_users")]
        all_folders = st.checkbox("Every document folder (now and future)",
                                  value=user["all_folders"], key=f"{prefix}_all")
        picked = st.multiselect("Document folders", folder_choices,
                                default=[f for f in user["folders"] if f in folder_choices],
                                key=f"{prefix}_folders")
        save = st.form_submit_button("Save changes", type="primary",
                                     icon=":material/save:")
    if save:
        if selected == me:
            chosen = sorted(set(chosen) | {"manage_users"})
            active = True
        if _apply(lambda d: users.update_user(
                d, selected, me, display_name=display, permissions=chosen,
                active=active, all_folders=all_folders, folders=picked),
                f"Changes to **{selected}** saved.", "user_updated", me,
                user=selected, permissions=chosen, active=active,
                folders="all" if all_folders else picked):
            st.rerun()

    one, generate, two, three = st.columns(4)
    with one:
        with st.popover("Change password", icon=":material/password:",
                        width="stretch", disabled=selected == me,
                        help="Type a new password for this user. Change your own "
                             "in the account menu (it asks for the current one)."):
            with st.form(f"{prefix}_setpw", border=False):
                new = st.text_input(f"New password for {selected}", type="password",
                                    key=f"{prefix}_setpw_new", autocomplete="new-password",
                                    help=f"At least {data['policy']['min_password_length']} "
                                         "characters, with letters and a digit or symbol.")
                repeat = st.text_input("Repeat it", type="password",
                                       key=f"{prefix}_setpw_repeat",
                                       autocomplete="new-password")
                must_change = st.checkbox("The user must choose a new one at the next "
                                          "sign-in", value=True, key=f"{prefix}_setpw_must")
                set_it = st.form_submit_button("Set password", type="primary",
                                               icon=":material/key:")
            if set_it:
                if new != repeat:
                    st.error("The two passwords differ.", icon=":material/error:")
                elif _apply(lambda d: users.set_password(d, selected, new,
                                                         must_change=must_change),
                            f"Password of **{selected}** changed"
                            + (" (to be changed at the next sign-in)." if must_change
                               else "."),
                            "password_reset", me, user=selected, typed=True,
                            must_change=must_change):
                    st.rerun()
    with generate:
        if st.button("Temporary password", key=f"{prefix}_reset", icon=":material/lock_reset:",
                     width="stretch", disabled=selected == me,
                     help="The app makes a temporary password and shows it once; the "
                          "user chooses a new one at the next sign-in."):
            password = _temporary_password()
            if _apply(lambda d: users.set_password(d, selected, password, must_change=True),
                      "", "password_reset", me, user=selected):
                st.session_state.access_notice = (
                    f"Temporary password for **{selected}**: `{password}` (shown once).")
                st.rerun()
    with two:
        if st.button("Unlock", key=f"{prefix}_unlock", icon=":material/lock_open:",
                     width="stretch", disabled=not users.locked(user)):
            if _apply(lambda d: users.unlock(d, selected), f"**{selected}** unlocked.",
                      "user_updated", me, user=selected, unlocked=True):
                st.rerun()
    with three:
        with st.popover("Delete user", icon=":material/person_remove:",
                        width="stretch", disabled=selected == me,
                        help="You cannot delete your own account."):
            st.write(f"Delete **{selected}**? They can no longer sign in. "
                     "Deactivating keeps the account and can be undone.")
            if st.button("Delete for good", key=f"{prefix}_delete", type="primary"):
                if _apply(lambda d: users.delete_user(d, selected, me),
                          f"**{selected}** deleted.", "user_deleted", me, user=selected):
                    st.session_state.pop("acc_edit_user", None)
                    st.rerun()


# -------------------------------------------------------------- folders

def _folders_tab(data, me, settings, folders):
    root = _docs_root(settings)
    st.caption(f"Each folder is a subfolder of `{root}`. A user opens a folder on "
               "the Documents page only if it is ticked here (or they have 'every "
               "folder') **and** they have the Documents permission.")
    names = sorted(data["users"])
    all_ids = sorted(set(folders) | {f for u in data["users"].values() for f in u["folders"]},
                     key=users._folder_sort)

    if not all_ids:
        st.info("There are no document folders yet. Create one below.",
                icon=":material/folder_off:")
    else:
        everything = [n for n in names if data["users"][n]["all_folders"]]
        no_docs = [n for n in names if "documents" not in data["users"][n]["permissions"]]
        rows = []
        for folder in all_ids:
            row = {"Folder": folder,
                   "PDFs": folders.get(folder, 0),
                   "On disk": folder in folders}
            for name in names:
                row[name] = folder in data["users"][name]["folders"]
            rows.append(row)
        columns = {
            "Folder": st.column_config.TextColumn(disabled=True),
            "PDFs": st.column_config.NumberColumn(disabled=True, width="small"),
            "On disk": st.column_config.CheckboxColumn(disabled=True, width="small"),
        }
        for name in names:
            columns[name] = st.column_config.CheckboxColumn(
                name, help=data["users"][name]["display_name"]
                + (" · has every folder anyway" if name in everything else "")
                + (" · no Documents permission" if name in no_docs else ""))
        edited = st.data_editor(rows, column_config=columns, hide_index=True,
                                width="stretch", key="acc_folder_grid",
                                num_rows="fixed")
        notes = []
        if everything:
            notes.append("every folder: " + ", ".join(everything))
        if no_docs:
            notes.append("no Documents permission (ticks have no effect until it is "
                         "given): " + ", ".join(no_docs))
        if notes:
            st.caption("; ".join(notes).capitalize() + ".")

        changes = []
        for row in edited:
            wanted = [n for n in names if row.get(n)]
            current = [n for n in names if row["Folder"] in data["users"][n]["folders"]]
            if sorted(wanted) != sorted(current):
                changes.append((row["Folder"], wanted, current))
        if st.button(f"Save folder access ({len(changes)} folder(s) changed)"
                     if changes else "No changes to save", key="acc_folder_save",
                     type="primary", disabled=not changes, icon=":material/save:"):
            def change(d):
                for folder, wanted, _ in changes:
                    users.set_folder_access(d, folder, wanted)
            if _apply(change, "Folder access saved.", "folders_changed", me,
                      changes={f: {"added": sorted(set(w) - set(c)),
                                   "removed": sorted(set(c) - set(w))}
                               for f, w, c in changes}):
                st.session_state.pop("acc_folder_grid", None)
                st.rerun()

    st.markdown("**Create a folder**")
    a, b = st.columns([2, 1], vertical_alignment="bottom")
    new_id = a.text_input("Folder number", key="acc_new_folder", placeholder="e.g. 103",
                          help="Letters, digits, '-' and '_'. Then copy the PDFs into it "
                               "and index them on the Documents page.")
    if b.button("Create folder", key="acc_create_folder", icon=":material/create_new_folder:",
                width="stretch", disabled=not new_id.strip()):
        folder = new_id.strip()
        if not users.FOLDER_ID.fullmatch(folder):
            st.error("A folder number may contain only letters, digits, '-' and '_'.",
                     icon=":material/error:")
        elif folder in folders:
            st.error(f"Folder {folder} already exists.", icon=":material/error:")
        else:
            (root / folder).mkdir(parents=True, exist_ok=True)
            audit.record("folders_changed", by=me, created=folder)
            st.session_state.access_notice = (f"Folder **{folder}** created at "
                                              f"`{root / folder}`. Tick who may open it.")
            st.rerun()


# --------------------------------------------------------------- policy

def _policy_tab(data, me):
    policy = data["policy"]
    with st.form("acc_policy", border=False):
        a, b = st.columns(2)
        values = {}
        labels = {
            "min_password_length": "Minimum password length",
            "max_failed_attempts": "Failed sign-ins before the account locks",
            "lockout_minutes": "Lock for (minutes)",
            "session_idle_minutes": "Sign out after inactivity (minutes)",
        }
        for number, (key, label) in enumerate(labels.items()):
            low, high = users.POLICY_LIMITS[key]
            values[key] = (a if number % 2 == 0 else b).number_input(
                label, min_value=low, max_value=high, value=policy[key], step=1,
                key=f"acc_policy_{key}")
        save = st.form_submit_button("Save policy", type="primary", icon=":material/save:")
    if save:
        def change(d):
            d["policy"] = users.clean_policy(values)
        if _apply(change, "Sign-in policy saved.", "user_updated", me, policy=values):
            st.rerun()
    st.caption("A new minimum length applies to passwords set from now on. "
               "Reloading the browser tab always asks to sign in again.")


BACKUP_FILES = ["config/settings.json", "config/users.json", "config/alerts.json",
                "config/secrets.bin"]


def backup_zip(root=None, now=None):
    """``(file name, zip bytes, names)`` of the settings, users, alerts,
    encrypted keys and every log (``logs/*.jsonl``, monthly files too)."""

    import datetime as dt
    import io
    import zipfile
    from pathlib import Path

    root = Path(root) if root else users.ROOT
    now = now or dt.datetime.now()
    files = [root / name for name in BACKUP_FILES] + sorted((root / "logs").glob("*.jsonl"))
    buffer = io.BytesIO()
    names = []
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            if path.is_file():
                name = path.relative_to(root).as_posix()
                archive.write(path, name)
                names.append(name)
        archive.writestr("README.txt", (
            f"InsightPilot backup, {now:%Y-%m-%d %H:%M}.\n\n"
            "Restore: stop InsightPilot, copy config\\ and logs\\ back into the "
            "project folder, start it again.\n"
            "config\\users.json holds password hashes: keep this file private.\n"
            "config\\secrets.bin (API keys, email password) is encrypted for this "
            "Windows account on this computer; elsewhere, enter the keys again.\n"))
    return f"insightpilot-backup-{now:%Y%m%d-%H%M}.zip", buffer.getvalue(), names


def _backup_tab(me):
    st.write("Download the settings, users, alerts, encrypted keys and all logs as one "
             "zip file, to keep somewhere safe. The documents (PDFs) and the search "
             "index are not included: back up the `documents` folder separately.")
    st.warning("The zip holds the users' password hashes: store it as privately as "
               "the computer itself.", icon=":material/lock:")
    if st.button("Prepare backup", key="acc_backup_make", icon=":material/inventory_2:"):
        name, data, names = backup_zip()
        st.session_state.acc_backup = (name, data, names)
    prepared = st.session_state.get("acc_backup")
    if prepared:
        name, data, names = prepared
        st.caption(f"{len(names)} files: " + ", ".join(names))
        st.download_button(f"Download {name} ({len(data) / 1024:,.0f} KB)", data,
                           file_name=name, mime="application/zip", key="acc_backup_get",
                           icon=":material/download:", type="primary",
                           on_click=lambda: audit.record("backup_downloaded", by=me,
                                                         files=len(names)))


def _log_tab():
    rows = list(reversed(audit.entries(limit=300)))
    if not rows:
        st.info("Nothing logged yet.", icon=":material/history:")
        return
    st.dataframe([{"Time": r.get("time", "").replace("T", " "),
                   "Event": r.get("event", "").replace("_", " "),
                   "User": r.get("user", ""), "By": r.get("by", ""),
                   "Detail": ", ".join(f"{k}: {v}" for k, v in r.items()
                                       if k not in ("time", "event", "user", "by"))}
                  for r in rows], hide_index=True, width="stretch", height=420)
    st.caption("Newest first, last 300 entries. Full log: `logs\\security.jsonl`.")


# ----------------------------------------------------------------- page

def render(settings, me):
    title, back = st.columns([5, 1.3], vertical_alignment="bottom")
    with title:
        st.html('<div class="ip-card-title" style="font-size:1.25rem">Users and '
                'folder access</div><div class="ip-card-sub">Who can sign in, what '
                'each person may open, and which document folders they see.</div>')
    with back:
        if st.button("Back", key="acc_back", icon=":material/arrow_back:",
                     width="stretch"):
            st.session_state.view = "chat"
            st.query_params.clear()
            st.rerun()

    notice = st.session_state.pop("access_notice", None)
    if notice:
        st.success(notice, icon=":material/check_circle:")

    data = users.load()
    folders = disk_folders(settings)
    tabs = st.tabs(["Users", "Document folders", "Sign-in policy", "Security log", "Backup"])
    with tabs[0]:
        _users_tab(data, me, folders)
    with tabs[1]:
        _folders_tab(data, me, settings, folders)
    with tabs[2]:
        _policy_tab(data, me)
    with tabs[3]:
        _log_tab()
    with tabs[4]:
        _backup_tab(me)
