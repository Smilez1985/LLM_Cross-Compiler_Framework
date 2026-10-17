#!/usr/bin/env python3
"""
Everything that talks to a board (consolidation plan, layer 2): one Qt-free package that imports only the bottom layer
(orchestrator.utils and orchestrator.security), never Core, runtime, tools or gui.

- address.py: the board's address (Endpoint with its known_hosts, stored and displayed forms), the one host check
  (valid_host, host_shaped), the port check and the one account check (valid_user, re.ASCII).
- reasons.py: why a board cannot be reached (the N6 codes), check_port, classify and reach_code (the code every caller
  records and shows). orchestrator/runtime/device/reasons.py is this module under its old name.
- errors.py: RemoteFailure, the base of every board step's failure (BoardStepError, DeviceScanError,
  DeviceRuntimeError): kind, SSH reason code and the error behind it.
- text.py: a board's words as one line (one_line) or as kept output lines (keep_tail), and the visible state of a
  running deploy (event_text: for the person in their language, or for an AI in English with the board's label).
- transport.py: whether SSH works here (available(), PARAMIKO_AVAILABLE); paramiko is imported on first use only.

What a contact found is recorded for Mojo by orchestrator/runtime/device/trusted.py record_reach (the store of the
trusted boards lives there; this layer imports no runtime code). The trust store, pairing, the session with waiting
and resume, and the transports follow in consolidation stage C.
"""
