# Gateway (llmcf-gateway) - template

Block `gateway-llmcf` (kind gateway), a documented template.

## Models

None of its own: the gateway talks to the bundle's llm component on loopback. Memory: about 64 MB.

## What is missing

The Rust gateway (ROADMAP rows "llmcf-gateway, stage 1 in Rust" and "The gateway as a bundle component"): loopback
first, the local network only with TLS and a pinned fingerprint, never plain HTTP.
