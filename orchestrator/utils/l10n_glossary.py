#!/usr/bin/env python3
"""
The wording toolkit's texts and the glossary of technical terms (wave 6, f-words; DECISIONS round 10, V6, Q1, Q7).
LocalizationManager merges TEXTS and FORMAL (localization.TEXT_MODULES); a key that exists already is refused there.
This module imports nothing from the framework and nothing from Qt: it is plain data.

TERMS: every technical term the GUI explains on mouse-over (gui/explain.py). A term has
  * its id (the key of TERMS, lower case with underscores: "kv_cache"),
  * `shown`: the term as the GUI shows it, English in both languages (technical terms stay English, Q7),
  * `link`: the page that explains it best, always https: Wikipedia, or the term's own documentation (Qdrant ->
    qdrant.tech, GGUF -> the ggml docs, RKLLM -> Rockchip's repository, MCP -> modelcontextprotocol.io), or this
    framework's own manual for the framework's own words,
  * `link_de`: a German page of the same quality when one exists (German Wikipedia, the manual's German twin),
  * `aliases`: other spellings that name the same thing in texts ("imatrix" for the importance matrix),
  * `auto`: False for a word that means other things too ("Token", "Port", "Format"): such a term is explained only
    where a window asks for it by id, never found in a text automatically (glossary.find_terms).
Its explanation is the text "term.<id>" in TEXTS, one or two plain sentences in English and German (tests check both
exist with the same placeholders, and that every link is https).

CANONICAL: the framework's words for things that have several names, with the synonyms a window must not use, as
data for the final wording lint (utils/wording.py synonym_problems).

German texts say "du" (the default form of address, Q1). FORMAL holds the "Sie" form of every German text of this
module that addresses the person; ui_text shows it when the person chose "Sie" (setting form_of_address).
"""

from typing import NamedTuple, Tuple

REPO_DOCS = "https://github.com/Smilez1985/llm_conversion_framework/blob/main/docs/"
LLAMA_CPP = "https://github.com/ggml-org/llama.cpp/blob/master/"


class Term(NamedTuple):
    shown: str
    link: str
    link_de: str = ""
    aliases: Tuple[str, ...] = ()
    auto: bool = True


def _manual(anchor_en: str, anchor_de: str, page: str = "Features") -> Tuple[str, str]:
    """The framework's own manual on GitHub, English and German (the anchor is the heading's GitHub anchor)."""
    return f"{REPO_DOCS}{page}.md#{anchor_en}", f"{REPO_DOCS}{page}_DE.md#{anchor_de}"


def _framework(shown: str, anchors: Tuple[str, str], aliases: Tuple[str, ...] = (), auto: bool = True) -> Term:
    return Term(shown, anchors[0], anchors[1], aliases, auto)


TERMS = {
    # --- models, formats and quantization ---------------------------------------------------------------------------
    "llm": Term("LLM", "https://en.wikipedia.org/wiki/Large_language_model",
                "https://de.wikipedia.org/wiki/Gro%C3%9Fes_Sprachmodell", ("large language model",)),
    "model_format": _framework("Format", _manual("build-pipeline", "build-pipeline"), auto=False),
    "gguf": Term("GGUF", "https://github.com/ggml-org/ggml/blob/master/docs/gguf.md"),
    "safetensors": Term("safetensors", "https://huggingface.co/docs/safetensors/index"),
    "onnx": Term("ONNX", "https://onnx.ai/onnx/intro/"),
    "rkllm": Term("RKLLM", "https://github.com/airockchip/rknn-llm"),
    "rknn": Term("RKNN", "https://github.com/airockchip/rknn-toolkit2"),
    "quantization": Term("Quantization", "https://huggingface.co/docs/optimum/en/concept_guides/quantization",
                         aliases=("quantize", "quantized")),
    "q4_k_m": Term("Q4_K_M", f"{LLAMA_CPP}tools/quantize/README.md"),
    "q4_0": Term("Q4_0", f"{LLAMA_CPP}tools/quantize/README.md"),
    "q8_0": Term("Q8_0", f"{LLAMA_CPP}tools/quantize/README.md"),
    "f16": Term("F16", "https://en.wikipedia.org/wiki/Half-precision_floating-point_format", aliases=("FP16",)),
    "iq_types": Term("IQ types", f"{LLAMA_CPP}tools/quantize/README.md",
                     aliases=("IQ1_S", "IQ1_M", "IQ2_XXS", "IQ2_XS", "IQ2_S", "IQ2_M", "IQ3_XXS", "IQ3_XS", "IQ3_S",
                              "IQ3_M", "IQ4_XS", "IQ4_NL")),
    "tq_types": Term("TQ types", f"{LLAMA_CPP}tools/quantize/README.md", aliases=("TQ1_0", "TQ2_0")),
    "imatrix": Term("Importance matrix", f"{LLAMA_CPP}tools/imatrix/README.md", aliases=("imatrix",)),
    "calibration_dataset": Term("Calibration dataset",
                                "https://huggingface.co/docs/optimum/en/concept_guides/quantization",
                                aliases=("calibration data",)),
    "kv_cache": Term("KV cache", "https://huggingface.co/docs/transformers/en/kv_cache"),
    "token": Term("Token", "https://huggingface.co/docs/transformers/en/tokenizer_summary", auto=False),
    "context_length": Term("Context length", "https://www.ibm.com/think/topics/context-window",
                           aliases=("context window", "context size")),
    "perplexity": Term("Perplexity", "https://en.wikipedia.org/wiki/Perplexity",
                       "https://de.wikipedia.org/wiki/Perplexit%C3%A4t"),
    "hugging_face": Term("Hugging Face", "https://huggingface.co/docs/hub/index"),
    "gated_model": Term("Gated model", "https://huggingface.co/docs/hub/models-gated"),
    "finished_model": _framework("Finished model", _manual("where-finished-models-go", "wohin-fertige-modelle-kommen"),
                                 ("golden artifact",)),

    # --- programs, building and hardware ------------------------------------------------------------------------------
    "llama_cpp": Term("llama.cpp", "https://github.com/ggml-org/llama.cpp"),
    "llama_server": Term("llama-server", f"{LLAMA_CPP}tools/server/README.md"),
    "llama_bench": Term("llama-bench", f"{LLAMA_CPP}tools/llama-bench/README.md"),
    "backend": Term("Backend", f"{LLAMA_CPP}docs/build.md", auto=False),
    "npu": Term("NPU", "https://en.wikipedia.org/wiki/Neural_processing_unit",
                "https://de.wikipedia.org/wiki/KI-Beschleuniger"),
    "cross_compiling": Term("Cross-compiling", "https://en.wikipedia.org/wiki/Cross_compiler",
                            "https://de.wikipedia.org/wiki/Cross-Compiler", ("cross-compile", "cross compiler")),
    "toolchain": Term("Toolchain", "https://en.wikipedia.org/wiki/Toolchain",
                      "https://de.wikipedia.org/wiki/Toolchain"),
    "docker": Term("Docker", "https://docs.docker.com/get-started/docker-overview/"),
    "docker_image": Term("Docker image",
                         "https://docs.docker.com/get-started/docker-concepts/the-basics/what-is-an-image/",
                         aliases=("container image",)),
    "container": Term("Container",
                      "https://docs.docker.com/get-started/docker-concepts/the-basics/what-is-a-container/"),
    "digest": Term("Digest", "https://docs.docker.com/dhi/explore/security-concepts/digests/"),
    "sha256": Term("SHA-256", "https://en.wikipedia.org/wiki/SHA-2", "https://de.wikipedia.org/wiki/SHA-2",
                   ("sha256",)),
    "build_sandbox": _framework("Build sandbox", _manual("build-sandbox", "build-sandbox")),
    "self_healing": _framework("Self-healing", _manual("self-healing", "self-healing")),
    "target": _framework("Target", _manual("targets-and-capabilities", "targets-und-capabilities"), auto=False),
    "board": Term("Board", "https://en.wikipedia.org/wiki/Single-board_computer",
                  "https://de.wikipedia.org/wiki/Einplatinencomputer", auto=False),
    "hardware_probe": _framework("Hardware probe", _manual("hardware-probe-and-new-targets",
                                                           "hardware-probe-und-neue-targets")),
    "deployment": _framework("Deployment", _manual("deployment", "%C3%BCbertragen-auf-das-board-deployment")),
    "trusted_board": _framework("Trusted board", _manual("deployment", "%C3%BCbertragen-auf-das-board-deployment")),
    "systemd": Term("systemd", "https://en.wikipedia.org/wiki/Systemd", "https://de.wikipedia.org/wiki/Systemd"),
    "port": Term("Port", "https://en.wikipedia.org/wiki/Port_(computer_networking)",
                 "https://de.wikipedia.org/wiki/Port_(Protokoll)", auto=False),

    # --- connections and security -------------------------------------------------------------------------------------
    "ssh": Term("SSH", "https://en.wikipedia.org/wiki/Secure_Shell", "https://de.wikipedia.org/wiki/Secure_Shell"),
    "ssh_host_key": Term("SSH host key", "https://www.ssh.com/academy/ssh/host-key", aliases=("host key",)),
    "fingerprint": Term("Fingerprint", "https://en.wikipedia.org/wiki/Public_key_fingerprint"),
    "ssh_key": Term("SSH key", "https://www.ssh.com/academy/ssh/public-key-authentication"),
    "api_key": Term("API key", "https://en.wikipedia.org/wiki/API_key", aliases=("access key",)),
    # The framework's own code (a named MCP token), explained in its manual. Not "access token": Hugging Face calls its
    # sign-in that (the token field "Access Token:"), and Wikipedia's "Access token" is a Windows logon token.
    "app_access_code": _framework("App access code", _manual("mcp-server", "mcp-server"), ("MCP token",)),
    "keyring": Term("Keyring", "https://keyring.readthedocs.io/en/latest/"),
    "vault": _framework("Vault", _manual("secrets-and-personal-state", "geheimnisse-und-pers%C3%B6nlicher-zustand"),
                        ("key safe", "credential vault")),
    "passphrase": Term("Passphrase", "https://en.wikipedia.org/wiki/Passphrase",
                       "https://de.wikipedia.org/wiki/Passphrase"),
    "signature": Term("Signature", "https://en.wikipedia.org/wiki/Digital_signature",
                      "https://de.wikipedia.org/wiki/Digitale_Signatur", auto=False),

    # --- Ditto and AI apps --------------------------------------------------------------------------------------------
    "ditto": _framework("Ditto", _manual("ditto-the-assistant", "ditto-der-assistent")),
    "ai_service": _framework("AI service", _manual("providers", "anbieter"), ("provider",)),
    "local_runtime": _framework("Local runtime", _manual("local-runtime-and-model-tiers",
                                                         "lokale-laufzeit-und-modellstufen")),
    "model_size": _framework("Model size", _manual("local-runtime-and-model-tiers", "lokale-laufzeit-und-modellstufen"),
                             ("model tier",)),
    "offline_mode": _framework("Offline mode", _manual("providers", "anbieter")),
    "mcp": Term("MCP", "https://modelcontextprotocol.io/docs/getting-started/intro",
                aliases=("Model Context Protocol",)),
    "tool_permission": _framework("Tool permission", _manual("tool-permissions-and-audit-log",
                                                             "tool-berechtigungen-und-audit-log")),
    "standing_approval": _framework("Standing approval", _manual("tool-permissions-and-audit-log",
                                                                 "tool-berechtigungen-und-audit-log"),
                                    ("don't-ask-again permission",)),
    "audit_log": _framework("Audit log", _manual("tool-permissions-and-audit-log", "tool-berechtigungen-und-audit-log"),
                            ("activity record",)),
    "mojo": _framework("Mojo", (f"{REPO_DOCS}Mojo.md", f"{REPO_DOCS}Mojo_DE.md"), ("Mojo Health Check",)),

    # --- knowledge ----------------------------------------------------------------------------------------------
    "rag": Term("RAG", "https://en.wikipedia.org/wiki/Retrieval-augmented_generation",
                "https://de.wikipedia.org/wiki/Retrieval-Augmented_Generation", ("retrieval-augmented generation",)),
    "knowledge_base": _framework("Knowledge base", _manual("knowledge-base-rag", "wissensbasis-rag")),
    "qdrant": Term("Qdrant", "https://qdrant.tech/documentation/overview/"),
    "embedding": Term("Embedding", "https://qdrant.tech/articles/what-are-embeddings/", aliases=("embeddings",)),
    "reranker": Term("Reranker", "https://sbert.net/examples/cross_encoder/applications/README.html",
                     aliases=("reranking",)),
    "deep_ingest": _framework("Deep Ingest", _manual("deep-ingest", "deep-ingest")),
    "knowledge_snapshot": _framework("Knowledge snapshot", _manual("knowledge-base-rag", "wissensbasis-rag")),
    "robots_txt": Term("robots.txt", "https://en.wikipedia.org/wiki/Robots.txt",
                       "https://de.wikipedia.org/wiki/Robots_Exclusion_Standard"),
    "tdmrep": Term("TDMRep", "https://www.w3.org/community/reports/tdmrep/CG-FINAL-tdmrep-20240510/",
                   aliases=("TDM Reservation Protocol",)),
    "pack": _framework("Pack", _manual("packs", "packs"), ("knowledge pack", "community pack"), auto=False),
}


# --- canonical words and the synonyms a window must not use (final wording lint) ----------------------------------

class Canonical(NamedTuple):
    en: str
    de: str
    avoid_en: Tuple[str, ...] = ()
    avoid_de: Tuple[str, ...] = ()


CANONICAL = (
    Canonical("target", "Target", ("hardware profile", "hardware target"), ("Hardware-Profil", "Hardware-Ziel")),
    Canonical("trusted board", "vertrautes Board", ("paired board", "coupled board"), ("gekoppelt", "koppeln")),
    Canonical("finished model", "fertiges Modell", ("golden artifact",), ("Golden Artifact",)),
    Canonical("AI apps", "KI-Apps", ("AI clients",), ("KI-Clients",)),
    Canonical("standing approval", "Dauerfreigabe", (), ("Dauerauftrag",)),
    Canonical("audit log", "Audit-Log", (), ("Prüfprotokoll",)),
    Canonical("API key", "API Key", ("API token",), ("API-Token", "API-Schlüssel")),
)


TEXTS = {
    "en": {
        # the hover card (gui/explain.py)
        "words.card.name": "Explanation: {term}",
        "words.card.more": "Learn more: {source}",
        "words.card.more_tip": "Opens {url} in your web browser.",
        "words.card.described": "{explanation} Learn more: {url}",
        "words.help.name": "Explain {term}",
        "words.help.tip": "What is {term}? (F1)",
        # the error box (gui/error_dialog.py)
        "words.error.title": "Something went wrong",
        "words.error.next": "Next step: {step}",
        "words.error.details": "Technical details",
        "words.error.details_hint": "In English, without secrets and without your home folder: copy it into a bug "
                                    "report or ask Ditto to explain it.",
        "words.error.copy": "Copy details",
        "words.error.copied": "Copied to the clipboard.",
        # sizes (utils/sizes.py)
        "words.size.bytes": "{n} bytes",
        "words.size.exact": "{size} ({n} bytes)",
        # language and form of address (gui/form_of_address.py, File > Settings...)
        "words.settings.tab.general": "General",
        "words.address.title": "Language and form of address",
        "words.address.intro": "Choose the language of the program. In German it can address you informally (du) "
                               "or formally (Sie). Technical terms stay English in both languages.",
        "words.address.language": "&Language:",
        "words.address.form": "German &form of address:",
        "words.address.du": "du (informal, the default)",
        "words.address.sie": "Sie (formal)",
        "words.address.only_de": "Applies to the German texts.",
        "words.address.saved": "Saved. Windows you open from now on use it; a window that is open keeps its texts "
                               "until you open it again.",
        "words.address.not_saved": "The choice applies until the program ends, but it could not be saved in "
                                   "config.yml. See the log.",
        "words.lang.en": "English",
        "words.lang.de": "Deutsch",

        # --- explanations of the glossary terms ("term.<id>") -----------------------------------------------------
        "term.llm": "Large language model: a neural network trained on very much text that continues and answers "
                    "text. Ditto talks to one.",
        "term.model_format": "The file format a build produces: GGUF runs with llama.cpp on almost any CPU or GPU, "
                             "RKLLM and RKNN run on the NPU of Rockchip chips.",
        "term.gguf": "The file format llama.cpp uses for language models: one file holds the weights and everything "
                     "needed to run them.",
        "term.safetensors": "A safe file format for model weights, used on Hugging Face: it holds numbers only, no "
                            "program code.",
        "term.onnx": "An open exchange format for neural networks; Rockchip's RKNN tools convert models from it.",
        "term.rkllm": "Rockchip's format and runtime for language models on the NPU of its chips (for example "
                      "RK3588 and RK3576).",
        "term.rknn": "Rockchip's format and toolkit for other neural networks (for example vision models) on the "
                     "NPU of its chips.",
        "term.quantization": "Storing a model's weights with fewer bits (for example 4 instead of 16): the file "
                             "gets much smaller and faster, the answers a little less precise.",
        "term.q4_k_m": "About 4.5 bits per weight with llama.cpp's k-quant method: a common good balance of size "
                       "and quality.",
        "term.q4_0": "A simple 4-bit type: small and fast, a little less precise than Q4_K_M; some ARM CPUs and "
                     "NPUs run it especially well.",
        "term.q8_0": "8 bits per weight: nearly the quality of the original model at about half its size.",
        "term.f16": "16-bit floating point: the model unquantized, with its full quality and size.",
        "term.iq_types": "llama.cpp's smallest types, 1 to 4 bits per weight (for example IQ2_XS); they need an "
                         "importance matrix to keep their quality.",
        "term.tq_types": "Ternary types (TQ1_0, TQ2_0) for models trained with weights of only -1, 0 and 1 (for "
                         "example BitNet); other models lose too much quality with them.",
        "term.imatrix": "A table, computed from a calibration dataset, of which weights matter most; quantization "
                        "with few bits keeps those more precise.",
        "term.calibration_dataset": "Sample text the model reads once during the conversion; the importance matrix "
                                    "(GGUF) or the NPU quantization (RKLLM, RKNN) is computed from it. Text close "
                                    "to your later use works best.",
        "term.kv_cache": "Memory in which the model keeps what it has already read of a conversation, so it need "
                         "not compute it again; it grows with the context length. A quantized KV cache (for "
                         "example q8_0) needs less memory.",
        "term.token": "A piece of text (a word or part of a word) as a language model reads and writes it; lengths "
                      "and limits of models are counted in tokens.",
        "term.context_length": "How many tokens of conversation and documents a model can consider at once; more "
                               "needs more memory.",
        "term.perplexity": "A number for how well a model predicts a test text; lower is better. Comparing it "
                           "before and after the quantization shows the quality lost.",
        "term.hugging_face": "A website where models and datasets are shared; the framework downloads models from "
                             "it.",
        "term.gated_model": "A model on Hugging Face that you may download only after accepting its terms there, "
                            "signed in with your account.",
        "term.finished_model": "The result of a successful build: the converted model with its checksums and a "
                               "record of how it was built.",
        "term.llama_cpp": "An open-source program that runs language models in the GGUF format on ordinary "
                          "computers and boards; the framework builds and uses it.",
        "term.llama_server": "llama.cpp's server program: Ditto's local model program, which answers through a "
                             "port on this computer.",
        "term.llama_bench": "llama.cpp's benchmark tool: it measures how many tokens per second a model reads and "
                            "writes on this hardware.",
        "term.backend": "The part of llama.cpp that does the math on one kind of hardware: CPU, CUDA (NVIDIA), "
                        "ROCm (AMD), Vulkan, Metal (Apple) and others.",
        "term.npu": "Neural processing unit: a part of a chip built for the math of neural networks; it runs "
                    "suitable models faster and with less power than the CPU.",
        "term.cross_compiling": "Building a program on one computer for another kind of computer (for example on a "
                                "PC for an ARM board).",
        "term.toolchain": "The set of compilers and tools that builds programs for one kind of processor and "
                          "system.",
        "term.docker": "A program that runs software in isolated containers; the framework builds models inside "
                       "them, so your computer stays clean.",
        "term.docker_image": "A packed, read-only template of a system with its tools; containers are started "
                             "from it.",
        "term.container": "A running, isolated copy of a Docker image: it sees only what it is given and is "
                          "removed after the build.",
        "term.digest": "A checksum (sha256:...) that names exactly one version of an image or file; pinned by its "
                       "digest, the content cannot be swapped unnoticed.",
        "term.sha256": "A method that computes a short check value of a file; when one byte changes, the value "
                       "changes completely. Downloads are checked with it.",
        "term.build_sandbox": "The limits of the build containers: no network, no root rights, a read-only system "
                              "and a process limit, so model code can neither harm your computer nor send data "
                              "away.",
        "term.self_healing": "When a build fails, Ditto reads the log and proposes a fix; nothing runs without "
                             "your yes.",
        "term.target": "The description of a hardware family (chip, boards, formats, quantization types) with its "
                       "build scripts; you choose one in the Builder.",
        "term.board": "A small computer on one circuit board (for example Raspberry Pi, Orange Pi or Rock 5) that "
                      "runs the finished model.",
        "term.hardware_probe": "A small script that reads what a computer or board has (CPU, memory, NPU, system) "
                               "and saves it as a file; the framework recommends a target from it.",
        "term.deployment": "Copying the finished model and its runtime to the board and starting it there.",
        "term.trusted_board": "A board whose SSH host key you confirmed; only trusted boards receive a deployment, "
                              "also when an AI asks.",
        "term.systemd": "The service manager of most Linux systems; on a board it starts the model server at boot "
                        "and again after a crash.",
        "term.port": "A number that picks one program on a computer for a network connection (for example 8080 "
                     "for a model server).",
        "term.ssh": "Secure Shell: the encrypted connection through which the framework reaches a board over the "
                    "network.",
        "term.ssh_host_key": "The board's own key: on every connection it proves that you reach the same board and "
                             "not an impostor. You confirm it once; a changed key stops the connection.",
        "term.fingerprint": "A short checksum of a key (for example SHA256:...), easier to compare than the key "
                            "itself: compare it with the one the board shows.",
        "term.ssh_key": "A key pair for signing in to a board without a password: the private part stays on this "
                        "computer, the public part goes on the board.",
        "term.api_key": "A secret code from an AI service that lets the framework use your account there; whoever "
                        "has it can spend your money, so keep it secret.",
        "term.app_access_code": "A secret code an AI app presents to reach the framework over the network (HTTP); "
                                "each has its own permissions and can be revoked.",
        "term.keyring": "Your system's password store (Windows Credential Manager, macOS Keychain, GNOME Keyring or "
                        "KWallet); the framework keeps the key of its vault there.",
        "term.vault": "The framework's encrypted file for your access keys and passwords; its own key is "
                      "protected by the keyring, a helper program or a passphrase.",
        "term.passphrase": "A long password, often several words; it protects the vault or an encrypted pack. "
                           "Nobody can recover it if you forget it.",
        "term.signature": "A cryptographic seal: it proves who made a file and that nobody changed it since.",
        "term.ditto": "The framework's built-in assistant: it answers questions and can use the framework's tools "
                      "within the permissions you set. Experimental.",
        "term.ai_service": "The company or program that runs the language model Ditto talks to: a cloud service "
                           "(paid per use) or a local model on this computer.",
        "term.local_runtime": "Ditto's local model program (llama-server) with its model on this computer: free and "
                              "private, nothing leaves the computer.",
        "term.model_size": "How big the local model is that Ditto uses; a bigger one answers better but needs more "
                           "memory and is slower.",
        "term.offline_mode": "With it on, only local models answer: a cloud AI service is refused, and the "
                             "knowledge base downloads nothing.",
        "term.mcp": "Model Context Protocol: the open standard through which AI apps (for example Claude Desktop) "
                    "use the framework's tools.",
        "term.tool_permission": "Your choice of what an AI may do with each tool: never, ask you first, or allowed.",
        "term.standing_approval": "A permission you give once, so an AI may repeat exactly this kind of action for "
                                  "a limited time without asking again; you can revoke it.",
        "term.audit_log": "The framework's signed record of every tool call by an AI and every decision: who, what, "
                          "when and with which result.",
        "term.mojo": "The framework's health check: it tests the installation (Docker, tools, disk space, "
                     "settings) and says what to repair.",
        "term.rag": "Retrieval-augmented generation: before answering, Ditto searches your knowledge base and gives "
                    "the model the matching excerpts with their sources.",
        "term.knowledge_base": "Your collection of documents and web pages that Ditto searches before it answers; "
                               "it stays on this computer.",
        "term.qdrant": "The vector database that holds the knowledge base; it finds text by its meaning, not only "
                       "by exact words.",
        "term.embedding": "A list of numbers that describes the meaning of a text; texts with similar meanings get "
                          "similar numbers, which is how the knowledge base finds them.",
        "term.reranker": "A second, more careful model that sorts the knowledge base's search results again, so "
                         "the best excerpts come first.",
        "term.deep_ingest": "Reads public web pages you name into your knowledge base and honours the sites' "
                            "opt-outs (robots.txt, TDMRep).",
        "term.knowledge_snapshot": "A saved copy of the knowledge base, so a later change can be undone or the "
                                   "knowledge can travel with a target.",
        "term.robots_txt": "A file on a website that says which pages automatic readers may fetch; Deep Ingest "
                           "follows it.",
        "term.tdmrep": "TDM Reservation Protocol: a machine-readable way for site owners to reserve text and data "
                       "mining of their pages; Deep Ingest skips such pages.",
        "term.pack": "A signed bundle of knowledge, a target or a recipe from the community zoo; it is checked "
                     "before it is installed.",
    },
    "de": {
        "words.card.name": "Erklärung: {term}",
        "words.card.more": "Mehr dazu: {source}",
        "words.card.more_tip": "Öffnet {url} in deinem Webbrowser.",
        "words.card.described": "{explanation} Mehr dazu: {url}",
        "words.help.name": "{term} erklären",
        "words.help.tip": "Was ist {term}? (F1)",
        "words.error.title": "Etwas ist schiefgelaufen",
        "words.error.next": "Nächster Schritt: {step}",
        "words.error.details": "Technische Details",
        "words.error.details_hint": "Auf Englisch, ohne Geheimnisse und ohne deinen Home-Ordner: Kopiere sie in "
                                    "einen Fehlerbericht oder lass sie dir von Ditto erklären.",
        "words.error.copy": "Details kopieren",
        "words.error.copied": "In die Zwischenablage kopiert.",
        "words.size.bytes": "{n} Byte",
        "words.size.exact": "{size} ({n} Byte)",
        "words.settings.tab.general": "Allgemein",
        "words.address.title": "Sprache und Anrede",
        "words.address.intro": "Wähle die Sprache des Programms. Auf Deutsch kann es dich duzen oder siezen. "
                               "Fachbegriffe bleiben in beiden Sprachen Englisch.",
        "words.address.language": "&Sprache:",
        "words.address.form": "&Anrede auf Deutsch:",
        "words.address.du": "du (persönlich, Standard)",
        "words.address.sie": "Sie (förmlich)",
        "words.address.only_de": "Gilt für die deutschen Texte.",
        "words.address.saved": "Gespeichert. Fenster, die du ab jetzt öffnest, nutzen sie; ein offenes Fenster "
                               "behält seine Texte, bis du es neu öffnest.",
        "words.address.not_saved": "Die Auswahl gilt bis zum Programmende, konnte aber nicht in config.yml "
                                   "gespeichert werden. Siehe Protokoll.",
        "words.lang.en": "English",
        "words.lang.de": "Deutsch",

        "term.llm": "Large Language Model (großes Sprachmodell): ein neuronales Netz, das mit sehr viel Text "
                    "trainiert wurde und Texte fortsetzt und beantwortet. Ditto spricht mit einem.",
        "term.model_format": "Das Dateiformat, das ein Build erzeugt: GGUF läuft mit llama.cpp auf fast jeder CPU "
                             "oder GPU, RKLLM und RKNN laufen auf der NPU von Rockchip-Chips.",
        "term.gguf": "Das Dateiformat, das llama.cpp für Sprachmodelle nutzt: Eine Datei enthält die Gewichte und "
                     "alles, was zum Ausführen nötig ist.",
        "term.safetensors": "Ein sicheres Dateiformat für Modellgewichte, das Hugging Face nutzt: Es enthält nur "
                            "Zahlen, keinen Programmcode.",
        "term.onnx": "Ein offenes Austauschformat für neuronale Netze; die RKNN-Werkzeuge von Rockchip wandeln "
                     "Modelle daraus um.",
        "term.rkllm": "Das Format und die Laufzeit von Rockchip für Sprachmodelle auf der NPU seiner Chips (zum "
                      "Beispiel RK3588 und RK3576).",
        "term.rknn": "Das Format und Toolkit von Rockchip für andere neuronale Netze (zum Beispiel Bildmodelle) auf "
                     "der NPU seiner Chips.",
        "term.quantization": "Die Gewichte eines Modells mit weniger Bits speichern (zum Beispiel 4 statt 16): Die "
                             "Datei wird viel kleiner und schneller, die Antworten etwas ungenauer.",
        "term.q4_k_m": "Etwa 4,5 Bit pro Gewicht mit dem k-quant-Verfahren von llama.cpp: ein üblicher guter "
                       "Kompromiss aus Größe und Qualität.",
        "term.q4_0": "Ein einfacher 4-Bit-Typ: klein und schnell, etwas ungenauer als Q4_K_M; manche ARM-CPUs und "
                     "NPUs führen ihn besonders gut aus.",
        "term.q8_0": "8 Bit pro Gewicht: fast die Qualität des Originalmodells bei etwa halber Größe.",
        "term.f16": "16-Bit-Gleitkommazahlen: das Modell ohne Quantization, mit voller Qualität und Größe.",
        "term.iq_types": "Die kleinsten Typen von llama.cpp, 1 bis 4 Bit pro Gewicht (zum Beispiel IQ2_XS); sie "
                         "brauchen eine Importance Matrix, damit ihre Qualität hält.",
        "term.tq_types": "Ternäre Typen (TQ1_0, TQ2_0) für Modelle, die mit Gewichten von nur -1, 0 und 1 trainiert "
                         "wurden (zum Beispiel BitNet); andere Modelle verlieren damit zu viel Qualität.",
        "term.imatrix": "Eine aus einem Calibration Dataset berechnete Tabelle, welche Gewichte am wichtigsten "
                        "sind; eine Quantization mit wenigen Bits hält diese genauer.",
        "term.calibration_dataset": "Beispieltext, den das Modell bei der Umwandlung einmal liest; daraus wird die "
                                    "Importance Matrix (GGUF) oder die NPU-Quantization (RKLLM, RKNN) berechnet. "
                                    "Text, der deiner späteren Nutzung ähnelt, wirkt am besten.",
        "term.kv_cache": "Speicher, in dem das Modell behält, was es von einem Gespräch schon gelesen hat, damit es "
                         "das nicht neu berechnen muss; er wächst mit der Context Length. Ein quantisierter KV Cache "
                         "(zum Beispiel q8_0) braucht weniger Speicher.",
        "term.token": "Ein Textstück (ein Wort oder Wortteil), wie ein Sprachmodell es liest und schreibt; Längen "
                      "und Grenzen von Modellen werden in Tokens gezählt.",
        "term.context_length": "Wie viele Tokens aus Gespräch und Dokumenten ein Modell auf einmal berücksichtigen "
                               "kann; mehr braucht mehr Speicher.",
        "term.perplexity": "Eine Zahl dafür, wie gut ein Modell einen Testtext vorhersagt; kleiner ist besser. Der "
                           "Vergleich vor und nach der Quantization zeigt den Qualitätsverlust.",
        "term.hugging_face": "Eine Website, auf der Modelle und Datensätze geteilt werden; das Framework lädt "
                             "Modelle von dort.",
        "term.gated_model": "Ein Modell auf Hugging Face, das du erst laden darfst, wenn du dort angemeldet seine "
                            "Bedingungen akzeptiert hast.",
        "term.finished_model": "Das Ergebnis eines erfolgreichen Builds: das umgewandelte Modell mit seinen "
                               "Prüfsummen und einem Protokoll, wie es gebaut wurde.",
        "term.llama_cpp": "Ein Open-Source-Programm, das Sprachmodelle im GGUF-Format auf normalen Computern und "
                          "Boards ausführt; das Framework baut und nutzt es.",
        "term.llama_server": "Das Server-Programm von llama.cpp: Dittos lokales Modellprogramm, das über einen Port "
                             "auf diesem Computer antwortet.",
        "term.llama_bench": "Das Benchmark-Werkzeug von llama.cpp: Es misst, wie viele Tokens pro Sekunde ein "
                            "Modell auf dieser Hardware liest und schreibt.",
        "term.backend": "Der Teil von llama.cpp, der die Rechnungen auf einer Art Hardware ausführt: CPU, CUDA "
                        "(NVIDIA), ROCm (AMD), Vulkan, Metal (Apple) und andere.",
        "term.npu": "Neural Processing Unit: ein Teil eines Chips, der für die Rechnungen neuronaler Netze gebaut "
                    "ist; er führt passende Modelle schneller und sparsamer aus als die CPU.",
        "term.cross_compiling": "Ein Programm auf einem Computer für eine andere Art Computer bauen (zum Beispiel "
                                "auf einem PC für ein ARM-Board).",
        "term.toolchain": "Die Sammlung von Compilern und Werkzeugen, die Programme für eine Art Prozessor und "
                          "System baut.",
        "term.docker": "Ein Programm, das Software in abgeschotteten Containern ausführt; das Framework baut "
                       "Modelle darin, damit dein Computer sauber bleibt.",
        "term.docker_image": "Eine gepackte, schreibgeschützte Vorlage eines Systems mit seinen Werkzeugen; "
                             "Container werden daraus gestartet.",
        "term.container": "Eine laufende, abgeschottete Kopie eines Docker Images: Sie sieht nur, was man ihr gibt, "
                          "und wird nach dem Build entfernt.",
        "term.digest": "Eine Prüfsumme (sha256:...), die genau eine Version eines Images oder einer Datei benennt; "
                       "über den Digest festgelegt, lässt sich der Inhalt nicht unbemerkt austauschen.",
        "term.sha256": "Ein Verfahren, das einen kurzen Prüfwert einer Datei berechnet; ändert sich ein Byte, "
                       "ändert sich der Wert völlig. Downloads werden damit geprüft.",
        "term.build_sandbox": "Die Grenzen der Build-Container: kein Netzwerk, keine Root-Rechte, ein "
                              "schreibgeschütztes System und eine Prozessgrenze, damit Modellcode deinem Computer "
                              "weder schaden noch Daten nach außen senden kann.",
        "term.self_healing": "Wenn ein Build scheitert, liest Ditto das Log und schlägt eine Korrektur vor; nichts "
                             "läuft ohne dein Ja.",
        "term.target": "Die Beschreibung einer Hardware-Familie (Chip, Boards, Formate, Quantization-Typen) mit "
                       "ihren Build-Skripten; du wählst eins im Builder.",
        "term.board": "Ein kleiner Computer auf einer Platine (zum Beispiel Raspberry Pi, Orange Pi oder Rock 5), "
                      "auf dem das fertige Modell läuft.",
        "term.hardware_probe": "Ein kleines Skript, das ausliest, was ein Computer oder Board hat (CPU, Speicher, "
                               "NPU, System), und es als Datei speichert; daraus empfiehlt das Framework ein Target.",
        "term.deployment": "Das fertige Modell und seine Laufzeit auf das Board kopieren und dort starten.",
        "term.trusted_board": "Ein Board, dessen SSH Host Key du bestätigt hast; nur vertraute Boards bekommen ein "
                              "Deployment, auch wenn eine KI darum bittet.",
        "term.systemd": "Der Dienste-Manager der meisten Linux-Systeme; auf einem Board startet er den Modellserver "
                        "beim Hochfahren und nach einem Absturz neu.",
        "term.port": "Eine Nummer, die bei einer Netzwerkverbindung ein Programm auf einem Computer auswählt (zum "
                     "Beispiel 8080 für einen Modellserver).",
        "term.ssh": "Secure Shell: die verschlüsselte Verbindung, über die das Framework ein Board im Netzwerk "
                    "erreicht.",
        "term.ssh_host_key": "Der eigene Schlüssel des Boards: Er beweist bei jeder Verbindung, dass du dasselbe "
                             "Board erreichst und keinen Betrüger. Du bestätigst ihn einmal; ein geänderter Key "
                             "stoppt die Verbindung.",
        "term.fingerprint": "Eine kurze Prüfsumme eines Keys (zum Beispiel SHA256:...), leichter zu vergleichen als "
                            "der Key selbst: Vergleiche ihn mit dem, den das Board anzeigt.",
        "term.ssh_key": "Ein Schlüsselpaar zum Anmelden an einem Board ohne Passwort: Der private Teil bleibt auf "
                        "diesem Computer, der öffentliche kommt auf das Board.",
        "term.api_key": "Ein geheimer Code eines KI-Anbieters, mit dem das Framework dein Konto dort nutzt; wer ihn "
                        "hat, kann dein Geld ausgeben, also halte ihn geheim.",
        "term.app_access_code": "Ein geheimer Code, den eine KI-App vorzeigt, um das Framework über das Netzwerk "
                                "(HTTP) zu erreichen; jeder hat eigene Berechtigungen und kann widerrufen werden.",
        "term.keyring": "Der Passwortspeicher deines Systems (Windows-Anmeldeinformationsverwaltung, "
                        "macOS-Schlüsselbund, GNOME Keyring oder KWallet); das Framework bewahrt dort den Schlüssel "
                        "seines Vaults auf.",
        "term.vault": "Die verschlüsselte Datei des Frameworks (der Vault) für deine API Keys und Passwörter; ihr "
                      "eigener Schlüssel ist durch den Keyring, ein Hilfsprogramm oder eine Passphrase geschützt.",
        "term.passphrase": "Ein langes Passwort, oft aus mehreren Wörtern; es schützt den Vault oder ein "
                           "verschlüsseltes Pack. Niemand kann es wiederherstellen, wenn du es vergisst.",
        "term.signature": "Ein kryptografisches Siegel: Es beweist, wer eine Datei erstellt hat und dass niemand "
                          "sie seitdem verändert hat.",
        "term.ditto": "Der eingebaute Assistent des Frameworks: Er beantwortet Fragen und kann die Tools des "
                      "Frameworks im Rahmen deiner Berechtigungen nutzen. Experimentell.",
        "term.ai_service": "Die Firma oder das Programm, das das Sprachmodell betreibt, mit dem Ditto spricht: ein "
                           "Cloud-Dienst (Bezahlung pro Nutzung) oder ein lokales Modell auf diesem Computer.",
        "term.local_runtime": "Dittos lokales Modellprogramm (llama-server) mit seinem Modell auf diesem Computer: "
                              "kostenlos und privat, nichts verlässt den Computer.",
        "term.model_size": "Wie groß das lokale Modell ist, das Ditto nutzt; ein größeres antwortet besser, braucht "
                           "aber mehr Speicher und ist langsamer.",
        "term.offline_mode": "Ist er an, antworten nur lokale Modelle: Ein Cloud-KI-Anbieter wird abgelehnt, und die "
                             "Wissensbasis lädt nichts herunter.",
        "term.mcp": "Model Context Protocol: der offene Standard, über den KI-Apps (zum Beispiel Claude Desktop) die "
                    "Tools des Frameworks nutzen.",
        "term.tool_permission": "Deine Wahl, was eine KI mit jedem Tool tun darf: nie, erst nachfragen oder "
                                "erlaubt.",
        "term.standing_approval": "Eine Erlaubnis, die du einmal gibst, damit eine KI genau diese Art Aktion für "
                                  "begrenzte Zeit ohne Nachfrage wiederholen darf; du kannst sie widerrufen.",
        "term.audit_log": "Das signierte Protokoll des Frameworks über jeden Tool-Aufruf einer KI und jede "
                          "Entscheidung: wer, was, wann und mit welchem Ergebnis.",
        "term.mojo": "Der Gesundheitscheck des Frameworks: Er prüft die Installation (Docker, Werkzeuge, "
                     "Speicherplatz, Einstellungen) und sagt, was zu reparieren ist.",
        "term.rag": "Retrieval-Augmented Generation: Vor einer Antwort durchsucht Ditto deine Wissensbasis und gibt "
                    "dem Modell die passenden Auszüge mit ihren Quellen.",
        "term.knowledge_base": "Deine Sammlung von Dokumenten und Webseiten, die Ditto vor einer Antwort durchsucht; "
                               "sie bleibt auf diesem Computer.",
        "term.qdrant": "Die Vektordatenbank, in der die Wissensbasis liegt; sie findet Text nach seiner Bedeutung, "
                       "nicht nur nach genauen Wörtern.",
        "term.embedding": "Eine Zahlenreihe, die die Bedeutung eines Textes beschreibt; Texte mit ähnlicher "
                          "Bedeutung bekommen ähnliche Zahlen, so findet die Wissensbasis sie.",
        "term.reranker": "Ein zweites, genaueres Modell, das die Suchergebnisse der Wissensbasis neu sortiert, damit "
                         "die besten Auszüge vorne stehen.",
        "term.deep_ingest": "Liest öffentliche Webseiten, die du angibst, in deine Wissensbasis ein und beachtet "
                            "dabei die Opt-outs der Seiten (robots.txt, TDMRep).",
        "term.knowledge_snapshot": "Eine gespeicherte Kopie der Wissensbasis, damit eine spätere Änderung "
                                   "rückgängig gemacht werden kann oder das Wissen mit einem Target mitreisen kann.",
        "term.robots_txt": "Eine Datei auf einer Website, die sagt, welche Seiten automatische Leser abrufen dürfen; "
                           "Deep Ingest hält sich daran.",
        "term.tdmrep": "TDM Reservation Protocol: ein maschinenlesbarer Weg, mit dem sich Seitenbetreiber das Text- "
                       "und Data-Mining ihrer Seiten vorbehalten; Deep Ingest überspringt solche Seiten.",
        "term.pack": "Ein signiertes Paket mit Wissen, einem Target oder einem Rezept aus dem Community-Zoo; es wird "
                     "vor der Installation geprüft.",
    },
}

# The "Sie" form of every German text above that addresses the person (Q1): only the texts that differ.
FORMAL = {
    "de": {
        "words.card.more_tip": "Öffnet {url} in Ihrem Webbrowser.",
        "words.error.details_hint": "Auf Englisch, ohne Geheimnisse und ohne Ihren Home-Ordner: Kopieren Sie sie in "
                                    "einen Fehlerbericht oder lassen Sie sie sich von Ditto erklären.",
        "words.address.intro": "Wählen Sie die Sprache des Programms. Auf Deutsch kann es Sie duzen oder siezen. "
                               "Fachbegriffe bleiben in beiden Sprachen Englisch.",
        "words.address.saved": "Gespeichert. Fenster, die Sie ab jetzt öffnen, nutzen sie; ein offenes Fenster "
                               "behält seine Texte, bis Sie es neu öffnen.",
        "term.calibration_dataset": "Beispieltext, den das Modell bei der Umwandlung einmal liest; daraus wird die "
                                    "Importance Matrix (GGUF) oder die NPU-Quantization (RKLLM, RKNN) berechnet. "
                                    "Text, der Ihrer späteren Nutzung ähnelt, wirkt am besten.",
        "term.gated_model": "Ein Modell auf Hugging Face, das Sie erst laden dürfen, wenn Sie dort angemeldet seine "
                            "Bedingungen akzeptiert haben.",
        "term.docker": "Ein Programm, das Software in abgeschotteten Containern ausführt; das Framework baut "
                       "Modelle darin, damit Ihr Computer sauber bleibt.",
        "term.build_sandbox": "Die Grenzen der Build-Container: kein Netzwerk, keine Root-Rechte, ein "
                              "schreibgeschütztes System und eine Prozessgrenze, damit Modellcode Ihrem Computer "
                              "weder schaden noch Daten nach außen senden kann.",
        "term.self_healing": "Wenn ein Build scheitert, liest Ditto das Log und schlägt eine Korrektur vor; nichts "
                             "läuft ohne Ihr Ja.",
        "term.target": "Die Beschreibung einer Hardware-Familie (Chip, Boards, Formate, Quantization-Typen) mit "
                       "ihren Build-Skripten; Sie wählen eines im Builder.",
        "term.trusted_board": "Ein Board, dessen SSH Host Key Sie bestätigt haben; nur vertraute Boards bekommen "
                              "ein Deployment, auch wenn eine KI darum bittet.",
        "term.ssh_host_key": "Der eigene Schlüssel des Boards: Er beweist bei jeder Verbindung, dass Sie dasselbe "
                             "Board erreichen und keinen Betrüger. Sie bestätigen ihn einmal; ein geänderter Key "
                             "stoppt die Verbindung.",
        "term.fingerprint": "Eine kurze Prüfsumme eines Keys (zum Beispiel SHA256:...), leichter zu vergleichen als "
                            "der Key selbst: Vergleichen Sie ihn mit dem, den das Board anzeigt.",
        "term.api_key": "Ein geheimer Code eines KI-Anbieters, mit dem das Framework Ihr Konto dort nutzt; wer ihn "
                        "hat, kann Ihr Geld ausgeben, also halten Sie ihn geheim.",
        "term.keyring": "Der Passwortspeicher Ihres Systems (Windows-Anmeldeinformationsverwaltung, "
                        "macOS-Schlüsselbund, GNOME Keyring oder KWallet); das Framework bewahrt dort den Schlüssel "
                        "seines Vaults auf.",
        "term.vault": "Die verschlüsselte Datei des Frameworks (der Vault) für Ihre API Keys und Passwörter; ihr "
                      "eigener Schlüssel ist durch den Keyring, ein Hilfsprogramm oder eine Passphrase geschützt.",
        "term.passphrase": "Ein langes Passwort, oft aus mehreren Wörtern; es schützt den Vault oder ein "
                           "verschlüsseltes Pack. Niemand kann es wiederherstellen, wenn Sie es vergessen.",
        "term.ditto": "Der eingebaute Assistent des Frameworks: Er beantwortet Fragen und kann die Tools des "
                      "Frameworks im Rahmen Ihrer Berechtigungen nutzen. Experimentell.",
        "term.tool_permission": "Ihre Wahl, was eine KI mit jedem Tool tun darf: nie, erst nachfragen oder "
                                "erlaubt.",
        "term.standing_approval": "Eine Erlaubnis, die Sie einmal geben, damit eine KI genau diese Art Aktion für "
                                  "begrenzte Zeit ohne Nachfrage wiederholen darf; Sie können sie widerrufen.",
        "term.rag": "Retrieval-Augmented Generation: Vor einer Antwort durchsucht Ditto Ihre Wissensbasis und gibt "
                    "dem Modell die passenden Auszüge mit ihren Quellen.",
        "term.knowledge_base": "Ihre Sammlung von Dokumenten und Webseiten, die Ditto vor einer Antwort durchsucht; "
                               "sie bleibt auf diesem Computer.",
        "term.deep_ingest": "Liest öffentliche Webseiten, die Sie angeben, in Ihre Wissensbasis ein und beachtet "
                            "dabei die Opt-outs der Seiten (robots.txt, TDMRep).",
    },
}
