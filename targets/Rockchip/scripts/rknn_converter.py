#!/usr/bin/env python3
"""
RKNN Converter Script
Bridge between Shell and RKNN Toolkit2 API (run with the image's RKNN venv, RKNN_PYTHON, by rknn_module.sh).
The same file in targets/Rockchip/scripts and targets/_template/scripts (tests/test_target_template.py compares them).

It also writes the model card of a vision model (`<name>.card.json` next to `<name>.rknn`, schema llmcf-model-card/1,
read by the board's vision engine llmcf-detect), because only the conversion knows how the model was converted: the
input size, colour order, mean and std from the model's own configuration (the metadata of an Ultralytics ONNX export
with its class names, stride and image size; preprocessor_config.json; config.json with id2label; a labels.txt next
to the ONNX file), the output names and shapes from the ONNX graph, the post-processing kind chosen by the rules of
card_from_facts, and the toolkit version, target platform and quantisation it converted with. Mean and std go to
rknn.config, so the normalisation is in the model and the engine feeds plain uint8 RGB. Where the rules cannot
decide the kind, a role or the labels, no card is written and the log says what is missing; the person then gives a
card with the model (the same schema). The host checks every card it takes in (orchestrator/runtime/bundle/
model_card.py).
"""

import ast
import hashlib
import json
import sys
import os
import argparse
import logging
import time

logging.basicConfig(level=logging.INFO, format='[RKNN-PY] %(message)s')
logger = logging.getLogger("RKNN_Converter")

CARD_SCHEMA = "llmcf-model-card/1"
# YOLOv5's default anchors per stride 8, 16, 32 (input pixels)
YOLOV5_ANCHORS = [[[10, 13], [16, 30], [33, 23]], [[30, 61], [62, 45], [59, 119]], [[116, 90], [156, 198], [373, 326]]]


def _labels_from(meta, configs):
    """The class names: the Ultralytics metadata's names, config.json's id2label or labels.txt; None when none."""
    names = meta.get("names")
    if names:
        try:
            parsed = ast.literal_eval(names)
        except (ValueError, SyntaxError):
            parsed = None
        if isinstance(parsed, dict) and parsed:
            return [str(parsed[k]) for k in sorted(parsed)]
        if isinstance(parsed, list) and parsed:
            return [str(x) for x in parsed]
    id2label = (configs.get("config.json") or {}).get("id2label")
    if isinstance(id2label, dict) and id2label:
        try:
            return [str(id2label[k]) for k in sorted(id2label, key=int)]
        except (ValueError, TypeError):
            return None
    labels = configs.get("labels.txt")
    if isinstance(labels, str):
        lines = [x.strip() for x in labels.splitlines() if x.strip()]
        return lines or None
    return None


def _normalisation(meta, configs):
    """(mean, std, colour) in 0..255 for rknn.config, or None when the model says nothing about it."""
    if meta.get("task") or meta.get("names"):
        return [0, 0, 0], [255, 255, 255], "rgb"    # Ultralytics: RGB scaled to 0..1, no mean
    pre = configs.get("preprocessor_config.json")
    if isinstance(pre, dict) and pre.get("image_mean") and pre.get("image_std"):
        mean = [round(float(v) * 255, 3) for v in pre["image_mean"]]
        std = [round(float(v) * 255, 3) for v in pre["image_std"]]
        if pre.get("do_normalize") is False:
            mean, std = [0, 0, 0], [255, 255, 255]
        if len(mean) == 3 and len(std) == 3:
            return mean, std, "rgb"
    return None


def card_from_facts(meta, input_shape, outputs, configs, settings):
    """The model card from the model's facts (pure: tested without the toolkit). `meta`: the ONNX metadata,
    `input_shape`: [1, 3, H, W], `outputs`: [(name, shape)], `configs`: the model folder's configuration files,
    `settings`: file, sha256, toolkit, platform, quantisation, name, licence. Returns (card, missing): the card, or
    None with what is missing."""
    missing = []
    labels = _labels_from(meta, configs)
    if not labels:
        missing.append("the class names (Ultralytics metadata names, config.json id2label or a labels.txt)")
    norm = _normalisation(meta, configs)
    if norm is None:
        missing.append("the normalisation (mean and std: Ultralytics metadata or preprocessor_config.json)")
    if not (isinstance(input_shape, list) and len(input_shape) == 4 and input_shape[1] == 3
            and all(isinstance(d, int) and d > 0 for d in input_shape)):
        missing.append("a static input of the shape [1, 3, height, width]")
        return None, missing
    h, w = input_shape[2], input_shape[3]
    classes = len(labels or [])
    shapes = [list(s) for _n, s in outputs]
    post, roles, activation = None, None, "model"
    four = [s for s in shapes if len(s) == 4 and s[0] == 1]
    if classes and len(four) == len(shapes) and shapes:
        # Rockchip's YOLOv6/v8/11 export: per stride boxes [1, 4*reg, h, w], scores [1, C, h, w], a score sum
        regs = {s[1] // 4 for s in four if s[1] % 4 == 0 and s[1] not in (classes, 1)}
        strides = sorted({h // s[2] for s in four if s[2] and h % s[2] == 0})
        heads5 = all(s[1] % (5 + classes) == 0 for s in four)
        if heads5 and len(four) == 3 and all(s[1] == 3 * (5 + classes) for s in four) and strides == [8, 16, 32]:
            post = {"kind": "yolo-anchor", "strides": strides, "anchors": YOLOV5_ANCHORS}
        elif heads5 and all(s[1] == 5 + classes for s in four):
            post = {"kind": "yolo-anchorfree-obj", "strides": strides}
        elif len(regs) == 1 and all(s[1] in (classes, 1) or s[1] // 4 in regs for s in four):
            post = {"kind": "yolo-dfl", "strides": strides, "reg_max": regs.pop()}
        if post is not None:
            roles = ["auto" if post["kind"] == "yolo-dfl" else "boxes" for _ in shapes]
    elif classes and len(shapes) == 2 and all(len(s) == 3 for s in shapes):
        # YOLO26 as Rockchip's zoo exports it: decoded boxes [1, 4, N] and scores [1, C, N]
        boxes = [s for s in shapes if s[1] == 4]
        scores = [s for s in shapes if s[1] == classes and s is not (boxes or [None])[0]]
        if len(boxes) >= 1 and scores and boxes[0][2] == scores[0][2]:
            post = {"kind": "yolo-nms-free", "box_format": "xyxy", "top_k": 300}
            roles = ["auto", "auto"]
    if post is None:
        missing.append("a post-processing kind the rules can tell from the outputs "
                       + ", ".join(str(s) for s in shapes)
                       + " (a single combined output, as the stock Ultralytics export has, needs Rockchip's export "
                         "or a card given with the model)")
    if missing:
        return None, missing
    card = {
        "schema": CARD_SCHEMA,
        "model": {"name": settings["name"][:128], "licence": settings.get("licence") or "unknown"},
        "rknn": {"file": settings["file"], "sha256": settings["sha256"], "toolkit": settings["toolkit"],
                 "platform": settings["platform"], "quantisation": settings["quantisation"]},
        "input": {"width": w, "height": h, "layout": "nhwc", "colour": norm[2], "resize": "letterbox", "pad": 114,
                  "mean": norm[0], "std": norm[1], "normalised_in_model": True, "type": "uint8"},
        "task": "detect",
        "postprocess": post,
        "labels": labels,
        "thresholds": {"score": 0.25, "nms_iou": 0.45, "max_results": 100},
        "score_activation": activation,
        "outputs": [{"index": i, "role": r, "shape": s} for i, (r, s) in enumerate(zip(roles, shapes))],
    }
    return card, []


def onnx_facts(onnx_path):
    """(metadata, input shape, outputs, configs) of an ONNX model and the configuration files next to it."""
    import onnx  # the RKNN venv's onnx (rknn-toolkit2 depends on it)
    model = onnx.load(onnx_path, load_external_data=False)
    meta = {p.key: p.value for p in model.metadata_props}

    def shape(value):
        return [d.dim_value if d.HasField("dim_value") else -1 for d in value.type.tensor_type.shape.dim]
    initialisers = {i.name for i in model.graph.initializer}
    inputs = [i for i in model.graph.input if i.name not in initialisers]
    input_shape = shape(inputs[0]) if len(inputs) == 1 else None
    outputs = [(o.name, shape(o)) for o in model.graph.output]
    configs = {}
    folder = os.path.dirname(os.path.abspath(onnx_path))
    for name in ("config.json", "preprocessor_config.json", "labels.txt"):
        path = os.path.join(folder, name)
        if os.path.isfile(path) and os.path.getsize(path) <= 4 * 1024 * 1024:
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
            try:
                configs[name] = json.loads(text) if name.endswith(".json") else text
            except ValueError:
                pass
    return meta, input_shape, outputs, configs


def toolkit_version():
    try:
        from importlib.metadata import version
        return version("rknn-toolkit2")
    except Exception:  # noqa: BLE001 - no version known: no card
        return None


def card_path(output_path):
    base = output_path[:-len(".rknn")] if output_path.endswith(".rknn") else output_path
    return base + ".card.json"


def convert_to_rknn(onnx_path, output_path, target_platform, dtype='i8', dataset_path=None):
    try:
        from rknn.api import RKNN
    except ImportError:
        logger.error("CRITICAL: 'rknn' module not found.")
        sys.exit(1)

    output_path = os.path.abspath(output_path)  # the build may change the working directory
    rknn = RKNN(verbose=False)

    logger.info(f"Configuring for {target_platform.upper()}...")
    # rknn-toolkit2 >= 2.x names quantization schemes w8a8/w8a16/...; FP16 simply means no quantization.
    config_kwargs = {"target_platform": target_platform, "optimization_level": 3}
    if dtype == 'i8':
        config_kwargs["quantized_dtype"] = "w8a8"
    try:
        facts = onnx_facts(onnx_path)
    except Exception as e:  # noqa: BLE001 - no facts: the model converts, without a card
        logger.info(f"The model's facts cannot be read ({type(e).__name__}); no model card.")
        facts = None
    norm = _normalisation(facts[0], facts[3]) if facts else None
    if norm is not None:
        # the normalisation goes into the model, so the board's engine feeds plain uint8 RGB
        config_kwargs["mean_values"] = [norm[0]]
        config_kwargs["std_values"] = [norm[1]]
    rknn.config(**config_kwargs)

    logger.info(f"Loading model: {os.path.basename(onnx_path)}")
    if rknn.load_onnx(model=onnx_path) != 0:
        logger.error("Load failed!")
        sys.exit(1)

    logger.info(f"Building (Quant: {dtype}, Dataset: {dataset_path or 'None'})...")
    do_quant = (dtype == 'i8')
    
    # The dataset lists sample inputs; relative entries are relative to the list file, so build from there.
    if dataset_path:
        dataset_path = os.path.abspath(dataset_path)
        os.chdir(os.path.dirname(dataset_path))
    if rknn.build(do_quantization=do_quant, dataset=dataset_path) != 0:
        logger.error("Build failed!")
        sys.exit(1)

    logger.info(f"Exporting to {output_path}...")
    if rknn.export_rknn(output_path) != 0:
        logger.error("Export failed!")
        sys.exit(1)

    logger.info("✅ Success.")
    if facts is not None:
        write_card(facts, output_path, target_platform, dtype)


def write_card(facts, output_path, target_platform, dtype):
    """The model card next to the .rknn, or the log line that says what is missing."""
    meta, input_shape, outputs, configs = facts
    with open(output_path, "rb") as fh:
        sha = hashlib.sha256(fh.read()).hexdigest()
    toolkit = toolkit_version()
    if not toolkit:
        logger.info("No model card: the toolkit's version is not known.")
        return
    name = os.path.basename(output_path)[:-len(".rknn")] if output_path.endswith(".rknn") else "model"
    licence = (meta.get("license") or "").split(" ")[0].strip()
    settings = {"file": os.path.basename(output_path), "sha256": sha, "toolkit": toolkit.split("+")[0],
                "platform": target_platform.lower(), "quantisation": "w8a8" if dtype == "i8" else "fp16",
                "name": name, "licence": licence if licence.replace("-", "").replace(".", "").isalnum() else "unknown"}
    card, missing = card_from_facts(meta, input_shape, outputs, configs, settings)
    if card is None:
        logger.info("No model card: missing " + "; ".join(missing) + ". Give the model's card with the model.")
        return
    with open(card_path(output_path), "w", encoding="utf-8") as fh:
        json.dump(card, fh, indent=1)
        fh.write("\n")
    logger.info(f"Model card written: {os.path.basename(card_path(output_path))} ({card['postprocess']['kind']})")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--model', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--target', default='rk3566')
    parser.add_argument('--dtype', default='i8')
    parser.add_argument('--dataset', default=None, help='Path to calibration dataset')
    args = parser.parse_args()

    if not os.path.exists(args.model):
        logger.error(f"Input file not found: {args.model}")
        sys.exit(1)
    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)

    start_ts = time.time()
    convert_to_rknn(args.model, args.output, args.target, args.dtype, args.dataset)
    logger.info(f"Total time: {time.time() - start_ts:.2f}s")
