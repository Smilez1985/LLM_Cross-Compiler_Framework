#!/usr/bin/env python3
"""
Everything that talks to a board (consolidation plan, layer 2): one Qt-free package that imports only the bottom layer
(orchestrator.utils and orchestrator.security), never Core, runtime, tools or gui.

- address.py: the board's address (Endpoint with its known_hosts, stored and displayed forms), the one host check
  (valid_host, host_shaped), the port check and the one account check (valid_user, re.ASCII).
"""
