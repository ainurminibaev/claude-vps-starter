#!/usr/bin/env python3
"""One-off: load fp32 omniASR-CTC-300M-v2 (HF format), dynamic-quantize Linear
layers to int8 and save the whole module with torch.save.

Usage: quantize.py <fp32_dir> <out_dir>
Produces <out_dir>/model_int8.pt, vocab.json, config.json, SOURCE.txt
<fp32_dir> must contain config.json, vocab.json and model.safetensors.
Run it with the same python/torch/transformers as server.py: the module is
pickled whole, so server.py needs the same library versions to load it.
"""
import json
import os
import shutil
import sys
import time

import torch
from transformers import Wav2Vec2ForCTC

if len(sys.argv) != 3:
    sys.exit("usage: quantize.py <fp32_dir> <out_dir>")
src, dst = sys.argv[1], sys.argv[2]
model_repo = os.environ.get("MODEL_REPO", "aadel4/omniASR-CTC-300M-v2")
os.makedirs(dst, exist_ok=True)
torch.set_num_threads(int(os.environ.get("NUM_THREADS", "2")))
t = time.time()
model = Wav2Vec2ForCTC.from_pretrained(src)
model.eval()
# weight_norm parametrization (pos_conv_embed) blocks pickling -> bake it into a plain weight
from torch.nn.utils import parametrize
for m in model.modules():
    if parametrize.is_parametrized(m):
        for name in list(m.parametrizations.keys()):
            parametrize.remove_parametrizations(m, name, leave_parametrized=True)
# drop leftover local (unpicklable) weight_norm compat hooks
for m in model.modules():
    for hooks in (m._load_state_dict_pre_hooks, m._state_dict_hooks, m._state_dict_pre_hooks,
                  m._load_state_dict_post_hooks):
        for k in [k for k, h in hooks.items()
                  if "weight_norm" in getattr(getattr(h, "hook", h), "__qualname__", "")]:
            del hooks[k]
qmodel = torch.ao.quantization.quantize_dynamic(model, {torch.nn.Linear}, dtype=torch.qint8)
del model
tmp = os.path.join(dst, "model_int8.pt.tmp")
torch.save(qmodel, tmp)
os.replace(tmp, os.path.join(dst, "model_int8.pt"))
shutil.copy(os.path.join(src, "vocab.json"), os.path.join(dst, "vocab.json"))
shutil.copy(os.path.join(src, "config.json"), os.path.join(dst, "config.json"))
with open(os.path.join(dst, "SOURCE.txt"), "w") as f:
    f.write(f"{model_repo} (Apache-2.0), dynamic int8 quantization of nn.Linear, "
            f"torch {torch.__version__}\n")
print(json.dumps({"sec": round(time.time() - t, 1),
                  "size_mb": round(os.path.getsize(os.path.join(dst, "model_int8.pt")) / 1e6, 1)}))
