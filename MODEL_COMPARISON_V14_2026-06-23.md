# V14 FP32 vs Current YOLOX V2 Comparison

Date: 2026-06-23 KST

## Scope

Compared the current `recording_AB` YOLOX-tiny LoRA V2 model against the V14 FP32 BVM models found in `abc_collector_v3`.

Dataset:

- Annotation: `yolox_lora_train/datasets/coco/annotations/instances_val2017.json`
- Images: `yolox_lora_train/datasets/coco/val2017`
- Size: 33 images, 161 boxes
- Current dataset classes: `abdomen`, `face`, `bvm_mask`, `bvm_bag`

Models:

- Current V2 checkpoint: `yolox_lora_train/runs/20260622_233439_v2_head_ft/yolox_tiny_lora_v2/best_ckpt.pth`
- Current V2 ONNX export: `yolox_lora_train/weights/yolox_tiny_lora_v2_640.onnx`
- V14 active ONNX: `C:/Users/USER/Documents/01_Git/abc_collector_v3/models/bvm_v14_fp32_320.onnx`
- V14 fallback ONNX: `C:/Users/USER/Documents/01_Git/abc_collector_v3/models/bvm_v14_fp32.onnx`

V14 class mapping used for this dataset:

- `bvm_bag` -> `bvm_bag`
- `bvm_mask` -> `bvm_mask`
- `head` -> `face`
- `torso` -> `abdomen`

This mapping is approximate for `head/face` and `torso/abdomen`; the labels are not guaranteed to be semantically identical.

## Results

### Native YOLOX Eval

This is the cleanest number for the current V2 checkpoint because it uses the same YOLOX evaluation path used during training.

| model | AP | AP50 | AP75 | AR100 |
|---|---:|---:|---:|---:|
| current_v2 checkpoint | 0.874 | 0.999 | 0.978 | 0.906 |

Per-class AP:

| class | AP |
|---|---:|
| abdomen | 0.972 |
| face | 0.911 |
| bvm_mask | 0.790 |
| bvm_bag | 0.822 |

### Same ONNX Postprocess Eval

This compares exported ONNX models through the same ONNX Runtime predictor/postprocess path, with `conf=0.01` and `nms=0.65`.

| model | AP | AP50 | AP75 | AR100 |
|---|---:|---:|---:|---:|
| current_v2 ONNX 640 | 0.734 | 0.937 | 0.806 | 0.838 |
| bvm_v14_fp32_320 | 0.048 | 0.094 | 0.051 | 0.110 |
| bvm_v14_fp32_640 | 0.301 | 0.417 | 0.338 | 0.407 |

Per-class AP:

| model | abdomen | face | bvm_mask | bvm_bag |
|---|---:|---:|---:|---:|
| current_v2 ONNX 640 | 0.894 | 0.843 | 0.552 | 0.645 |
| bvm_v14_fp32_320 | 0.000 | 0.000 | 0.011 | 0.182 |
| bvm_v14_fp32_640 | 0.707 | 0.053 | 0.086 | 0.359 |

Artifacts:

- `yolox_lora_train/runs/compare_v14_20260623_084803/summary.md`
- `yolox_lora_train/runs/compare_v14_20260623_085100/summary.md`
- `yolox_lora_train/runs/compare_v14_20260623_084803/current_v2_best_model_only.pth`

## Conclusion

The current V2 model is clearly better on the user's current labeled dataset.

The active V14 320 model is not usable as-is for this dataset. The 640 fallback model is better than the 320 export, especially for `abdomen/torso`, but still trails the current V2 model by a wide margin and is weak on `face/head`, `bvm_mask`, and `bvm_bag`.

The main reason is not just model quality. The V14 model was trained/exported under a different class schema and likely a different visual distribution. It uses `head/torso`, while the current dataset uses `face/abdomen`; it also was not fine-tuned on this iPhone-labeled set.

## Next Step

Use current V2 as the detector baseline for the current dataset. Keep V14 as a historical/operational reference only. If V14 behavior is still desired, fine-tune or distill from V14 into the current 4-class schema rather than swapping it in directly.
