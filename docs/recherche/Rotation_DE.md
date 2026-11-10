Version 3.4.0 · Zuletzt aktualisiert 2026-11-10

# Rotation vor der Quantisierung: Recherche

[English version](Rotation.md)

Recherchedatum: 2026-11-10. Alle Quellen wurden an diesem Tag abgerufen. Sie stehen am Ende und werden als [n]
zitiert. Fakten stammen aus der zitierten Quelle, aus dem Code von llama.cpp am Pin des Frameworks (Tag b11429,
Commit `d81235049384534c167caea52b85a694f6103d14`) oder aus unserer eigenen Messung in Abschnitt 5, deren Aufbau
vollständig angegeben ist. Mit **[Schluss]** markierter Text ist unsere eigene Folgerung. Mit **[Unbestätigt]**
markierter Text ließ sich weder an einer Primärquelle noch an einer Messung prüfen.

Das ist die Grundlage für die ROADMAP-Zeile „Rotationsstufe vor der Quantisierung“: ob es die 4-Bit-Modelle, die llmcf
baut, auf kleinen Boards besser macht, die Gewichte eines Modells vor der Quantisierung zu rotieren (QuaRot,
SpinQuant), für welche Formate und zu welchem Preis.

## Inhalt

1. [Die Verfahren](#1-die-verfahren)
2. [Welche Rotationen die Laufzeit brauchen und welche in die Gewichte eingehen](#2-welche-rotationen-die-laufzeit-brauchen-und-welche-in-die-gewichte-eingehen)
3. [Was die Formate können, die llmcf baut](#3-was-die-formate-können-die-llmcf-baut)
4. [Veröffentlichte Messungen](#4-veröffentlichte-messungen)
5. [Unsere Messung: GGUF mit Importance-Matrix, rotiert und nicht rotiert](#5-unsere-messung-gguf-mit-importance-matrix-rotiert-und-nicht-rotiert)
6. [Was das für llmcf bedeutet](#6-was-das-für-llmcf-bedeutet)
7. [Quellen](#7-quellen)

---

## 1. Die Verfahren

**Rechnerische Invarianz.** In einem Transformer mit RMSNorm lässt sich der Residualstrom mit einer orthogonalen
Matrix Q multiplizieren, ohne dass sich die Ausgabe des Modells ändert: Die Norm eines Vektors ändert sich unter Q
nicht, der Skalenvektor jeder RMSNorm wird zuerst in die Gewichte der linearen Schichten eingerechnet, die die Ausgabe
der Norm lesen, jedes Gewicht, das aus dem Residualstrom liest, wird von rechts mit Q multipliziert (W Q), jedes, das
hineinschreibt, von links mit dem transponierten Q (Qᵀ W), und die Token-Einbettung wird ebenfalls rotiert (E Q)
[1, Abschnitt 3; SliceGPT, auf dem QuaRot aufbaut]. In Gleitkomma rechnet das Modell dieselbe Funktion; nur die Zahlen
in den Gewichtsmatrizen ändern sich. Eine zufällige Hadamard-Matrix verteilt einen großen Wert eines Kanals auf alle
Kanäle, sodass aus einer Matrix mit Ausreißern eine mit gleichmäßig verteilten Werten wird, die ein gleichförmiges
Quantisierungsraster besser trifft [1].

**QuaRot** [1] (Ashkboos u. a., 2024; Code unter Apache-2.0 [2]) nutzt zufällige Hadamard-Rotationen ohne Training.
Sein Ziel sind 4-Bit-Gewichte *und* 4-Bit-Aktivierungen und -KV-Cache (W4A4KV4) auf GPUs mit INT4-Kernels; es nennt
höchstens 0,63 Perplexitätspunkte Verlust bei Llama 2 in W4A4KV4. Ein 70B-Modell wird auf einer GPU in etwa
5 Minuten umgebaut [1].

**SpinQuant** [3] (Liu u. a., 2024) lernt die Rotationen stattdessen: Es optimiert sie auf der Stiefel-Mannigfaltigkeit
(Cayley-SGD) über 800 WikiText-2-Beispiele in 100 Iterationen, mit dem quantisierten Netz in der Schleife. Das Paper
nennt etwa 13, 18 und 30 Minuten für Llama 3 1B, 3B und 8B und 3,5 Stunden für Llama 2 70B; der Code braucht PyTorch
mit CUDA und das Paket `fast-hadamard-transform` [4]. Das Repository steht unter **CC BY-NC 4.0** [5]
(nicht kommerziell), sein Code kann also nicht in llmcf übernommen werden, das unter MIT steht. Meta hat 4-Bit-Modelle
von Llama 3.2 1B und 3B mit SpinQuant für ExecuTorch veröffentlicht (gruppenweise 4-Bit-Gewichte mit dynamischen
8-Bit-Aktivierungen), nicht als GGUF [4].

## 2. Welche Rotationen die Laufzeit brauchen und welche in die Gewichte eingehen

Beide Papers nennen vier Rotationen [1] [3]:

| Rotation | Wo | Geht in die Gewichte ein? |
|---|---|---|
| R1 | der Residualstrom (Hidden Size) | Ja: Einbettung, die linearen Schichten, die aus dem Residualstrom lesen und hineinschreiben, der Ausgabekopf. Die RMSNorm-Skalen werden vorher in die lesenden Schichten eingerechnet und auf 1 gesetzt. |
| R2 | je Attention-Head, zwischen Value- und Output-Projektion | Ja: Zeilen von `W_v` und Spalten von `W_o` je Head. |
| R3 | Queries und Keys nach RoPE (für einen 4-Bit-KV-Cache) | Nein: eine Hadamard-Transformation zur Laufzeit. |
| R4 | die Eingabe der Down-Projektion (für 4-Bit-Aktivierungen) | Nein: eine Hadamard-Transformation zur Laufzeit (QuaRot wendet auch den kopfübergreifenden Teil von R2 zur Laufzeit an). |

Die Variante von SpinQuant ohne Laufzeit-Transformationen (SpinQuant_noHad) nutzt nur R1 und R2 [3]. **Für eine reine
Gewichtsquantisierung, und das sind die GGUF-k-Quants und IQ-Typen, nützen nur R1 und R2, und beide gehen in die
Gewichte ein: Die Laufzeit braucht keine Änderung.** Eine Eigenschaft kostet Speicher: Ein Modell, dessen Ausgabekopf
die Einbettungsmatrix mitbenutzt (gebundene Einbettungen, zum Beispiel Llama 3.2 1B und 3B, SmolLM2), braucht einen
eigenen Ausgabekopf, weil der Kopf die Skala der letzten RMSNorm aufnimmt und die Einbettung nicht **[Schluss, in
Abschnitt 5 geprüft: Die rotierte Datei ist größer]**.

## 3. Was die Formate können, die llmcf baut

**GGUF / llama.cpp.** Ein mit R1+R2 rotiertes Llama-Modell ist ein gewöhnliches Llama-Modell: llama.cpp lädt es
unverändert (Abschnitt 5 misst es in Gleitkomma gleich dem Original). llama.cpp kennt keine Hadamard-Transformation
der Eingaben der Gewichte zur Laufzeit; ein Werkzeug Dritter, das GGUF-Gewichte rotiert, braucht einen Fork von
llama.cpp, der die Transformation zur Laufzeit anwendet [6]. Seit PR #21038 (gemergt am 2026-04-01) rotiert
llama.cpp Queries, Keys und Values mit Block-Hadamard-Matrizen, wenn der KV-Cache quantisiert ist, ohne jede Änderung
an der Modelldatei, und schaltet das nur mit der Umgebungsvariable `LLAMA_ATTN_ROT_DISABLE` ab [7]; am Pin steht das
in `src/llama-kv-cache.cpp` (an, wenn der Cache-Typ quantisiert ist und die Head-Größe ein Vielfaches von 64). Der
Teil R3, der einzige, der für einen quantisierten KV-Cache zählt, erledigt also schon die Laufzeit, die llmcf
ausliefert, für jedes Modell. k-Quants haben eine eigene Skala je Block aus 16 oder 32 Gewichten innerhalb von
Superblöcken aus 256 [8], und mit Importance-Matrix gewichtet der Quantisierer den Fehler jeder Spalte danach, wie groß
die Aktivierungen dieser Spalte auf Kalibriertext sind [9].

**RKLLM (Rockchip-NPU).** Das RKLLM-Toolkit nimmt ein Hugging-Face-Modell und quantisiert es selbst (w4a16 mit GRQ,
w8a8, gruppenweise Varianten, GDQ seit 1.1.0 [10]). Sein Changelog bis 1.3.1 nennt keine Rotationsoption [10]. Ein
rotiertes Llama-Modell ist ein gewöhnliches Hugging-Face-Llama-Modell (mit `tie_word_embeddings: false`, wenn der Kopf
gelöst wurde), das Toolkit würde es also annehmen. Ob es dort hilft, lässt sich ohne das geschlossene Toolkit und ein
Board nicht prüfen: w8a8 quantisiert Aktivierungen, wo Rotationen im Prinzip helfen (der Hauptfall der Papers), aber
bei 8 Bit ist der Gewinn, den die Papers zeigen, klein, und RKLLMs eigene Verfahren und Kalibrierung decken ihn
vielleicht schon ab **[Unbestätigt]**.

**RKNN (Vision).** ONNX-Vision- und -Audiomodelle; das Verfahren ist für Transformer-Sprachmodelle mit RMSNorm und
greift dort nicht.

## 4. Veröffentlichte Messungen

Die Zahlen der Papers zur reinen Gewichtsquantisierung nutzen **eine Skala je Ausgabekanal** (keine Gruppen), den
Fall, in dem ein Ausreißer am meisten schadet:

| Llama 2 7B, WikiText-2-Perplexität | FP16 | RTN W4 | QuaRot-RTN W4 | GPTQ W4 | QuaRot-GPTQ W4 |
|---|---|---|---|---|---|
| QuaRot, Tabelle 7 [1] | 5,47 | 6,99 | 6,76 | 8,25 | 5,60 |

SpinQuant nennt für W4A16 auf Llama 2 7B mit RTN: zufälliges Hadamard 6,9, gelernt 5,5, gegenüber 5,5 in Gleitkomma
[3, Tabelle 4].

Diese Zahlen lassen sich nicht auf GGUF übertragen. Bei kleinen Gruppen hat jeder Block seine eigene Skala, ein
Ausreißer verdirbt also nur seinen eigenen Block: Die Analyse der Microscaling-FP4-Formate zeigt, dass eine kleine
Gruppengröße die herkömmlichen Mittel gegen Ausreißer „beweisbar neutralisiert“ [11], und eine Transformation, die
Werte innerhalb einer Gruppe einebnet, verfolgt ein anderes Ziel als ein Quantisierer, der Energie bündelt; welches
gewinnt, hängt vom Format ab [12]. Die einzige veröffentlichte GGUF-Messung, die wir fanden, rotiert die Gewichte von
Qwen3-4B und -8B und lässt sie mit einem veränderten llama.cpp laufen; bei Q4_K_M und Q3_K_M änderte sich die
Perplexität um +2,6 % und −2,7 % (und −5,4 % / +0,8 %), was der Autor als Rauschen wertet, ein beständiger Gewinn nur
bei Q2_K; eine Importance-Matrix wurde nicht benutzt [6]. Eine Messung rotierter Gewichte gegen k-Quants mit
Importance-Matrix, die llmcf unter 5 Bit je Gewicht standardmäßig baut, fanden wir nicht.

## 5. Unsere Messung: GGUF mit Importance-Matrix, rotiert und nicht rotiert

**Aufbau.** Das ARM64-Build-Image von llmcf (llama.cpp am Pin b11429; `llama-quantize`, `llama-imatrix`,
`llama-perplexity` für den Build-Rechner gebaut, x86-64 mit AVX2, 4 Threads). Modelle: SmolLM2-360M
(Llama-Architektur, Hidden Size 960, gebundene Einbettungen, Apache-2.0) und Llama 3.2 1B (Hidden Size 2048,
gebundene Einbettungen). Jedes mit `convert_hf_to_gguf.py` des Images nach F16 umgewandelt. Rotation: unsere eigene
Umsetzung von R1+R2 auf dem F16-GGUF mit numpy und gguf-py (RMSNorm-Skalen eingerechnet, Einbettung und Kopf rotiert,
Kopf gelöst, R2 je Head mit einer normierten Hadamard-Matrix der Head-Größe; R1 eine Hadamard-Matrix mit zufälligen
Vorzeichen bei einer Hidden Size, die eine Zweierpotenz ist, sonst eine zufällige orthogonale Matrix aus einer
QR-Zerlegung, Seed 0). Importance-Matrix: `llama-imatrix` über 100 Abschnitte des mitgelieferten Kalibriertexts von
llmcf (die gemischte Menge aus `data/calibration`, wie ein Build sie nimmt), getrennt auf dem Original und auf dem
rotierten Modell berechnet. Quantisierung: `llama-quantize --imatrix`. Bewertung: `llama-perplexity` auf dem
Testteil von WikiText-2 (SmolLM2: 80 Abschnitte zu 512 Tokens, Llama 3.2 1B: 20 Abschnitte), die KL-Divergenz der
Token-Verteilung jedes quantisierten Modells gegen die des ursprünglichen F16-Modells (`--kl-divergence-base`).
Kleiner ist besser; es ist das Maß, das die Quantisierungsarbeit von llama.cpp selbst neben der Perplexität nutzt
[7] [9].

**Invarianz.** Das rotierte F16-Modell gibt dieselbe Verteilung wie das Original: SmolLM2-360M mittlere KLD
0,000005, gleiches wahrscheinlichstes Token in 99,88 % (der Rest ist F16-Rundung der rotierten Gewichte);
Llama 3.2 1B: mittlere KLD 0,000002, gleiches wahrscheinlichstes Token in 99,92 %, Perplexität 11,105 gegenüber
11,105.

**Ergebnisse.**

KL-Divergenz zum F16-Modell (kleiner ist besser), Perplexität in Klammern, Dateigröße in MB (10⁶ Bytes). „Rotiert“
gibt der Token-Einbettung denselben Typ, den die gebundene Einbettung des Originals bekommt (Q6_K bei Llama 3.2 1B,
Q8_0 bei SmolLM2), verglichen wird also die Rotation allein; „rotiert, Standard“ ist, was `llama-quantize` ohne diese
Option aus der rotierten Datei macht (die gelöste Einbettung bekommt dann den Typ der übrigen Schichten).

| Modell (F16-Perplexität) | Typ, mit Importance-Matrix | Original | Rotiert | Rotiert, Standard | Größe Original → rotiert |
|---|---|---|---|---|---|
| Llama 3.2 1B (11,105) | Q4_K_M | **0,0407** (11,58) | 0,0628 (11,85) | 0,0643 (11,85) | 808 → 1023 |
| Llama 3.2 1B | IQ4_XS | **0,0498** (11,67) | 0,0715 (11,95) | | 743 → 959 |
| Llama 3.2 1B | Q4_0 | **0,0730** (11,93) | 0,1098 (12,35) | 0,1131 (12,40) | 773 → 988 |
| SmolLM2-360M (12,59) | Q4_K_M ¹ | **0,0225** (12,87) | 0,0450 (13,15) | 0,0455 (13,16) | 271 → 321 |
| SmolLM2-360M | IQ4_XS ¹ | **0,0546** (13,29) | 0,2090 (15,58) | 0,2112 (15,62) | 227 → 277 |
| SmolLM2-360M | Q4_0 | **0,0885** (13,75) | 0,2408 (16,04) | | 230 → 280 |
| SmolLM2-360M | Q4_0 ohne Importance-Matrix | 0,1395 (14,49) | 0,3236 (17,35) | | 229 → 279 |

¹ Die Hidden Size 960 von SmolLM2 ist kein Vielfaches von 256, deshalb speichert `llama-quantize` die meisten seiner
Matrizen als Q5_0 oder IQ4_NL statt als K- und XS-Typen; Llama 3.2 1B hat keinen dieser Ausweichtypen.

In jedem Paar ist das rotierte Modell deutlich schlechter, weit jenseits des Messfehlers (der Standardfehler jeder
mittleren KLD ist höchstens 0,0028), und es ist bei Llama 3.2 1B etwa 27 % größer und bei SmolLM2 18 bis 22 %, weil
der gebundene Ausgabekopf zu einer eigenen Matrix wird.

**Warum [Schluss, aus den Zahlen].** Die Importance-Matrix sagt dem Quantisierer, dass wenige Spalten jeder
Gewichtsmatrix auf sehr große Aktivierungen treffen und genau bleiben müssen. Der größte Wichtigkeitswert einer
Eingabespalte der Attention ist bei Llama 3.2 1B etwa 42-mal so groß wie der Mittelwert und bei SmolLM2-360M etwa
330-mal (Median über die Schichten; 23- und 93-mal bei den Eingaben der FFN). Nach R1 ist er 3,4- und 7-mal so groß:
Die Rotation verteilt die Ausreißer der Aktivierungen auf alle Kanäle, genau dafür ist sie da, und nimmt damit der
Importance-Matrix ihren Hebel (die Eingabe der Down-Projektion, die R1 nicht berührt, behält ihr 342- und 34-faches).
Die Ausreißer der Gewichte, die die Rotation beseitigt, hielt die Skala, die jeder Block aus 16 oder 32 Gewichten
hat, schon in Schach. Der verlorene Hebel der Importance-Matrix erklärt nicht alles: Bei SmolLM2 ist das rotierte
Q4_0 auch ohne Importance-Matrix schlechter (0,324 gegenüber 0,139). Was sonst wirkt, haben wir nicht gemessen.

## 6. Was das für llmcf bedeutet

- **GGUF (das Format jedes CPU-Builds, ARM64 und Rockchip):** Eine offline eingerechnete R1+R2-Rotation braucht keine
  Änderung der Laufzeit und ist billig zu rechnen (numpy, Minuten auf einer CPU), aber gegen den Standard von llmcf,
  k-Quants und IQ-Typen mit Importance-Matrix, hat sie die 4-Bit-Modelle in unserer Messung verschlechtert (das
  1,4- bis 3,8-fache der KL-Divergenz zum F16-Modell; bei SmolLM2 auch ohne Importance-Matrix) und größer gemacht,
  wenn das Modell seine Einbettungen bindet. Sie bringt keinen echten Gewinn, deshalb baut llmcf sie
  für GGUF nicht. Die Rotation, die GGUF bei der Inferenz hilft, die Hadamard-Rotation eines quantisierten KV-Caches,
  wendet llama.cpp am Pin schon an, für jedes Modell.
- **SpinQuant:** braucht eine Trainingsschleife auf der GPU, und sein Code ist nicht kommerziell; selbst wenn die
  gelernte Rotation der Quantisierung je Kanal hilft, gibt das Ergebnis bei den k-Quants keinen Grund, einen Gewinn
  gegenüber k-Quants mit Importance-Matrix zu erwarten.
- **RKLLM w8a8:** das eine Format, bei dem eine Rotation plausibel helfen könnte (Quantisierung der Aktivierungen),
  aber das lässt sich nur mit dem Toolkit von Rockchip und einem Board beurteilen; bis eine Messung dort einen Gewinn
  zeigt, bleibt es ein geplanter, eingegrenzter Punkt.
- **Hier nicht prüfbar:** größere Llama-Modelle (7B und mehr), andere Modellfamilien und Quantisierung unter 4 Bit
  (Q2_K, IQ2/IQ3), wo die Messung Dritter ohne Importance-Matrix einen Gewinn sah [6].

## 7. Quellen

1. Ashkboos u. a., „QuaRot: Outlier-Free 4-Bit Inference in Rotated LLMs“, arXiv:2404.00456, https://arxiv.org/abs/2404.00456 (Tabelle 7: reine Gewichtsquantisierung)
2. https://github.com/spcl/QuaRot (LICENSE: Apache-2.0)
3. Liu u. a., „SpinQuant: LLM quantization with learned rotations“, arXiv:2405.16406, https://arxiv.org/abs/2405.16406 (Tabelle 4, Tabelle 17, Optimierungszeiten)
4. https://github.com/facebookresearch/SpinQuant (README: Voraussetzungen, ExecuTorch-Export, veröffentlichte Llama-3.2-Modelle)
5. https://github.com/facebookresearch/SpinQuant/blob/main/LICENSE (CC BY-NC 4.0)
6. https://github.com/christopherrobertbrooks-tech/volta-hadamard (README: Rotation von GGUF-Gewichten mit einem Fork von llama.cpp; Messungen an Qwen3)
7. https://github.com/ggml-org/llama.cpp/pull/21038 („llama : rotate activations for better quantization“, gemergt am 2026-04-01)
8. https://github.com/ggml-org/llama.cpp/pull/1684 (k-Quants)
9. https://github.com/ggml-org/llama.cpp/blob/master/tools/quantize/README.md (Importance-Matrix, Perplexität und KLD)
10. https://github.com/airockchip/rknn-llm/blob/main/CHANGELOG.md (RKLLM 1.0.0 bis 1.3.1)
11. Egiazarian u. a., „Bridging the Gap Between Promise and Performance for Microscaling FP4 Quantization“, arXiv:2509.23202, https://arxiv.org/abs/2509.23202
12. Jokar, „Transforms for LLM Quantization: The Great Inversion and Format Co-Design“, arXiv:2608.25188, https://arxiv.org/abs/2608.25188
13. Die Modellkarten von SmolLM2-360M und Llama 3.2 1B auf Hugging Face (Architektur, Lizenz); https://huggingface.co/datasets/ggml-org/ci (WikiText-2 roh, die Datei, die die Skripte von llama.cpp holen)
