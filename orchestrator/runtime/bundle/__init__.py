"""The board bundle, deploy way 3 (ROADMAP 3.3.0 "The bundle, deploy way 3", "An idempotent installer on the board"):
building blocks with typed variables (blocks.py), the declarative, signed bundle manifest (manifest.py), the generator
on this computer (generator.py), the on-board installer of the unprivileged steps (installer.py) and its gate
operations and deploy over the board session (board.py).

Nothing here imports Qt; the CLI (`llmcf bundle ...`), the Boards > Board bundle... window and the MCP tools
bundle_plan / bundle_deploy call the same functions."""
