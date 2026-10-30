Version 3.3.0 · Last updated 2026-10-30

**English** · [Deutsch](BLUEPRINT_DE.md)

# Blueprint

The blueprint of the LLM Cross-Compiler Framework is three files, each in English with a German twin:

| File | What it holds |
|---|---|
| [docs/DESIGN.md](DESIGN.md) | What the framework is, every component in detail, and every decision with what it says, how the code does it and why |
| [docs/ROADMAP.md](ROADMAP.md) | What comes: planned work and work in progress, nothing that is finished |
| [CHANGELOG.md](../CHANGELOG.md) | What was: only finished, wired, tested and committed work, one section per version (Keep a Changelog 1.1.0), every entry with its time stamp and version |

A change is built against all three and updates all three in the same commit: its changelog entry under the version it
lands in, its decision in the design, and the roadmap row it finishes removed. There is no "Unreleased" section;
upcoming work is in the roadmap only.

## Where the old sections went

This file used to hold the design and the roadmap in one document. Links to its old sections keep working: each old
anchor is listed here with its new place.

| Old anchor | New place |
|---|---|
| <a id="contents"></a>`#contents` | [DESIGN: Contents](DESIGN.md#contents) |
| <a id="in-short"></a>`#in-short` | [DESIGN: In short](DESIGN.md#in-short) |
| <a id="purpose-and-principles"></a>`#purpose-and-principles` | [DESIGN: Purpose and principles](DESIGN.md#purpose-and-principles) |
| <a id="architecture-overview"></a>`#architecture-overview` | [DESIGN: Architecture overview](DESIGN.md#architecture-overview) |
| <a id="layers-unreleased"></a>`#layers-unreleased` | [DESIGN: Layers](DESIGN.md#layers) |
| <a id="components"></a>`#components` | [DESIGN: Components](DESIGN.md#components) |
| <a id="where-things-are-stored"></a>`#where-things-are-stored` | [DESIGN: Where things are stored](DESIGN.md#where-things-are-stored) |
| <a id="data-flows"></a>`#data-flows` | [DESIGN: Data flows](DESIGN.md#data-flows) |
| <a id="a-build-from-request-to-device"></a>`#a-build-from-request-to-device` | [DESIGN: A build, from request to device](DESIGN.md#a-build-from-request-to-device) |
| <a id="a-tool-call-from-an-mcp-client"></a>`#a-tool-call-from-an-mcp-client` | [DESIGN: A tool call from an MCP client](DESIGN.md#a-tool-call-from-an-mcp-client) |
| <a id="an-ai-app-in-the-open-window-unreleased"></a>`#an-ai-app-in-the-open-window-unreleased` | [DESIGN: An AI app in the open window](DESIGN.md#an-ai-app-in-the-open-window) |
| <a id="a-question-to-ditto-unreleased"></a>`#a-question-to-ditto-unreleased` | [DESIGN: A question to Ditto](DESIGN.md#a-question-to-ditto) |
| <a id="self-healing-approval"></a>`#self-healing-approval` | [DESIGN: Self-healing approval](DESIGN.md#self-healing-approval) |
| <a id="the-local-model-for-offline-mode-unreleased"></a>`#the-local-model-for-offline-mode-unreleased` | [DESIGN: The local model for offline mode](DESIGN.md#the-local-model-for-offline-mode) |
| <a id="offline-mode-and-the-local-network-unreleased"></a>`#offline-mode-and-the-local-network-unreleased` | [DESIGN: Offline mode and the local network](DESIGN.md#offline-mode-and-the-local-network) |
| <a id="deep-ingest-from-a-web-page-to-the-knowledge-base-unreleased"></a>`#deep-ingest-from-a-web-page-to-the-knowledge-base-unreleased` | [DESIGN: Deep Ingest: from a web page to the knowledge base](DESIGN.md#deep-ingest-from-a-web-page-to-the-knowledge-base) |
| <a id="a-knowledge-base-search-unreleased"></a>`#a-knowledge-base-search-unreleased` | [DESIGN: A knowledge-base search](DESIGN.md#a-knowledge-base-search) |
| <a id="the-knowledge-bases-storage-qdrant-edge-unreleased"></a>`#the-knowledge-bases-storage-qdrant-edge-unreleased` | [DESIGN: The knowledge base's storage: Qdrant Edge](DESIGN.md#the-knowledge-bases-storage-qdrant-edge) |
| <a id="community-packs-format-trust-and-exchange-unreleased"></a>`#community-packs-format-trust-and-exchange-unreleased` | [DESIGN: Community packs: format, trust and exchange](DESIGN.md#community-packs-format-trust-and-exchange) |
| <a id="recent-inputs-unreleased"></a>`#recent-inputs-unreleased` | [DESIGN: Recent inputs](DESIGN.md#recent-inputs) |
| <a id="security-model"></a>`#security-model` | [DESIGN: Security model](DESIGN.md#security-model) |
| <a id="known-gaps"></a>`#known-gaps` | [DESIGN: Known gaps](DESIGN.md#known-gaps) |
| <a id="key-decisions"></a>`#key-decisions` | [DESIGN: Key decisions](DESIGN.md#key-decisions) |
| <a id="versioning-and-documentation-policy"></a>`#versioning-and-documentation-policy` | [DESIGN: Versioning and documentation policy](DESIGN.md#versioning-and-documentation-policy) |
| <a id="roadmap"></a>`#roadmap` | [ROADMAP](ROADMAP.md) |
| <a id="310---ditto-rebuild-sign-in-and-local-ai-in-progress"></a>`#310---ditto-rebuild-sign-in-and-local-ai-in-progress` | [ROADMAP](ROADMAP.md) |
| <a id="next---build-service-queue-and-modes-planned-starts-right-after-the-architecture-consolidation"></a>`#next---build-service-queue-and-modes-planned-starts-right-after-the-architecture-consolidation` | [ROADMAP: Next - build service, queue and modes (planned; starts right after the architecture consolidation)](ROADMAP.md#next---build-service-queue-and-modes-planned-starts-right-after-the-architecture-consolidation) |
| <a id="320---device-runtime-and-the-maintainers-hardware-planned"></a>`#320---device-runtime-and-the-maintainers-hardware-planned` | [ROADMAP: Rest of 3.2.0 - Ditto's context, the board bundle, the device runtime and more hardware (in progress)](ROADMAP.md#rest-of-320---dittos-context-the-board-bundle-the-device-runtime-and-more-hardware-in-progress) |
| <a id="330---lora-and-large-models-planned"></a>`#330---lora-and-large-models-planned` | [ROADMAP: 3.3.0 - LoRA and large models (planned)](ROADMAP.md#330---lora-and-large-models-planned) |
| <a id="340---supply-chain-and-community-planned"></a>`#340---supply-chain-and-community-planned` | [ROADMAP: 3.4.0 - Supply chain and community (planned)](ROADMAP.md#340---supply-chain-and-community-planned) |
| <a id="rust-parts-planned"></a>`#rust-parts-planned` | [ROADMAP: Rust parts (planned)](ROADMAP.md#rust-parts-planned) |
| <a id="later-planned"></a>`#later-planned` | [ROADMAP: Later (planned)](ROADMAP.md#later-planned) |
| <a id="history"></a>`#history` | [DESIGN: History](DESIGN.md#history) |
| <a id="glossary"></a>`#glossary` | [DESIGN: Glossary](DESIGN.md#glossary) |
