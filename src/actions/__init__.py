"""Actions on an answer, each executed only after the user approves the exact
draft (docs/AGENT_ACTIONS_PLAN.md).

* ``detect``        is this chat message an action request? (rules only)
* ``address_book``  recipient phrases -> addresses from the admin's list
* ``render``        message, card, task and file content built from the answer
* ``validate``      recipients, sizes, caps, settings
* ``executors``     export, Teams webhook, Graph mail, Graph To Do
* ``audit``         append-only log of every draft, approval and result
* ``service``       draft lifecycle: create, edit, approve once, expire
"""
