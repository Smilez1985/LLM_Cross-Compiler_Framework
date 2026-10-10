#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Ditto Manager (AI Hardware Agent) v2.4.0
DIREKTIVE: Goldstandard. RAG-Enabled Expert System.

This manager orchestrates the AI-based analysis of hardware probes.
It fetches context, manages chat memory, handles offline inference,
and prepares calibration datasets for quantization.

Updates v2.4.0:
- Added `prepare_calibration_dataset` for IMatrix/Smart Calibration.
- Integrated Smart Fallbacks for offline dataset generation.
- robust API Key handling (SSOT).
"""

import json
import os
import logging
import requests
import threading
import random
from pathlib import Path
from typing import Dict, Optional, Any, List, Union

# External Libraries
try:
    from litellm import completion
    import tiktoken
except ImportError:
    completion = None
    tiktoken = None

# Native Inference (Offline)
try:
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer, TextIteratorStreamer
    TRANSFORMERS_AVAILABLE = True
except ImportError:
    TRANSFORMERS_AVAILABLE = False

from orchestrator.utils.logging import get_logger
from orchestrator.utils.untrusted import UNTRUSTED_RULE, wrap_untrusted

# RAG Integration
try:
    from orchestrator.Core.rag_manager import RAGManager
except ImportError:
    RAGManager = None

class DittoError(RuntimeError):
    """The model could not answer (provider error, missing model, ...). Never shown or stored as an answer."""


class NativeInferenceEngine:
    """
    Runs LLMs locally using Hugging Face Transformers.
    Zero-Dependency solution for Offline Mode.
    """
    def __init__(self, model_path: str):
        self.logger = get_logger("NativeInference")
        self.model_path = model_path
        self.model = None
        self.tokenizer = None
        self._loaded = False

    def load(self):
        if not TRANSFORMERS_AVAILABLE:
            raise ImportError("Transformers library not found. Cannot run offline mode.")
            
        self.logger.info(f"Loading native model from {self.model_path}...")
        try:
            # Determine device
            device = "cuda" if torch.cuda.is_available() else "cpu"
            torch_dtype = torch.float16 if device == "cuda" else torch.float32
            
            self.tokenizer = AutoTokenizer.from_pretrained(self.model_path)
            self.model = AutoModelForCausalLM.from_pretrained(
                self.model_path,
                torch_dtype=torch_dtype,
                device_map="auto" if device == "cuda" else None,
                low_cpu_mem_usage=True
            )
            if device == "cpu":
                self.model.to("cpu")
                
            self._loaded = True
            self.logger.info(f"Model loaded successfully on {device}.")
        except Exception as e:
            self.logger.error(f"Failed to load native model: {e}")
            raise e

    def generate(self, messages: List[Dict[str, str]], max_new_tokens=1024) -> str:
        if not self._loaded: self.load()
        
        # Simple Chat Template applying
        try:
            prompt = self.tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        except Exception:
            # Fallback: Raw concatenation
            prompt = ""
            for m in messages:
                prompt += f"{m['role'].upper()}: {m['content']}\n"
            prompt += "ASSISTANT:"

        inputs = self.tokenizer(prompt, return_tensors="pt").to(self.model.device)
        
        with torch.no_grad():
            outputs = self.model.generate(
                **inputs, 
                max_new_tokens=max_new_tokens,
                do_sample=True,
                temperature=0.2 # Low temp for deterministic code generation
            )
            
        response = self.tokenizer.decode(outputs[0][inputs.input_ids.shape[1]:], skip_special_tokens=True)
        return response

class DittoCoder:
    """
    AI Agent for Hardware Analysis, Chat, System Diagnosis, and Smart Calibration.
    """
    
    def __init__(self, provider: str = "OpenAI", model: str = "gpt-6.1-sol", 
                 api_key: Optional[str] = None, base_url: Optional[str] = None,
                 config_manager = None, framework_manager = None):
        self.logger = get_logger("DittoManager")
        self.config = config_manager
        self.framework = framework_manager
        
        # Handle Constructor Overload (Framework passing itself as config_manager sometimes or specific kwargs)
        # If framework_manager is None but config_manager has 'config' attr, it might be FrameworkManager
        if framework_manager is None and hasattr(config_manager, 'config'):
            self.framework = config_manager
            self.config = config_manager.config
        elif framework_manager is not None:
             self.config = framework_manager.config

        # Configuration
        self.provider = provider
        self.model_name = model
        self.base_url = self._endpoint_for(provider, base_url)
        self.api_key: Optional[str] = None
        self.litellm_model = self._format_model_name(provider, model)
        self.offline_mode = False
        
        # Settings from ConfigManager
        if self.config:
            # Use .get() if available (ConfigManager), else getattr
            get_fn = getattr(self.config, 'get', lambda k, d=None: getattr(self.config, k, d))
            self.offline_mode = get_fn("offline_mode", False)
            self.context_limit = get_fn("chat_context_limit", 4096)
        else:
            self.context_limit = 4096

        # Native Engine
        self.native_engine: Optional[NativeInferenceEngine] = None
        
        # Cloud Setup
        if not self.offline_mode:
            self._setup_cloud_provider(provider, model, api_key, base_url)

    # Older CLI/config strings for OpenAI-compatible servers.
    _LEGACY_PROVIDER_IDS = {"LocalAI": "Custom", "Compatible": "Custom"}

    @classmethod
    def _provider_id(cls, provider: Optional[str]) -> str:
        first = provider.split()[0] if provider else ""
        return cls._LEGACY_PROVIDER_IDS.get(first, first)

    @classmethod
    def _endpoint_for(cls, provider: Optional[str], base_url: Optional[str]) -> Optional[str]:
        # Cloud providers have fixed endpoints; a leftover Ollama/Custom URL would receive their API key.
        return (base_url or None) if cls._provider_id(provider) in ("Ollama", "Custom") else None

    def _setup_cloud_provider(self, provider, model, api_key, base_url):
        self.litellm_model = self._format_model_name(provider, model)
        self.base_url = self._endpoint_for(provider, base_url)

        # SecretsManager Integration
        final_key = api_key
        provider_key = self._provider_id(provider)
        if not final_key and self.framework and getattr(self.framework, 'secrets_manager', None):
            key_map = {
                "OpenAI": "openai_api_key",
                "Anthropic": "anthropic_api_key",
                "Google": "gemini_api_key",
                "Custom": "custom_api_key",
                "HuggingFace": "hf_token"
            }
            if provider_key in key_map:
                stored_key = self.framework.secrets_manager.get_secret(key_map[provider_key])
                if stored_key:
                    final_key = stored_key
                    self.logger.info(f"Loaded API Key for {provider} from secure Keyring.")

        # The key is passed per call only; exporting it to os.environ would hand it to every child process.
        self.api_key = final_key if final_key and final_key != "sk-dummy" else None

    def _format_model_name(self, provider: str, model: str) -> str:
        prefix = {"Ollama": "ollama", "Google": "gemini", "Anthropic": "anthropic", "Custom": "openai"}.get(
            self._provider_id(provider))
        if not prefix or not model or model.startswith(f"{prefix}/"):
            return model
        return f"{prefix}/{model}"

    def _completion_api_key(self) -> Optional[str]:
        if self.api_key:
            return self.api_key
        # Without an explicit key litellm would fall back to OPENAI_API_KEY and send it to the custom endpoint.
        return "EMPTY" if self._provider_id(self.provider) == "Custom" else None

    def _get_rag_manager(self) -> Optional[RAGManager]:
        if not self.framework: return None
        # Robust config check
        get_fn = getattr(self.config, 'get', lambda k, d=None: getattr(self.config, k, d))
        if not get_fn("enable_rag_knowledge", False): return None
        # Embeddings are computed locally; in offline mode the RAG manager only uses cached models.
        return self.framework.get_component("rag_manager")

    def reinitialize(self, provider: Optional[str] = None, model: Optional[str] = None,
                     api_key: Optional[str] = None, base_url: Optional[str] = None,
                     offline_mode: Optional[bool] = None):
        """Applies a new provider/model selection at runtime (e.g. from the GUI)."""
        if provider:
            self.provider = provider
        if model:
            self.model_name = model
        if base_url is not None:
            self.base_url = base_url or None
        self.base_url = self._endpoint_for(self.provider, self.base_url)
        if offline_mode is not None:
            self.offline_mode = offline_mode

        self.litellm_model = self._format_model_name(self.provider, self.model_name)
        if not self.offline_mode:
            self._setup_cloud_provider(self.provider, self.model_name, api_key, self.base_url)

        self.logger.info(f"Ditto reconfigured: Provider={self.provider}, Model={self.model_name}, Offline={self.offline_mode}")

    # --- RAG / DOCS ---
    
    def _fetch_documentation(self, sdk_name: str) -> str:
        """Fetches documentation context (RAG or Web Fallback)."""
        context_text = ""
        rag = self._get_rag_manager()
        
        if rag:
            self.logger.info(f"Querying Knowledge Base for '{sdk_name}'...")
            query = f"{sdk_name} SDK compilation flags build configuration optimization parameters"
            results = rag.search(query, limit=5)
            
            if results:
                context_text += "--- EXPERT KNOWLEDGE (Local RAG) ---\n"
                for res in results:
                    context_text += f"\n[Source: {res.source}]\n{res.content}\n"
                return context_text 

        return ""

    # --- CONTEXT MEMORY MANAGEMENT ---

    def _count_tokens(self, text: str) -> int:
        """Approximates token count using tiktoken."""
        if not tiktoken: return len(text) // 4
        try:
            enc = tiktoken.get_encoding("cl100k_base")
            return len(enc.encode(text))
        except:
            return len(text) // 4

    def _compress_history(self, history: List[Dict[str, str]]) -> List[Dict[str, str]]:
        """
        Compresses chat history if it exceeds context limits.
        """
        total_tokens = sum(self._count_tokens(m["content"]) for m in history)
        
        if total_tokens < self.context_limit:
            return history

        self.logger.info(f"Context limit reached ({total_tokens} tokens). Compressing history...")
        
        if len(history) <= 5: return history 
        
        system_msg = history[0] if history[0]["role"] == "system" else None
        to_summarize = history[1:-4] if system_msg else history[:-4]
        recent_msgs = history[-4:]
        
        text_blob = "\n".join([f"{m['role']}: {m['content']}" for m in to_summarize])
        summary_prompt = f"Summarize the following technical conversation history in 3 concise sentences, preserving key errors and hardware details:\n{text_blob}"
        
        def from_user_turn(messages):
            while messages and messages[0]["role"] != "user":
                messages = messages[1:]
            return messages

        head = [system_msg] if system_msg else []  # the system prompt (with its rules) always stays
        try:
            # The conversation may contain untrusted excerpts, so the summarizer gets the same rule.
            summary = self._query_llm([{"role": "system", "content": UNTRUSTED_RULE},
                                       {"role": "user", "content": summary_prompt}])
            # The summary is model output derived from untrusted text: it goes into a user turn, wrapped.
            summary_turn = {"role": "user", "content": "Summary of the earlier conversation:\n"
                            + wrap_untrusted(summary, "conversation summary")}
            self.logger.info("History compressed successfully.")
            return head + [summary_turn, {"role": "assistant", "content": "Understood."}] + from_user_turn(recent_msgs)
            
        except Exception as e:
            self.logger.error(f"Compression failed: {e}")
            return head + from_user_turn(history[-(len(history)//2):])

    def _query_llm(self, messages: List[Dict[str, str]]) -> str:
        """Unified Interface for Cloud/Offline Generation."""
        
        # A. Offline Mode
        if self.offline_mode:
            if not self.native_engine:
                # Use robust config access
                models_dir = getattr(self.config, 'models_dir', 'models')
                # Try to find tiny_models path based on config or installation path
                if self.framework:
                    base = Path(self.framework.info.installation_path)
                else:
                    base = Path(".")
                
                model_base = base / models_dir / "tiny_models"
                
                if model_base.exists() and any(model_base.iterdir()):
                    # Pick first available model
                    try:
                        target = next(x for x in model_base.iterdir() if x.is_dir())
                        self.native_engine = NativeInferenceEngine(str(target))
                    except StopIteration:
                        raise DittoError("Offline mode is active but no local model was found (empty directory).")
                else:
                    raise DittoError("Offline mode is active but no local model was found. Download one in the wizard.")
            
            return self.native_engine.generate(messages)

        # B. Cloud/Ollama Mode (LiteLLM)
        if not completion:
            raise DittoError("LiteLLM library missing.")
        
        try:
            response = completion(
                model=self.litellm_model,
                messages=messages,
                # No temperature: current Claude and OpenAI models reject non-default sampling parameters.
                api_base=self.base_url,
                api_key=self._completion_api_key()
            )
        except Exception as e:
            raise DittoError(f"LLM API error: {e}") from e
        choice = response.choices[0]
        content = choice.message.content
        if getattr(choice, "finish_reason", None) == "content_filter" or content is None:
            # Claude 5.x reports a refusal as a normal (HTTP 200) response; LiteLLM maps it to content_filter.
            raise DittoError(f"The model declined to answer ({self.model_name}).")
        return content

    # --- PUBLIC API ---

    def ask_ditto(self, question: str, history: List[Dict[str, str]]) -> str:
        """
        Interactive Chat Entry Point.
        """
        # 1. RAG Retrieval
        rag_context = ""
        rag = self._get_rag_manager()
        if rag:
            results = rag.search(question)
            if results:
                rag_context = "\n".join([f"- [{r.source}] {r.content}" for r in results])

        # 2. Construct/Update History. Retrieved text is data and goes into the user turn, never the system prompt.
        base_system = (
            "You are Ditto, the assistant of the LLM Cross-Compiler Framework, which converts and quantizes "
            "language models for edge hardware (GGUF, RKLLM, RKNN) and deploys them. Help the user configure "
            "their builds and understand errors. Answer in the language of the user's question. "
            "Say when you are unsure.\n" + UNTRUSTED_RULE
        )
        
        messages_for_llm = [{"role": "system", "content": base_system}]
        
        if history:
            messages_for_llm.extend(history)

        user_content = question
        if rag_context:
            user_content += "\n\nPossibly relevant knowledge base excerpts:\n" + wrap_untrusted(rag_context, "knowledge base")
        messages_for_llm.append({"role": "user", "content": user_content})

        # 3. Compress if needed
        optimized_messages = self._compress_history(messages_for_llm)

        # 4. Generate
        return self._query_llm(optimized_messages)

    def analyze_error_log(self, log_content: str, context_info: str) -> Dict[str, Any]:
        """
        Analyzes a build log and returns a structured JSON fix proposal.
        """
        # 1. RAG Check for similar errors
        rag_context = ""
        rag = self._get_rag_manager()
        if rag:
            # Extract last few lines as query
            query = "\n".join(log_content.strip().split('\n')[-3:])
            results = rag.search(query)
            if results:
                rag_context = "\n".join([f"- {r.content}" for r in results])

        system_prompt = """
        You are the Self-Healing System of an Embedded AI Framework.
        Analyze the provided ERROR LOG.
        
        OUTPUT JSON ONLY:
        {
            "summary": "Brief error description",
            "root_cause": "Technical explanation",
            "fix_command": "Single bash command to fix it",
            "confidence": 0.0 to 1.0,
            "target": "HOST" or "DEVICE"
        }
        NO MARKDOWN. NO EXPLANATIONS.
        """ + UNTRUSTED_RULE

        user_prompt = f"CONTEXT: {context_info}\n\nLOG:\n" + wrap_untrusted(log_content[-3000:], "build log")
        if rag_context:
            user_prompt += "\n\nKNOWN ISSUES (knowledge base):\n" + wrap_untrusted(rag_context, "knowledge base")

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]

        try:
            response = self._query_llm(messages) or ""
            # Clean JSON
            if "```" in response:
                import re
                match = re.search(r"```(?:json)?(.*?)```", response, re.DOTALL)
                if match: response = match.group(1).strip()
            
            return json.loads(response)
        except Exception as e:
            self.logger.error(f"Analysis failed: {e}")
            return {}

    def generate_module_content(self, probe_file: Path) -> Dict[str, Any]:
        """
        Analysiert die Probe-Datei und generiert die Modul-Konfiguration.
        """
        if not probe_file.exists():
            raise FileNotFoundError(f"Probe file not found: {probe_file}")

        # 1. Hardware Daten lesen
        probe_data = probe_file.read_text(encoding="utf-8", errors="ignore")
        
        # 2. Determine SDK Hint
        sdk_hint = "generic"
        lower_probe = probe_data.lower()
        if "nvidia" in lower_probe or "tegra" in lower_probe: sdk_hint = "nvidia"
        elif "rockchip" in lower_probe or "rk3" in lower_probe: sdk_hint = "rockchip"
        elif "hailo" in lower_probe: sdk_hint = "hailo"
        elif "intel" in lower_probe: sdk_hint = "intel"
        elif "riscv" in lower_probe: sdk_hint = "riscv"
        elif "memryx" in lower_probe: sdk_hint = "memryx" 
        elif "axelera" in lower_probe: sdk_hint = "axelera" 
        
        # 3. Fetch Context (RAG or Web)
        doc_context = self._fetch_documentation(sdk_hint)

        # 4. Construct System Prompt
        system_prompt = """
        You are 'Ditto', an expert Embedded Systems Engineer.
        Analyze the hardware probe and generate a JSON configuration to fill the framework templates.
        
        TASKS:
        1. Analyze Hardware (Arch, CPU Flags, NPU).
        2. Generate Bash Code blocks for 'build.sh'.
        
        REQUIRED JSON STRUCTURE:
        {
            "module_name": "Str",
            "architecture": "aarch64|x86_64",
            "sdk": "Str",
            "base_os": "Docker Image Name",
            "packages": ["list", "of", "packages"],
            "cpu_flags": "GCC Flags",
            "cmake_flags": "CMake Flags",
            "setup_commands": "Bash commands build.sh runs before converting, as the unprivileged build user and without network, e.g. exporting SDK variables (optional)",
            "quantization_logic": "Bash CASE block content for build.sh"
        }
        """ + UNTRUSTED_RULE

        user_prompt = (
            "--- INPUT: target_hardware_config.txt ---\n"
            + wrap_untrusted(probe_data[:8000], "hardware probe")
            + "\n\nBased on this probe, generate the optimal configuration JSON."
        )
        if doc_context:
            user_prompt += "\n\nDocumentation context:\n" + wrap_untrusted(doc_context, "knowledge base")
        
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]

        # 5. Call LLM
        try:
            content = self._query_llm(messages)
            
            if "```" in content:
                import re
                match = re.search(r"```(?:json)?(.*?)```", content, re.DOTALL)
                if match: content = match.group(1).strip()

            return json.loads(content)
            
        except Exception as e:
            self.logger.error(f"AI Analysis failed: {e}")
            raise e

    def prepare_calibration_dataset(self, output_path: Path, language: str = "en") -> bool:
        """
        v2.4.0: Generates or downloads a calibration dataset for IMatrix quantization.
        Called by FrameworkManager before Build.
        """
        if output_path.exists() and output_path.stat().st_size > 0:
            self.logger.info(f"Calibration dataset already exists: {output_path.name}")
            return True
            
        self.logger.info(f"Preparing calibration dataset at {output_path}...")
        
        # 1. Try to download standard dataset (WikiText Subset)
        # We use a known public domain text or standard URL
        dataset_url = "https://raw.githubusercontent.com/ggerganov/llama.cpp/master/prompts/chat-with-bob.txt"
        
        if not self.offline_mode:
            try:
                self.logger.info(f"Downloading dataset from {dataset_url}...")
                resp = requests.get(dataset_url, timeout=10)
                if resp.status_code == 200:
                    with open(output_path, 'w', encoding='utf-8') as f:
                        f.write(resp.text)
                        # Append some technical context for better calibration on code
                        f.write("\n\n" + ("def function(): pass\n" * 50)) 
                    return True
            except Exception as e:
                self.logger.warning(f"Download failed: {e}. Switching to synthetic generation.")

        # 2. Synthetic Generation (Fallback / Offline)
        try:
            with open(output_path, 'w', encoding='utf-8') as f:
                # Basic English calibration
                f.write("The quick brown fox jumps over the lazy dog.\n" * 50)
                
                # Technical / Code context (Important for coding models)
                code_snippet = """
                import os
                def optimize_tensor(t):
                    return t * 0.5
                class NeuralNetwork:
                    def __init__(self):
                        self.layers = []
                """
                f.write((code_snippet + "\n") * 20)
                
                # Conversational padding
                f.write("User: Hello AI.\nAssistant: Hello! How can I help you regarding hardware acceleration?\n" * 20)
                
            self.logger.info("Generated synthetic calibration dataset.")
            return True
        except Exception as e:
            self.logger.error(f"Failed to generate dataset: {e}")
            return False

    def save_module(self, module_name: str, config: Dict[str, Any], targets_dir: Path):
        """Wrapper um den ModuleGenerator aufzurufen."""
        
        # Import here to avoid circular dependency
        from orchestrator.Core.module_generator import ModuleGenerator
        
        packages = config.get("packages", "")
        if isinstance(packages, str):
            packages = packages.split()
            
        gen_data = {
            "module_name": module_name,
            "architecture": config.get("architecture", "aarch64"),
            "sdk": config.get("sdk", "none"),
            "description": f"AI-generated target for {module_name}",
            "base_os": config.get("base_os", "debian:bookworm-slim"),
            "packages": packages,
            "cpu_flags": config.get("cpu_flags", ""),
            "cmake_flags": config.get("cmake_flags", ""),
            "quantization_logic": config.get("quantization_logic", ""), 
            "setup_commands": config.get("setup_commands", "# Auto-generated setup by Ditto"),
            "detection_commands": "lscpu",
            "supported_boards": [module_name]
        }
        
        generator = ModuleGenerator(targets_dir)
        return generator.generate_module(gen_data, self.framework)
