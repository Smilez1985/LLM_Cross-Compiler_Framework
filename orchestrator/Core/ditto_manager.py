#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Ditto Manager (AI Hardware Agent) v2.3.0
DIREKTIVE: Goldstandard. RAG-Enabled Expert System.

This manager orchestrates the AI-based analysis of hardware probes.
It fetches context, manages chat memory, and handles offline inference.

Updates v2.3.0:
- Integrated SecretsManager for secure API Key retrieval (Keyring).
- Added `analyze_error_log` for Self-Healing integration.
- Preserved NativeInferenceEngine for offline capability.
- Robust ConfigManager handling in constructor.
"""

import json
import os
import logging
import requests
import threading
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
# Lazy import ModuleGenerator only when needed to avoid circular imports? 
# Better: Import at top if structure allows, or inside method.
# We will import inside method save_module to be safe against circular dependency with framework init.

# RAG Integration
try:
    from orchestrator.Core.rag_manager import RAGManager
except ImportError:
    RAGManager = None

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
    AI Agent for Hardware Analysis, Chat, and System Diagnosis.
    """
    
    def __init__(self, provider: str = "OpenAI", model: str = "gpt-4o", 
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

    def _setup_cloud_provider(self, provider, model, api_key, base_url):
        self.litellm_model = self._format_model_name(provider, model)
        self.base_url = base_url
        
        # SecretsManager Integration
        # Wenn kein API Key übergeben wurde (oder dummy), versuche ihn aus dem Keyring zu laden.
        final_key = api_key
        if self.framework and hasattr(self.framework, 'secrets_manager') and self.framework.secrets_manager:
            # Mapping Provider -> Key Name
            key_map = {
                "OpenAI": "openai_api_key",
                "Anthropic": "anthropic_api_key",
                "Google": "gemini_api_key",
                "HuggingFace": "hf_token"
            }
            # Simple heuristic: first word of provider usually matches key
            provider_key = provider.split()[0]
            if provider_key in key_map:
                stored_key = self.framework.secrets_manager.get_secret(key_map[provider_key])
                if stored_key:
                    final_key = stored_key
                    self.logger.info(f"Loaded API Key for {provider} from secure Keyring.")

        if final_key and final_key != "sk-dummy":
            # LiteLLM erwartet oft spezifische Env Vars, wir setzen sie sicherheitshalber
            if "OpenAI" in provider: os.environ["OPENAI_API_KEY"] = final_key
            elif "Anthropic" in provider: os.environ["ANTHROPIC_API_KEY"] = final_key
            elif "Google" in provider: os.environ["GEMINI_API_KEY"] = final_key

    def _format_model_name(self, provider: str, model: str) -> str:
        if "Ollama" in provider: return f"ollama/{model}"
        if "Google" in provider: return f"gemini/{model}"
        return model

    def _get_rag_manager(self) -> Optional[RAGManager]:
        if not self.framework: return None
        # Robust config check
        get_fn = getattr(self.config, 'get', lambda k, d=None: getattr(self.config, k, d))
        if not get_fn("enable_rag_knowledge", False): return None
        
        return self.framework.get_component("rag_manager")

    # --- RAG / DOCS ---
    
    def _fetch_documentation(self, sdk_name: str) -> str:
        """Fetches documentation context (RAG or Web Fallback)."""
        context_text = ""
        rag = self._get_rag_manager()
        
        if rag:
            self.logger.info(f"Querying Knowledge Base for '{sdk_name}'...")
            query = f"{sdk_name} SDK compilation flags build configuration optimization parameters"
            results = rag.search(query, limit=5, score_threshold=0.65)
            
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
        
        try:
            summary = self._query_llm([{"role": "user", "content": summary_prompt}])
            
            new_history = []
            if system_msg:
                new_sys_content = f"{system_msg['content']}\n\n[PREVIOUS CONTEXT SUMMARY]: {summary}"
                new_history.append({"role": "system", "content": new_sys_content})
            else:
                new_history.append({"role": "system", "content": f"Previous Context: {summary}"})
                
            new_history.extend(recent_msgs)
            self.logger.info("History compressed successfully.")
            return new_history
            
        except Exception as e:
            self.logger.error(f"Compression failed: {e}")
            return history[-(len(history)//2):] 

    def _query_llm(self, messages: List[Dict[str, str]]) -> str:
        """Unified Interface for Cloud/Offline Generation."""
        
        # A. Offline Mode
        if self.offline_mode:
            if not self.native_engine:
                # Use robust config access
                models_dir = getattr(self.config, 'models_dir', 'models')
                model_base = Path(self.framework.info.installation_path) / models_dir / "tiny_models" if self.framework else Path(models_dir) / "tiny_models"
                
                if model_base.exists() and any(model_base.iterdir()):
                    target = next(x for x in model_base.iterdir() if x.is_dir())
                    self.native_engine = NativeInferenceEngine(str(target))
                else:
                    return "Error: Offline Mode active but no Tiny Model found. Please download one via Wizard."
            
            return self.native_engine.generate(messages)

        # B. Cloud/Ollama Mode (LiteLLM)
        if not completion: return "Error: LiteLLM library missing."
        
        try:
            response = completion(
                model=self.litellm_model,
                messages=messages,
                temperature=0.2, # Low temp for code
                api_base=self.base_url
            )
            return response.choices[0].message.content
        except Exception as e:
            return f"LLM API Error: {str(e)}"

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
                rag_context = "\n".join([f"- {r.content}" for r in results])

        # 2. Construct/Update History
        base_system = "You are Ditto, an AI Hardware Expert. Help the user configure their build."
        if rag_context:
            base_system += f"\n\nRELEVANT KNOWLEDGE BASE:\n{rag_context}"
        
        messages_for_llm = [{"role": "system", "content": base_system}]
        
        if history:
            messages_for_llm.extend(history)
            
        messages_for_llm.append({"role": "user", "content": question})

        # 3. Compress if needed
        optimized_messages = self._compress_history(messages_for_llm)

        # 4. Generate
        return self._query_llm(optimized_messages)

    def analyze_error_log(self, log_content: str, context_info: str) -> Dict[str, Any]:
        """
        NEU: Dedicated Method for Self-Healing Manager.
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
        """
        
        if rag_context:
            system_prompt += f"\n\nKNOWN ISSUES:\n{rag_context}"

        user_prompt = f"CONTEXT: {context_info}\n\nLOG:\n{log_content[-3000:]}"

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]

        try:
            response = self._query_llm(messages)
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
            "setup_commands": "Bash code for Dockerfile setup (optional)",
            "quantization_logic": "Bash CASE block content for build.sh"
        }
        
        Documentation Context:
        {doc_context}
        """

        user_prompt = f"""
        --- INPUT: target_hardware_config.txt ---
        {probe_data[:8000]}
        
        Based on this probe, generate the optimal configuration JSON.
        """
        
        messages = [
            {"role": "system", "content": system_prompt.replace("{doc_context}", doc_context)},
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
