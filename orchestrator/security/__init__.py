"""
Secret hygiene and what may leave this computer (bottom layer, Qt-free): the shared table of secret formats
(secret_patterns, also loaded by scripts/secret_scan.py), the rules for personal data that scan and the blueprint's
scrub test use (personal_data), SecretStr for credentials held in memory, the logging
redaction (redaction), paths in text an AI reads (paths: label_paths, shorten_home, device_text, scrub_for_ai) and
HTTP that reaches only public addresses (http: PublicOnlyAdapter).
"""
