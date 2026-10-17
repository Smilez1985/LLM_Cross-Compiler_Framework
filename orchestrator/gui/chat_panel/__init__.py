#!/usr/bin/env python3
"""
The chat panel on the right of the main window (delegation design 5.1-5.9): one place for Ditto's answers, the
person's messages, notices and the records of what AI apps did, read from the signed chat feed, plus the questions
that wait for the person, read from the pending approvals.

Every text here is plain text (gui/plain.py): nothing an AI, an app or a file name wrote is ever drawn as rich text,
and nothing is clickable inline. Interactive question and decision bubbles, undo, revoke and "Why?" are later
packages; their extension points are `bubbles.register_bubble()` and `Bubble.add_action()`.
"""
