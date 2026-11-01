# Agent harness - template

Block `harness-agent` (kind harness), a documented template.

## Models

None of its own: a harness uses the bundle's llm component (through the gateway once it exists).

## What is missing

The harness installer runs after the person's yes, under its own account, and llmcf watches what it does (network
targets, packages, files, processes) and reports before and after (ROADMAP row "An agent harness as a bundle
component").
