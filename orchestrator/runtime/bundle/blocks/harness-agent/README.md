# Agent harness

Block `harness-agent` (kind harness): the person's own agent harness, never code of llmcf.

## Models

None of its own: a harness uses the bundle's llm component (through the gateway).

## How it works

1. `llm-cli bundle harness take-in` takes the harness in at its pin (a commit of its repository, or the sha256 of an
   archive) and records its tree with one checksum.
2. The bundle carries the tree as `harness.tar` with that checksum in the signed manifest, and the framework's
   `harness_watch.sh`; its root action `run_harness` lets llmcf-install unpack it root-owned, after the person's yes
   for the root actions.
3. `llm-cli bundle harness dry-run` and `run` start the harness's own installer through llmcf-install, which computes
   the tree checksum again first; the installer's questions reach the person live, its run survives a dropped
   connection, and the report says before and after what it did.

The variable `assume_yes` carries the harness's own "assume yes" switch into the bundle (an opt-in); a run still asks
the installer's questions unless the person also chooses it for that run.
