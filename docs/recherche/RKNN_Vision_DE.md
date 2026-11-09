Version 3.4.0 · Stand 2026-11-09

# RKNN-Vision-Recherche

[English version](RKNN_Vision.md)

Recherchedatum: 2026-11-09. Alle Quellen wurden an diesem Tag abgerufen. Sie stehen am Ende und werden als [n]
zitiert. Fakten stammen aus der zitierten Quelle oder aus dem Lesen des Codes des zitierten Repositorys beim genannten
Tag. Text mit **[Schluss]** ist unsere eigene Folgerung. Text mit **[Unbestätigt]** ließ sich nicht an einer
Primärquelle prüfen.

Dies ist die Grundlage für die Vision-Engine `llmcf-detect` des Boards (ROADMAP-Zeile „Vision auf der NPU“): woher
Rockchips NPU-Runtime kommt, welche Vision-Modelle und Arten der Nachverarbeitung es gibt und wie Kameras auf
Rockchip-Boards Bilder liefern.

## Inhalt

1. [Die NPU-Runtime librknnrt.so](#1-die-npu-runtime-librknnrtso)
2. [Vision-Modelle und ihre Nachverarbeitung](#2-vision-modelle-und-ihre-nachverarbeitung)
3. [Kameras auf Rockchip-Boards](#3-kameras-auf-rockchip-boards)
4. [Was das für llmcf bedeutet](#4-was-das-für-llmcf-bedeutet)
5. [Quellen](#5-quellen)

---

## 1. Die NPU-Runtime librknnrt.so

### 1.1 Woher sie kommt

| Quelle | Pfad | Version | Hinweise |
|---|---|---|---|
| rknn-toolkit2 (maßgeblich) [1] | `rknpu2/runtime/Linux/librknn_api/aarch64/librknnrt.so` | 2.5.0 (Tag vom 2026-10-13, Commit `2c723177a94fac65cbc036d722907ddbc91980cf`) | Eine Runtime für RK3562, RK3566, RK3568, RK3576, RK3588 und RV1126B; `librknnmrt.so` (Mini-Runtime) für RV1103 und RV1106 unter `armhf-uclibc/` [2] |
| rknn_model_zoo [3] | `3rdparty/rknpu2/Linux/aarch64/librknnrt.so` | 2.5.0 | Byte für Byte gleich mit der Datei des Toolkits |
| rknn-llm [4] | nur als Demo-Abhängigkeit unter `examples/multimodal_model_demo/` | 2.3.2 | Seine eigene Runtime ist `librkllmrt.so`; RKLLM unterstützt RK3566 und RK3568 nicht |
| rknpu2 (alt) [5] | `runtime/<SoC>/Linux/librknn_api/...` | 1.5.2 (letzte, 2023) | Veraltet; in rknn-toolkit2 aufgegangen |
| Radxa apt [6] | Pakete `rknpu2-rk356x`, `rknpu2-rk3588` → `/usr/lib/aarch64-linux-gnu/librknnrt.so.2.3.0`, `/usr/lib/librknnrt.so`, `rknn_server`, `rknn_api.h` | 2.3.0 (bookworm) | Signiertes apt-Repository; hinter upstream zurück |

SHA256 der Dateien von 2.5.0 (aus den heruntergeladenen Dateien gelesen):

| Datei | SHA256 |
|---|---|
| `aarch64/librknnrt.so` | `4b6cc575bedf8952b13db4b1190fa3b3d2ac213f00be774ff6ccd19d651970c0` |
| `armhf/librknnrt.so` | `267193f5f32f01f6e9137072c67ca737d57528257f1a63ce4d65e3698369ffa3` |
| `armhf-uclibc/librknnmrt.so` | `632d0e505ca9c9d3da7037107a7cbc6a593b9b36007cdce600377c529484947e` |
| `aarch64/librknnmrt.so` (RV1126B, RK3562) | `6283d5b93e05897f5f9a6ec3dcc990a86fafab6fa230f1d51c717fdb77ac00c6` |
| `aarch64/librknnrt.so` 2.3.2 | `d31fc19c85b85f6091b2bd0f6af9d962d5264a4e410bfb536402ec92bac738e8` |

Die GitHub-Releases des Toolkits enthalten nur Quellarchive; es gibt keine eigenen Binärdateien, Prüfsummen oder
Signaturen für die `.so`-Dateien, und die Tags sind leichtgewichtig (unsigniert). Ein Download ist daher eine rohe
Datei, auf einen Commit festgelegt und gegen eine SHA256 geprüft, die der Installer mitbringt [1]. Radxas Paket geht
durch dpkg und ist gestrippt, daher weicht sein Hash von upstream ab [6].

### 1.2 Lizenz

rknn-toolkit2 2.5.0 steht unter der „RKNN SDK License“ (Recht der VR China, Click-Wrap): Nutzung, Vervielfältigung und
Änderung für Anwendungen, die mit Rockchip-Produkten kompatibel sind; kein Reverse Engineering; kein ausdrückliches
Recht, die unveränderte Binärdatei weiterzugeben [7]. Releases bis 2.3.2 trugen nur einen Copyright-Vermerk [8].
**[Schluss]** Ein Open-Source-Projekt sollte die Datei nicht mitliefern; sie wird auf dem Rechner der Person aus
Rockchips Repository geholt, mit angezeigter Lizenz, oder aus dem Paket des Board-Herstellers genommen.

### 1.3 Versionen

- Die Runtime muss mindestens die Version des Toolkits haben, das das Modell konvertiert hat; Modelle von 2.x laden auf
  einer neueren 2.x-Runtime [2].
- Modelle für RK3566 und RK3568 sind austauschbar; Modelle für RK3588 nicht [2].
- Kernel-Treiber (rknpu): 0.9.2 oder neuer laut Entwicklerhandbuch, 0.9.8 oder neuer empfohlen vom Quick Start von
  2.5.0 [2]; Images für RK356x bringen eventuell noch 0.8.8 mit [9].
- Zur Laufzeit gelesen mit `rknn_query(RKNN_QUERY_SDK_VERSION)` (API- und Treiberversion); auf dem Board außerdem
  `/sys/kernel/debug/rknpu/version` (root), `/proc/rknpu/version` (RV110x) oder `dmesg` [2].
- Auf dem Radxa Zero 3W wird die NPU zuerst mit `rsetup` → Overlays → „Enable NPU“ und einem Neustart eingeschaltet
  [9].
- Die aarch64-Runtime braucht glibc 2.17 und libstdc++ (GLIBCXX 3.4.22); einen musl-Build gibt es nicht [aus der Datei
  gelesen].

## 2. Vision-Modelle und ihre Nachverarbeitung

### 2.1 Rockchips Model Zoo (v2.5.0) [3]

Vorverarbeitung in den Demos: Letterbox auf die Modellgröße mit Füllwert 114, RGB; Mittelwert und Standardabweichung
werden bei der Konvertierung ins Modell eingebacken, die Runtime bekommt also uint8-RGB. Die C-Demos lesen Bilddateien;
keine nutzt eine Kamera.

| Modell | Aufgabe | Ausgaben und Nachverarbeitung |
|---|---|---|
| YOLOv5, YOLOv7 | Detektion | 3 Köpfe `[1, 3×(5+C), H, W]`, Anker je Stride, Sigmoid im Modell; `xy=(v·2−0.5+grid)·stride`, `wh=(v·2)²·anchor`; Score = obj × cls; NMS je Klasse |
| YOLOX | Detektion | 3 Köpfe, ankerfrei, `wh=exp(v)·stride`, obj × cls, NMS |
| YOLOv6, YOLOv8, YOLO11, PP-YOLOE | Detektion | 3 Zweige: Box `[1, 4·reg, H, W]` (DFL, reg 16; 1 = direkte Abstände), Score `[1, C, H, W]`, optionale Score-Summe; NMS |
| YOLOv10 | Detektion | wie v8, dann Top-K ohne NMS |
| YOLO26 | Detektion | Boxen `[1, 4, 8400]` xyxy und Scores `[1, 80, 8400]`, an der Form erkannt; Top-K ohne NMS |
| YOLO-World v2s | Detektion mit offenem Vokabular | ein CLIP-Textmodell liefert Embeddings als zweite Eingabe des Detektors; Ausgabe wie bei v8 mit C = Prompts |
| YOLOv8-OBB | gedrehte Boxen | Winkel-Tensor, `θ=(a−0.25)·π`, probIoU-NMS |
| YOLOv8-pose | Pose, 17 Keypoints | DFL-Boxen plus Keypoints `[1, 17, 3, 8400]` |
| YOLOv5-seg, YOLOv8-seg | Instanzsegmentierung | Detektion plus 32 Maskenkoeffizienten; Prototypen `[1, 32, 160, 160]`; Koeffizienten × Prototypen, Zuschnitt, Schwellwert |
| DeepLabV3, PP-LiteSeg | semantische Segmentierung | Klassenkarte, Hochskalieren, argmax |
| MobileSAM | Segmentierung per Prompt | Encoder plus Prompt-Decoder |
| RetinaFace | Gesichter mit 5 Landmarken | Prior-Boxen, Varianz {0.1, 0.2}, NMS |
| MobileNetV2, ResNet50 | Klassifikation | Softmax, Top-K |
| LPRNet | Kennzeichentext | CTC über 18 Schritte × 68 Klassen |
| PP-OCR det / rec | Texterkennung: Lage / Inhalt | DB-Karte (Schwellwert, Konturen, Unclip) / CTC mit Wörterbuch |
| CLIP ViT-B/32 | Bild-Text-Abgleich | Embeddings, Kosinus × Logit-Skala, Softmax |

Plattformen: volle Unterstützung auf RK3562, RK3566, RK3568, RK3576, RK3588, RV1126B; RV1103 und RV1106 eingeschränkt
(nur INT8, native Ausgabe in NHWC; Portierungen von mobilenet, YOLOv5 bis v8, v10, 11, 26, YOLOX, LPRNet) [3]. Im
YOLOv8-Export von airockchip ist das Sigmoid Teil des Modells; die Pose- und OBB-Demos wenden es auf dem Host an [3].
Radxas NPU-Seiten führen mit Bilddateien durch diese Demos; eine Kamera-Demo wurde nicht gefunden [10]
**[Unbestätigt: Suchausschnitte und eine Seite]**.

### 2.2 Hailos Beispiele für den Raspberry Pi 5 [11] [12]

hailo-rpi5-examples verweist jetzt auf hailo-apps. Dessen GStreamer-Pipelines: Detektion (YOLOv5 bis v11, YOLO26, mit
einem Tracker), Pose (YOLOv8-pose), Instanzsegmentierung (YOLOv5/v8-seg, FastSAM), Tiefe (SCDepthV3), Kacheln für
kleine Objekte, CLIP auf erkannten Ausschnitten, mehrere Streams gleichzeitig, Gesichtserkennung (SCRFD + ArcFace), OCR
(PaddleOCR), Re-ID. Eigenständige Apps ergänzen gedrehte Boxen, Spurerkennung, Super-Resolution, Klassifikation,
Stereo-Tiefe, semantische Segmentierung und Zero-Shot-Klassifikation. Detektionsmodelle tragen NMS im kompilierten
Modell; Pose, Segmentierung, Tiefe, Gesichter, CLIP und OCR dekodieren auf dem Host. Eingänge: Raspberry-Pi-Kamera
(Picamera2), libcamera, USB, RTSP, UDP, Dateien, Bilder. Jede Pipeline zeigt Video mit eingezeichneten Ergebnissen; die
Ergebnisse erreichen einen Callback; eine eingebaute JSON-Ausgabe gibt es nicht. Eine `config.json` je Modell
(Ausgabenamen, Eingabegröße, Klassen, Schwellwerte) kommt einer Modellkarte nahe. Kennzeichenerkennung liegt in TAPPAS
[13] **[Unbestätigt]**.

### 2.3 Nachverarbeitungsarten, die sie alle abdecken [Schluss]

| Art | Deckt ab |
|---|---|
| `yolo-anchor` | YOLOv5, v7, tiny-YOLOv4 |
| `yolo-anchorfree-obj` | YOLOX |
| `yolo-dfl` | YOLOv6, v8, v9, 11, PP-YOLOE, YOLO-World |
| `yolo-nms-free` | YOLOv10, YOLO26 |
| `device-nms` | Modelle mit einkompiliertem NMS |
| `ssd-prior` | SSD, RetinaFace, SCRFD |
| `yolo-seg-protos` | YOLOv5/v8-seg, FastSAM |
| `yolo-pose` | YOLOv8-pose, YOLO26-pose |
| `yolo-obb` | YOLOv8/11-OBB |
| `classification` | MobileNet, ResNet, FastViT |
| `semantic-seg` | DeepLabV3, PP-LiteSeg, FCN, SegFormer, STDC |
| `embedding` | ArcFace, CLIP, Re-ID |
| `depth-map` | SCDepth, StereoNet |
| `ctc-text` | LPRNet, PP-OCR rec |
| `db-text-det` | PP-OCR det |

Später: zweistufige Verkettung (Detektor → Ausschnitt → zweites Modell), Segmentierung per Prompt, Super-Resolution,
Spuren. Eine Modellkarte nennt die Eingabe (Größe, Layout, Farbreihenfolge, Skalierung, Füllwert, Mittelwert und
Standardabweichung, ob die Normalisierung im Modell steckt), die Quantisierung, Labels, Schwellwerte, die
Score-Aktivierung (im Modell oder auf dem Host) und die Rolle jeder Ausgabe; die meisten Rollen lassen sich an der Form
des Tensors erkennen, wie es der YOLO26- und v8-Code des Zoos tut [3].

## 3. Kameras auf Rockchip-Boards

Auf den Hersteller-Kerneln von Rockchip wird jede unterstützte CSI-Kamera zu einem V4L2-Multiplanar-NV12-Knoten des ISP
(`rkisp`), und sie liefert brauchbare Bilder nur mit Rockchips 3A-Dienst (`rkaiq_3A_server`) und einer Tuning-Datei
(IQ) für Sensor und Modul unter `/etc/iqfiles/` [14] [15]. libcamera funktioniert auf Hersteller-Kerneln nicht;
mainline hat noch keinen ISP für RK356x [16] [17].

| Kamera | Unterstützung durch Rockchip | Wie |
|---|---|---|
| Raspberry Pi Camera v1.3 (OV5647) und ihre Klone (Waveshare, Arducam 5MP, SunFounder, OKdo) | offiziell auf Zero 3W/3E, ROCK 3, 4, 5, CM3/4/5 | Radxa-Overlays wie `radxa-zero3-rpi-camera-v1.3` [18] |
| Raspberry Pi Camera v2 (IMX219), Radxa Camera 8M | offiziell auf denselben Boards | `radxa-zero3-rpi-camera-v2`, `radxa-zero3-radxa-camera-8m` [18] |
| Radxa Camera 4K (IMX415), Waveshare IMX415 | ROCK 4D, 5er-Serie, CM4/CM5 (nicht Zero 3W, nicht ROCK 3) | Radxa-Overlays [18] |
| Radxa 13M (IMX214), 12M (IMX577), 2M (AR0234), OV9281 | verschiedene ROCK-Boards | Radxa-Overlays [18] |
| Raspberry Pi Camera 3 (IMX708), HQ (IMX477), Global Shutter (IMX296), Arducam IMX519, IMX290/327/462 | kein Treiber im Hersteller-Kernel [19]; Community-Portierungen, Arducams Installationsskript für ältere Radxa-Images [20] [21] | Treiber, Overlay und IQ-Datei nach der Anleitung des Herstellers |
| Raspberry Pi AI Camera (IMX500) | braucht Raspberry Pis Firmware und Kamera-Stack [22] | so nicht portierbar **[Schluss]** |
| USB-Kameras (UVC) | jedes Board | `uvcvideo`; MJPEG für 720p und mehr an USB 2.0, YUYV für kleine Größen |
| RTSP-Netzwerkkameras | jedes Board | H.264/H.265, dekodiert von Rockchips MPP |

Auf dem Radxa Zero 3W wird der 22-polige CSI-Anschluss mit zwei Lanes genutzt; die Aufnahme funktioniert auf dem
Hersteller-Kernel 5.10.160-26, spätere Builds haben sie bei manchen Nutzern kaputt gemacht [23]. „Soundforge“ ergab
keinen Kamerahersteller; das Modul von SunFounder ist eine OV5647. PiSugar verkauft keine Kamera; Seeed verkauft die
Standardsensoren weiter.

Aufnahmewege [14] [24]: den Knoten am Namen in `/sys/class/video4linux/*/name` finden (`rkisp_mainpath`,
`rkisp_selfpath` oder der Treiber `uvcvideo`), nie an der Nummer. CSI: Multiplanar-NV12, DMA-BUF-Puffer, dann Rockchips
RGA für Skalierung, Farbumwandlung und Letterbox. USB: MJPEG, dekodiert von MPP (`librockchip_mpp`), libjpeg-turbo als
Rückfall. RTSP und Dateien: Dekodierung durch MPP; die Hersteller-Demos nutzen GStreamer (`mppvideodec`) oder FFmpeg
mit rkmpp [25].

Die Raspberry Pi AI Camera (IMX500) braucht den eigenen Stack von Raspberry Pi: Der Kernel-Treiber richtet das Modul
über I2C ein, die Netzwerk-Firmware des Sensors wird bei jedem Start heruntergeladen (das Paket `imx500-all` auf
Raspberry Pi OS), und ein RP2040 auf dem Modul wird von einem eigenen SPI-Treiber von Raspberry Pi angesteuert
([Raspberry Pi: AI Camera](https://raspberrypi.com/documentation/accessories/ai-camera.html)). Auch das libcamera von
Ubuntu deckt sie nicht ab ([Ubuntu: Raspberry-Pi-Kameras](https://documentation.ubuntu.com/hardware-support/boards/how-to/special_hardware/rpi-camera/)),
und einen Treiber oder ein Overlay für Rockchip oder Radxa gibt es nicht. Auf Rockchip-Boards bräuchte es eine
Portierung des imx500-Treibers, des Firmware-Downloads und des RP2040-SPI-Treibers auf den Herstellerkernel
**[Schluss]**.

## 4. Was das für llmcf bedeutet

[Schluss] Die Entscheidungen, die aus dieser Recherche folgen, stehen in DESIGN.md (Baustein „Auf dem Board“, und
das Entscheidungsprotokoll ab [seinem Eintrag vom 2026-11-09](../DESIGN_DE.md#decision-round-66)):

- `librknnrt.so` wird nie mitgeliefert: zuerst die eigene Kopie des Boards, sonst ein Download, festgelegt auf einen
  Commit und seine SHA256, mit der Zustimmung der Person und angezeigter Lizenz von Rockchip; die Runtime wird gegen die
  Toolkit-Version des Modells geprüft.
- Die Engine ist Rust für aarch64 mit glibc und lädt die Runtime beim Start über ihren absoluten Pfad.
- Jede Modellvariante über eine Modellkarte und die Nachverarbeitungsarten aus 2.3.
- Jede Kamera, die ein Raspberry-Pi- oder Radxa-Board nutzen kann, dazu RTSP, Dateien und Bilder; der Board-Installer
  richtet Treiber, Overlays (`rsetup`), den 3A-Dienst und die IQ-Datei mit der Freigabe der Person ein, nach der
  offiziellen Anleitung des Kameraherstellers, und startet das Board neu.
- V4L2, MPP und RGA direkt; GStreamer nur als Rückfall, wo er nötig ist.
- Die Ausgaben folgen dem, was das Modell kann: Boxen, Masken, Keypoints, Text oder Embeddings als JSON und Ereignisse,
  und Video mit eingezeichneten Ergebnissen.

## 5. Quellen

1. https://github.com/airockchip/rknn-toolkit2/tree/v2.5.0/rknpu2/runtime/Linux/librknn_api
2. https://github.com/airockchip/rknn-toolkit2/tree/v2.5.0/doc/EN (QuickStart and Development Guide 2.5.0)
3. https://github.com/airockchip/rknn_model_zoo (tag v2.5.0, `examples/*/cpp/postprocess.cc`)
4. https://github.com/airockchip/rknn-llm
5. https://github.com/rockchip-linux/rknpu2
6. https://github.com/radxa-pkg/rknn2 and https://radxa-repo.github.io/bookworm/
7. https://github.com/airockchip/rknn-toolkit2/blob/v2.5.0/LICENSE
8. https://github.com/airockchip/rknn-toolkit2/blob/v2.3.2/LICENSE
9. https://docs.radxa.com/en/zero/zero3/app-development/ai/rknn-install
10. https://docs.radxa.com/en/som/cm/cm4/app-dev/npu/rknn-model-zoo
11. https://github.com/hailo-ai/hailo-apps
12. https://github.com/hailo-ai/hailo-rpi5-examples
13. https://hailo.ai/blog/automatic-license-plate-recognition-with-hailo-8/
14. https://docs.radxa.com/en/zero/zero3/accessories/camera
15. https://forum.radxa.com/t/how-to-use-radxa-camera-4k/13031/2
16. https://raw.githubusercontent.com/torvalds/linux/master/drivers/media/platform/rockchip/rkisp1/rkisp1-dev.c
17. https://collabora.com/news-and-blog/news-and-events/mainline-video-capture-and-camera-support-for-rockchip-rk3588.html
18. https://github.com/radxa-pkg/radxa-overlays
19. https://github.com/rockchip-linux/kernel/tree/develop-6.1/drivers/media/i2c
20. https://forum.radxa.com/t/imx290-imx519-imx708-imx477-on-zero-3w/31353
21. https://arducam-doc.pages.dev/ROCK-PI-Camera-Module/Quick-Start-Guide
22. https://www.raspberrypi.com/documentation/accessories/camera-software.html
23. https://forum.radxa.com/t/zero-3w-camera-8m-doesn-t-work/22617
24. https://docs.radxa.com/en/rock5/rock5b/getting-started/interface-usage/mipi-csi
25. https://github.com/avafinger/ff-rknn
