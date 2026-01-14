#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Transparency Manager (RepE Integration) v1.0.0
DIREKTIVE: Goldstandard, Opt-In Transparency Layer.

Zweck:
Analysiert Ditto's Reasoning mittels Representation Engineering (RepE).
Ermöglicht transparente Einblicke in AI-Entscheidungen und verhindert Hallucinations.
"""

from pathlib import Path
from typing import Dict, List, Optional, Any
from dataclasses import dataclass

try:
    from transformers import pipeline, AutoModelForCausalLM, AutoTokenizer
    from repe import repe_pipeline_registry
    REPE_AVAILABLE = True
except ImportError:
    REPE_AVAILABLE = False

from orchestrator.utils.logging import get_logger

@dataclass
class TransparencyReport:
    """Strukturierter Report über Ditto's Reasoning."""
    hallucination_score: float  # 0.0 - 1.0 (niedrig ist gut)
    truthfulness_score: float   # 0.0 - 1.0 (hoch ist gut)
    confidence_level: float     # 0.0 - 1.0
    reasoning_quality: str      # "EXCELLENT" / "GOOD" / "QUESTIONABLE" / "UNRELIABLE"
    warnings: List[str]
    raw_scores: Dict[str, float]

class TransparencyManager:
    """
    RepE-basierter Transparenz-Layer für Ditto's AI-Entscheidungen.
    """
    
    def __init__(self, framework_manager):
        self.logger = get_logger("TransparencyManager")
        self.framework = framework_manager
        self.config = framework_manager.config
        
        self.rep_reader = None
        self._enabled = False
        
        # Prüfe ob Feature aktiviert ist
        if not getattr(self.config, 'enable_repe_transparency', False):
            self.logger.info("RepE Transparency disabled in config.")
            return
            
        if not REPE_AVAILABLE:
            self.logger.warning("RepE libraries not installed. Run: pip install representation-engineering")
            return
            
        self._enabled = True

    def initialize(self) -> bool:
        """Initialisiert RepE Pipeline."""
        if not self._enabled:
            return False
            
        try:
            self.logger.info("Initializing RepE Transparency Layer...")
            
            # Register RepE Tasks
            repe_pipeline_registry()
            
            # Model Path aus Config
            model_path = getattr(self.config, 'repe_model_path', None)
            
            if not model_path:
                # Fallback: Nutze Ditto's Model (falls offline)
                ditto = self.framework.get_component("ditto_manager")
                if ditto and ditto.offline_mode and ditto.native_engine:
                    model_path = ditto.native_engine.model_path
                else:
                    self.logger.warning("No RepE model configured. Using HuggingFace default.")
                    model_path = "meta-llama/Llama-2-7b-hf"  # Kleines Default-Modell
            
            # Load Pipeline
            tokenizer = AutoTokenizer.from_pretrained(model_path)
            model = AutoModelForCausalLM.from_pretrained(model_path, device_map="auto", low_cpu_mem_usage=True)
            
            self.rep_reader = pipeline("rep-reading", model=model, tokenizer=tokenizer)
            
            self.logger.info(f"RepE initialized with model: {model_path}")
            return True
            
        except Exception as e:
            self.logger.error(f"RepE initialization failed: {e}")
            self._enabled = False
            return False

    def analyze_reasoning(self, prompt: str, response: str, context: str = "") -> Optional[TransparencyReport]:
        """
        Analysiert Ditto's Antwort mit RepE.
        
        Args:
            prompt: Die User-Frage
            response: Ditto's Antwort
            context: Optional RAG-Context
            
        Returns:
            TransparencyReport oder None bei Fehler
        """
        if not self._enabled or not self.rep_reader:
            return None
            
        try:
            # Konstruiere Messages für RepE
            messages = [
                {"role": "system", "content": "You are Ditto, an AI Hardware Expert."},
                {"role": "user", "content": prompt}
            ]
            
            if context:
                messages.insert(1, {"role": "system", "content": f"Context: {context}"})
                
            messages.append({"role": "assistant", "content": response})
            
            # RepE Analysis
            results = self.rep_reader(messages)
            
            # Score Extraction (RepE gibt dict zurück)
            hallucination = results.get("hallucination_score", 0.0)
            truthfulness = results.get("truthfulness_score", 1.0)
            confidence = results.get("confidence", 0.5)
            
            # Quality Assessment
            warnings = []
            
            if hallucination > self.config.repe_threshold_hallucination:
                warnings.append(f"HIGH HALLUCINATION RISK: {hallucination:.1%}")
                
            if truthfulness < self.config.repe_threshold_truthfulness:
                warnings.append(f"LOW TRUTHFULNESS: {truthfulness:.1%}")
                
            if confidence < 0.5:
                warnings.append(f"LOW CONFIDENCE: {confidence:.1%}")
            
            # Reasoning Quality
            if not warnings:
                quality = "EXCELLENT"
            elif len(warnings) == 1:
                quality = "GOOD"
            elif hallucination > 0.3 or truthfulness < 0.6:
                quality = "UNRELIABLE"
            else:
                quality = "QUESTIONABLE"
            
            report = TransparencyReport(
                hallucination_score=hallucination,
                truthfulness_score=truthfulness,
                confidence_level=confidence,
                reasoning_quality=quality,
                warnings=warnings,
                raw_scores=results
            )
            
            self.logger.debug(f"Transparency Report: {quality} (H:{hallucination:.2f} T:{truthfulness:.2f})")
            return report
            
        except Exception as e:
            self.logger.error(f"RepE analysis failed: {e}")
            return None

    def format_report_for_ui(self, report: TransparencyReport) -> str:
        """Formatiert Report für Terminal/GUI Ausgabe."""
        if not report:
            return "⚠️ Transparency unavailable"
            
        icons = {
            "EXCELLENT": "✅",
            "GOOD": "✔️",
            "QUESTIONABLE": "⚠️",
            "UNRELIABLE": "❌"
        }
        
        output = f"\n{icons.get(report.reasoning_quality, '?')} Ditto Reasoning Quality: {report.reasoning_quality}\n"
        output += f"  Hallucination Risk: {report.hallucination_score:.1%}\n"
        output += f"  Truthfulness: {report.truthfulness_score:.1%}\n"
        output += f"  Confidence: {report.confidence_level:.1%}\n"
        
        if report.warnings:
            output += "\n  Warnings:\n"
            for w in report.warnings:
                output += f"    - {w}\n"
                
        return output
