Version 3.4.0 · Last updated 2026-11-09

# RKNN vision research

[Deutsche Fassung](RKNN_Vision_DE.md)

Research date: 2026-11-09. All sources were accessed on that date. They are listed at the end and cited as [n].
Facts come from the cited source or from reading the cited repository's code at the stated tag. Text marked
**[Inference]** is our own conclusion. Text marked **[Unconfirmed]** could not be checked against a primary source.

This is the groundwork for the board's vision engine `llmcf-detect` (ROADMAP row "Vision on the NPU"): where
Rockchip's NPU runtime comes from, which vision models and post-processing kinds exist, and how cameras deliver frames
on Rockchip boards.

## Contents

1. [The NPU runtime librknnrt.so](#1-the-npu-runtime-librknnrtso)
2. [Vision models and their post-processing](#2-vision-models-and-their-post-processing)
3. [Cameras on Rockchip boards](#3-cameras-on-rockchip-boards)
4. [What this means for llmcf](#4-what-this-means-for-llmcf)
5. [Sources](#5-sources)

---

## 1. The NPU runtime librknnrt.so

### 1.1 Where it comes from

| Source | Path | Version | Notes |
|---|---|---|---|
| rknn-toolkit2 (canonical) [1] | `rknpu2/runtime/Linux/librknn_api/aarch64/librknnrt.so` | 2.5.0 (tag of 2026-10-13, commit `2c723177a94fac65cbc036d722907ddbc91980cf`) | One runtime for RK3562, RK3566, RK3568, RK3576, RK3588 and RV1126B; `librknnmrt.so` (mini runtime) for RV1103 and RV1106 under `armhf-uclibc/` [2] |
| rknn_model_zoo [3] | `3rdparty/rknpu2/Linux/aarch64/librknnrt.so` | 2.5.0 | Byte-identical to the toolkit's file |
| rknn-llm [4] | only as a demo dependency under `examples/multimodal_model_demo/` | 2.3.2 | Its own runtime is `librkllmrt.so`; RKLLM does not support RK3566 or RK3568 |
| rknpu2 (old) [5] | `runtime/<SoC>/Linux/librknn_api/...` | 1.5.2 (last, 2023) | Deprecated; moved into rknn-toolkit2 |
| Radxa apt [6] | packages `rknpu2-rk356x`, `rknpu2-rk3588` → `/usr/lib/aarch64-linux-gnu/librknnrt.so.2.3.0`, `/usr/lib/librknnrt.so`, `rknn_server`, `rknn_api.h` | 2.3.0 (bookworm) | Signed apt repository; behind upstream |

SHA256 of the 2.5.0 files (read from the downloaded files):

| File | SHA256 |
|---|---|
| `aarch64/librknnrt.so` | `4b6cc575bedf8952b13db4b1190fa3b3d2ac213f00be774ff6ccd19d651970c0` |
| `armhf/librknnrt.so` | `267193f5f32f01f6e9137072c67ca737d57528257f1a63ce4d65e3698369ffa3` |
| `armhf-uclibc/librknnmrt.so` | `632d0e505ca9c9d3da7037107a7cbc6a593b9b36007cdce600377c529484947e` |
| `aarch64/librknnmrt.so` (RV1126B, RK3562) | `6283d5b93e05897f5f9a6ec3dcc990a86fafab6fa230f1d51c717fdb77ac00c6` |
| `aarch64/librknnrt.so` 2.3.2 | `d31fc19c85b85f6091b2bd0f6af9d962d5264a4e410bfb536402ec92bac738e8` |

The toolkit's GitHub releases carry only source archives; there are no separate binaries, checksums or signatures
for the `.so` files, and the tags are lightweight (unsigned). A download is therefore a raw file pinned to a commit and
checked against a SHA256 the installer carries [1]. Radxa's package passes through dpkg and is stripped, so its hash
differs from upstream [6].

### 1.2 Licence

rknn-toolkit2 2.5.0 is under the "RKNN SDK License" (PRC law, click-wrap): use, reproduction and modification for
applications compatible with Rockchip products; no reverse engineering; no explicit right to redistribute the
unmodified binary [7]. Releases up to 2.3.2 carried only a copyright statement [8]. **[Inference]** An open-source
project should not ship the file; it is fetched on the person's machine from Rockchip's repository with the licence
shown, or taken from the board vendor's package.

### 1.3 Versions

- The runtime must be at least the toolkit version that converted the model; models of 2.x load on a newer 2.x runtime
  [2].
- Models for RK3566 and RK3568 are interchangeable; RK3588 models are not [2].
- Kernel driver (rknpu): 0.9.2 or later in the development guide, 0.9.8 or later recommended by the 2.5.0 quick start
  [2]; RK356x images may still ship 0.8.8 [9].
- Read at run time with `rknn_query(RKNN_QUERY_SDK_VERSION)` (API and driver version); on the board also
  `/sys/kernel/debug/rknpu/version` (root), `/proc/rknpu/version` (RV110x) or `dmesg` [2].
- On the Radxa Zero 3W the NPU is first enabled with `rsetup` → Overlays → "Enable NPU" and a reboot [9].
- The aarch64 runtime needs glibc 2.17 and libstdc++ (GLIBCXX 3.4.22); there is no musl build [read from the file].

## 2. Vision models and their post-processing

### 2.1 Rockchip's model zoo (v2.5.0) [3]

Pre-processing in the demos: letterbox to the model size with pad value 114, RGB; mean and std are baked into the
model at conversion, so the runtime gets uint8 RGB. The C demos read image files; none uses a camera.

| Model | Task | Outputs and post-processing |
|---|---|---|
| YOLOv5, YOLOv7 | detection | 3 heads `[1, 3×(5+C), H, W]`, anchors per stride, sigmoid in the model; `xy=(v·2−0.5+grid)·stride`, `wh=(v·2)²·anchor`; score = obj × cls; per-class NMS |
| YOLOX | detection | 3 heads, anchor-free, `wh=exp(v)·stride`, obj × cls, NMS |
| YOLOv6, YOLOv8, YOLO11, PP-YOLOE | detection | 3 branches: box `[1, 4·reg, H, W]` (DFL, reg 16; 1 = direct distances), score `[1, C, H, W]`, optional score sum; NMS |
| YOLOv10 | detection | as v8, then top-K without NMS |
| YOLO26 | detection | boxes `[1, 4, 8400]` xyxy and scores `[1, 80, 8400]`, found by shape; top-K without NMS |
| YOLO-World v2s | open-vocabulary detection | a CLIP text model gives embeddings as the detector's second input; v8-style output with C = prompts |
| YOLOv8-OBB | oriented boxes | angle tensor, `θ=(a−0.25)·π`, probIoU NMS |
| YOLOv8-pose | pose, 17 keypoints | DFL boxes plus keypoints `[1, 17, 3, 8400]` |
| YOLOv5-seg, YOLOv8-seg | instance segmentation | detection plus 32 mask coefficients; prototypes `[1, 32, 160, 160]`; coefficients × prototypes, crop, threshold |
| DeepLabV3, PP-LiteSeg | semantic segmentation | class map, upsample, argmax |
| MobileSAM | promptable segmentation | encoder plus prompt decoder |
| RetinaFace | faces with 5 landmarks | prior boxes, variance {0.1, 0.2}, NMS |
| MobileNetV2, ResNet50 | classification | softmax, top-K |
| LPRNet | plate text | CTC over 18 steps × 68 classes |
| PP-OCR det / rec | text detection / recognition | DB map (threshold, contours, unclip) / CTC with dictionary |
| CLIP ViT-B/32 | image–text matching | embeddings, cosine × logit scale, softmax |

Platforms: full support on RK3562, RK3566, RK3568, RK3576, RK3588, RV1126B; RV1103 and RV1106 limited (INT8 only, NHWC
native output; ports of mobilenet, YOLOv5 to v8, v10, 11, 26, YOLOX, LPRNet) [3]. In the airockchip YOLOv8 export the
sigmoid is part of the model; the pose and OBB demos apply it on the host [3]. Radxa's NPU pages walk through these
demos with image files; no camera demo was found [10] **[Unconfirmed: search snippets and one page]**.

### 2.2 Hailo's examples for the Raspberry Pi 5 [11] [12]

hailo-rpi5-examples now points to hailo-apps. Its GStreamer pipelines: detection (YOLOv5 to v11, YOLO26, with a
tracker), pose (YOLOv8-pose), instance segmentation (YOLOv5/v8-seg, FastSAM), depth (SCDepthV3), tiling for small
objects, CLIP on detected crops, several streams at once, face recognition (SCRFD + ArcFace), OCR (PaddleOCR), re-ID.
Standalone apps add oriented boxes, lane detection, super-resolution, classification, stereo depth, semantic
segmentation and zero-shot classification. Detection models carry NMS in the compiled model; pose, segmentation,
depth, faces, CLIP and OCR decode on the host. Inputs: Raspberry Pi camera (Picamera2), libcamera, USB, RTSP, UDP,
files, images. Every pipeline shows video with the results drawn in; results reach a callback; there is no built-in
JSON output. A per-model `config.json` (output names, input size, classes, thresholds) is close to a model card.
Licence-plate recognition lives in TAPPAS [13] **[Unconfirmed]**.

### 2.3 Post-processing kinds that cover all of them [Inference]

| Kind | Covers |
|---|---|
| `yolo-anchor` | YOLOv5, v7, tiny-YOLOv4 |
| `yolo-anchorfree-obj` | YOLOX |
| `yolo-dfl` | YOLOv6, v8, v9, 11, PP-YOLOE, YOLO-World |
| `yolo-nms-free` | YOLOv10, YOLO26 |
| `device-nms` | models with NMS compiled in |
| `ssd-prior` | SSD, RetinaFace, SCRFD |
| `yolo-seg-protos` | YOLOv5/v8-seg, FastSAM |
| `yolo-pose` | YOLOv8-pose, YOLO26-pose |
| `yolo-obb` | YOLOv8/11-OBB |
| `classification` | MobileNet, ResNet, FastViT |
| `semantic-seg` | DeepLabV3, PP-LiteSeg, FCN, SegFormer, STDC |
| `embedding` | ArcFace, CLIP, re-ID |
| `depth-map` | SCDepth, StereoNet |
| `ctc-text` | LPRNet, PP-OCR rec |
| `db-text-det` | PP-OCR det |

Later: two-stage composition (detector → crop → second model), promptable segmentation, super-resolution, lanes. A
model card names the input (size, layout, colour order, resize, pad value, mean and std, whether normalisation is in
the model), the quantisation, labels, thresholds, the score activation (in the model or on the host), and the role of
each output; most roles can be found by tensor shape, as the zoo's YOLO26 and v8 code does [3].

## 3. Cameras on Rockchip boards

On Rockchip vendor kernels every supported CSI camera becomes a V4L2 multiplanar NV12 node of the ISP (`rkisp`), and it
gives usable images only with Rockchip's 3A service (`rkaiq_3A_server`) and a tuning (IQ) file for the sensor and
module under `/etc/iqfiles/` [14] [15]. libcamera does not work on vendor kernels; mainline has no ISP for RK356x yet
[16] [17].

| Camera | Rockchip support | How |
|---|---|---|
| Raspberry Pi Camera v1.3 (OV5647) and its clones (Waveshare, Arducam 5MP, SunFounder, OKdo) | official on Zero 3W/3E, ROCK 3, 4, 5, CM3/4/5 | Radxa overlays such as `radxa-zero3-rpi-camera-v1.3` [18] |
| Raspberry Pi Camera v2 (IMX219), Radxa Camera 8M | official on the same boards | `radxa-zero3-rpi-camera-v2`, `radxa-zero3-radxa-camera-8m` [18] |
| Radxa Camera 4K (IMX415), Waveshare IMX415 | ROCK 4D, 5 series, CM4/CM5 (not Zero 3W, not ROCK 3) | Radxa overlays [18] |
| Radxa 13M (IMX214), 12M (IMX577), 2M (AR0234), OV9281 | various ROCK boards | Radxa overlays [18] |
| Raspberry Pi Camera 3 (IMX708), HQ (IMX477), Global Shutter (IMX296), Arducam IMX519, IMX290/327/462 | no driver in the vendor kernel [19]; community ports, Arducam's install script for older Radxa images [20] [21] | driver, overlay and IQ file from the maker's instructions |
| Raspberry Pi AI Camera (IMX500) | needs Raspberry Pi's firmware and camera stack [22] | not portable as is **[Inference]** |
| USB (UVC) cameras | every board | `uvcvideo`; MJPEG for 720p and up on USB 2.0, YUYV for small sizes |
| RTSP network cameras | every board | H.264/H.265 decoded by Rockchip's MPP |

On the Radxa Zero 3W the 22-pin CSI connector is used with two lanes; capture works on vendor kernel 5.10.160-26,
later builds broke it for some users [23]. "Soundforge" turned up no camera maker; SunFounder's module is an OV5647.
PiSugar sells no camera; Seeed resells the standard sensors.

Capture paths [14] [24]: find the node by name in `/sys/class/video4linux/*/name` (`rkisp_mainpath`, `rkisp_selfpath`,
or the `uvcvideo` driver), never by number. CSI: multiplanar NV12, DMA-BUF buffers, then Rockchip's RGA for scaling,
colour conversion and letterbox. USB: MJPEG decoded by MPP (`librockchip_mpp`), libjpeg-turbo as the fallback. RTSP
and files: MPP decode; vendor demos use GStreamer (`mppvideodec`) or FFmpeg with rkmpp [25].

## 4. What this means for llmcf

[Inference] The decisions taken from this research are in DESIGN.md (component "On the board", and the decision log
from [its entry of 2026-11-09](../DESIGN.md#decision-round-66) on):

- `librknnrt.so` is never shipped: the board's own copy first, else a download pinned to a commit and its SHA256, with
  the person's consent and Rockchip's licence shown; the runtime is checked against the model's toolkit version.
- The engine is Rust for aarch64 with glibc and loads the runtime by its absolute path at start.
- Every model variant through a model card and the post-processing kinds of 2.3.
- Every camera the board shows as an ISP or USB video device, plus RTSP, files and images; the board installer sets up
  overlays, the 3A service and the IQ file with the person's approval, following the camera maker's instructions.
- V4L2, MPP and RGA directly; GStreamer only as a fallback where it is needed.
- Outputs follow what the model can do: boxes, masks, keypoints, text or embeddings as JSON and events, and video with
  the results drawn in.

## 5. Sources

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
