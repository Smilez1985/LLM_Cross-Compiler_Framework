#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Network Utilities (v2.3.0)
DIREKTIVE: Goldstandard, Robustness, Security.

Zweck:
Zentrale Klasse für Netzwerk-Operationen.
- Prüft Konnektivität (Ping Loop)
- Lädt Dateien sicher herunter (Hash Check)
- Handhabt Verbindungsabbrüche (Auto-Wait)

Updates v2.3.0:
- Updated version header.
- Integrated with centralized logging.
- Optimized hash calculation (streaming).
"""

import time
import socket
import hashlib
import requests
import logging
import os
from pathlib import Path
from typing import Optional

# Wir nutzen den existierenden Logger oder Fallback
try:
    from orchestrator.utils.logging import get_logger
    logger = get_logger("NetworkGuard")
except ImportError:
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger("NetworkGuard")

class NetworkGuard:
    """
    Enterprise Network Manager.
    Zuständig für:
    - Connectivity Checks (Ping Loop)
    - Sichere Downloads mit Hash-Validierung
    - Auto-Resume bei Verbindungsverlust
    """

    def __init__(self, ping_target="8.8.8.8", ping_port=53, timeout=5):
        self.ping_target = ping_target
        self.ping_port = ping_port
        self.timeout = timeout

    def check_connection(self) -> bool:
        """
        Prüft die Internetverbindung via DNS-Port Ping (schneller/stabiler als ICMP).
        """
        try:
            socket.setdefaulttimeout(self.timeout)
            # Wir nutzen socket connect, da ICMP oft von Firewalls blockiert wird
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.connect((self.ping_target, self.ping_port))
            return True
        except (socket.timeout, socket.error):
            return False

    def wait_for_network(self, verbose: bool = True):
        """
        Blockiert die Ausführung (Ping-Loop), bis das Netzwerk verfügbar ist.
        """
        if self.check_connection():
            return

        if verbose:
            logger.warning("Netzwerkverbindung unterbrochen. Warte auf Wiederherstellung...")

        start_time = time.time()
        attempt = 0
        
        while not self.check_connection():
            attempt += 1
            # Exponential Backoff light: Warte nicht länger als 5 Sekunden
            sleep_time = min(5, 1 + (attempt * 0.5))
            time.sleep(sleep_time)
            
            # Alle 10 Sekunden ein Log-Update
            if attempt % 5 == 0 and verbose:
                elapsed = int(time.time() - start_time)
                logger.info(f"Warte auf Netzwerk... ({elapsed}s)")

        if verbose:
            logger.info("Netzwerkverbindung wiederhergestellt.")

    def download_file(self, url: str, target_path: Path, expected_sha256: Optional[str] = None) -> bool:
        """
        Lädt eine Datei sicher herunter.
        
        Features:
        - Prüft VORHER das Netzwerk.
        - Streamt den Download (RAM-schonend).
        - Validiert OPTIONAL den SHA256 Hash.
        - Löscht die Datei bei Fehler sofort wieder (keine korrupten Reste).
        """
        self.wait_for_network()
        
        target_path = Path(target_path)
        temp_path = target_path.with_suffix(target_path.suffix + ".tmp")
        
        logger.info(f"Starte Download: {url}")
        
        try:
            # Request mit Stream und Timeout
            # Wir nutzen Session für Retry-Logik
            session = requests.Session()
            adapter = requests.adapters.HTTPAdapter(max_retries=3)
            session.mount('https://', adapter)
            session.mount('http://', adapter)
            
            with session.get(url, stream=True, timeout=30) as r:
                r.raise_for_status()
                
                # Wir berechnen den Hash während des Downloads "on the fly"
                sha256_hash = hashlib.sha256()
                
                with open(temp_path, 'wb') as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
                            if expected_sha256:
                                sha256_hash.update(chunk)

            # Validierung (falls Hash angegeben)
            if expected_sha256:
                calculated_hash = sha256_hash.hexdigest()
                if calculated_hash.lower() != expected_sha256.lower():
                    logger.error(f"Hash-Mismatch bei {target_path.name}!")
                    logger.debug(f"Erwartet: {expected_sha256}")
                    logger.debug(f"Erhalten: {calculated_hash}")
                    
                    # Kaputte Datei löschen
                    if temp_path.exists():
                        temp_path.unlink()
                    return False
                else:
                    logger.info("Datei-Integrität verifiziert (SHA256 Match).")

            # Wenn alles gut ging: Temp -> Final verschieben
            if target_path.exists():
                target_path.unlink()
            temp_path.rename(target_path)
            
            logger.info(f"Download erfolgreich: {target_path.name}")
            return True

        except Exception as e:
            logger.error(f"Download fehlgeschlagen: {e}")
            if temp_path.exists():
                try: temp_path.unlink()
                except: pass
            return False
