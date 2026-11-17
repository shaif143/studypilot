"""The Claude chat: a conversation that can act on the student's courses.

Three parts, kept apart on purpose:

* :mod:`app.assistant.keys` — the Claude API key each account brings.
* :mod:`app.assistant.tools` — what Claude may do here: list, file, search, read.
* :mod:`app.assistant.service` — the conversation loop that talks to the API.
"""
