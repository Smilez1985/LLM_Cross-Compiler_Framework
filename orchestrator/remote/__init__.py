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
- transport.py: whether SSH works here (available(), PARAMIKO_AVAILABLE; paramiko is imported on first use only),
  sh_command (every command is one `sh -c '...'`), and the Transport interface with its two transports.
  ParamikoTransport: run with a time limit and an output cap (read_limited), the board's files over SFTP, alive, the
  login (open_client) and paramiko_quiet. SystemSshTransport: the same over this computer's OpenSSH client where
  paramiko is missing (SshFailed for OpenSSH's own failures), with the one OpenSSH command-line builder
  (openssh_argv, openssh_options with the login time limit and keepalive, config_value quoting a path).
- session.py: RemoteSession, one session with a board: the login with the host key checked at every login, typed
  credentials (KeyOnly, OneTimePassword: only the latter can pair), the private folder, the upload that resumes after
  a network drop, the wait for the board (N5), a job that runs on the board on its own and is followed again after a
  drop, one CancelToken, and RemoteEvent (DeploymentManager.DeployEvent). The deploy is a thin adapter over it.
- trust.py: the framework's known_hosts, read-only (TrustStore: status, status_of, check -> KeyCheck,
  read_presented_key; fingerprint, device_id_for, parse_public_key for a pasted key; the HostKeyError classes).

- pairing.py, the person's only: trust a presented key with its audit row in one step (trust_presented_key), the one
  decision of trust on first use (first_use), pair over a one-time password session (pair), Q5's change of a board's
  address and host key (plan_replacement, replace_key) and a consent's audit row (record_consent). No module an AI can
  reach imports it (tests/test_remote_trust.py follows the imports).

What a contact found is recorded for Mojo by orchestrator/runtime/device/trusted.py record_reach (the store of the
trusted boards lives there; this layer imports no runtime code). The deploy's OpenSSH fallback is a RemoteSession
over SystemSshTransport (chain c2-remote-system, decision A5); the runtime deploy, the probe and the on-board benchmark
move onto RemoteSession in chain c2-remote-callers.
"""
