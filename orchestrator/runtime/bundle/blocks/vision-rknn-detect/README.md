# Object detection on the NPU (llmcf-detect)

Block `vision-rknn-detect` (kind detect) of the board bundle: `llmcf-detect`, the framework's vision engine, runs one
RKNN detection model on the Rockchip NPU of an RK3562, RK3566, RK3568, RK3576 or RK3588 board, on 127.0.0.1, reached
only through the gateway.

## Models

- The program comes from the framework, never from a file you pick: `llmcf-detect` is built from the framework's Rust
  workspace for aarch64 with glibc 2.31 (zig through cargo-zigbuild, both pinned), by cargo on this computer or in the
  framework's builder image, and kept for later bundles.
- One RKNN model (`model.rknn`, recognized by its first bytes) converted by the Rockchip target module for the board's
  SoC (RK3566 and RK3568 models are interchangeable, every other pair is not), and its model card
  (`model.card.json`, schema `llmcf-model-card/1`), which the target module writes next to the `.rknn` at conversion.
  The plan checks the card by the engine's own rules and that it names exactly this model (its SHA256); the engine
  checks both again at every start, before Rockchip's runtime reads the model.
- The detection kinds of the card: `yolo-anchor` (YOLOv5, v7), `yolo-anchorfree-obj` (YOLOX), `yolo-dfl` (YOLOv6, v8,
  11, PP-YOLOE), `yolo-nms-free` (YOLOv10, YOLO26), `device-nms` (NMS inside the model) and `ssd-prior` (SSD,
  RetinaFace, SCRFD with landmarks).
- Rockchip's NPU runtime `librknnrt.so` is never part of the bundle (its licence allows no redistribution):
  `rknn_runtime` board takes the board's own copy of its vendor package (`/usr/lib/librknnrt.so` or
  `/usr/lib/aarch64-linux-gnu/librknnrt.so`), pinned takes the pinned 2.5.0 download at
  `/opt/llmcf/vendor/rknn/2.5.0/librknnrt.so`, checked by its SHA256 at every start. The runtime must be at least the
  toolkit version the model was converted with, the NPU driver at least 0.9.2 (below 0.9.8 the engine warns).
- Memory: 96 MB plus twice the model file, until the board's measurement measures the engine.
- Licence: name the licence of the model you converted.

## Variables

- `port` (8093), `rknn_runtime` (board), `npu_cores` (auto; on an RK3588 or RK3576 cores joined by '.', refused on a
  single-core SoC).
- `score_pct`, `nms_pct`, `max_results` (0: the card's thresholds) and `classes` (all, or class ids such as 0.2.15):
  decisions of use, never facts of the model. A request may narrow them further with its `options` field.
- `file_source` (no) and `source_fps` (2): play the still images of the component's `media` folder as the engine's
  source; the event stream `GET /api/detect/events` sends one result per processed frame while someone follows it.
- `autostart` (no): with yes, root actions create_user, install_unit and set_cgroup (`user` llmcf-vision,
  `memory_limit_mb`, OOMScoreAdjust 500), carried out by llmcf-install; in turn the gateway starts the engine on its
  first request.

## Routes through the gateway

- `POST /api/detect`: a form with the file field `image` (JPEG or PNG) and the optional text field `options` (JSON:
  `score`, `nms_iou`, `max_results`, `labels`), answered with one JSON object of the schema `llmcf-vision-result/1`.
- `POST /api/detect/image`: the image alone (JPEG or PNG), answered with the defaults.
- `GET /api/detect/events`: the source's results as an event stream.
- An image whose header announces more than 40 megapixels is refused (422) before it is decoded.
