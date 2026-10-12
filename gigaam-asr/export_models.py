#!/usr/bin/env python3
"""One-off model build for gigaam-asr: GigaAM and VoxLingua107 -> ONNX, so the runtime needs only
numpy + onnxruntime + sentencepiece (no torch: -300 MB RAM). Runs in a temporary build venv
(torch, gigaam, speechbrain, onnx, onnxscript) that install-gigaam-asr.sh creates and deletes.

The upstream weights are downloaded and checksum-verified by install-gigaam-asr.sh BEFORE this script runs;
here they are only read from local directories (nothing is fetched from the network).

  export_models.py lid <src_dir> <out_dir>
      speechbrain/lang-id-voxlingua107-ecapa (Apache-2.0), files from <src_dir>:
      wav -> log-probs[107] (Fbank + sentence mean norm + ECAPA + classifier inside the graph)
      -> <out_dir>/lid.onnx, <out_dir>/lid_labels.txt
  export_models.py gigaam <ckpt_dir> <out_dir>
      GigaAM v3_e2e_ctc (Sber, MIT): <ckpt_dir>/v3_e2e_ctc.ckpt + v3_e2e_ctc_tokenizer.model -> fp32 ONNX
  export_models.py gigaam-quantize <out_dir>
      fp32 ONNX -> <out_dir>/gigaam.int8.onnx (dynamic, QUInt8). Separate process: the export peak and
      the quantization peak together do not fit into ~4 GB RAM.
"""
import os
import shutil
import sys

import torch

GIGAAM = os.environ.get("GIGAAM_MODEL", "v3_e2e_ctc")


def gigaam(ckpt_dir: str, out: str) -> None:
    import gigaam as g
    # files are already in ckpt_dir -> gigaam does not download, but still checks its own md5 of the ckpt
    m = g.load_model(GIGAAM, device="cpu", fp16_encoder=False, download_root=ckpt_dir)
    m.to_onnx(dir_path=out, dtype=torch.float32)
    shutil.copy(os.path.join(ckpt_dir, f"{GIGAAM}_tokenizer.model"), os.path.join(out, "tokenizer.model"))
    print("exported", sorted(os.listdir(out)))


def gigaam_quantize(out: str) -> None:
    from onnxruntime.quantization import QuantType, quantize_dynamic
    src = os.path.join(out, f"{GIGAAM}.onnx")
    quantize_dynamic(src, os.path.join(out, "gigaam.int8.onnx"), weight_type=QuantType.QUInt8)
    os.unlink(src)
    print("quantized", sorted(os.listdir(out)))


def lid(src: str, out: str) -> None:
    from speechbrain.inference.classifiers import EncoderClassifier
    src = os.path.abspath(src)
    # pretrained_path in hyperparams.yaml points to the HF repo id -> override with the verified local copy
    sb = EncoderClassifier.from_hparams(source=src, savedir=os.path.join(out, "sb"),
                                        overrides={"pretrained_path": src})

    class LID(torch.nn.Module):
        """Fbank as in speechbrain, but STFT as a convolution with the DFT matrix: complex torch.stft
        cannot be exported to ONNX."""

        def __init__(self, mods, n_fft=400, hop=160):
            super().__init__()
            self.fbank, self.emb, self.cls = mods.compute_features.compute_fbanks, mods.embedding_model, mods.classifier
            self.n_fft, self.hop = n_fft, hop
            n = torch.arange(n_fft, dtype=torch.float64)
            k = torch.arange(n_fft // 2 + 1, dtype=torch.float64)[:, None]
            w = torch.hamming_window(n_fft, dtype=torch.float64)            # periodic, as in speechbrain STFT
            ang = 2 * torch.pi * k * n / n_fft
            kern = torch.cat([torch.cos(ang) * w, -torch.sin(ang) * w])[:, None, :].float()
            self.register_buffer("kern", kern)

        def forward(self, wav):                               # [1, N] float32, 16 kHz
            x = torch.nn.functional.pad(wav[:, None, :], (self.n_fft // 2, self.n_fft // 2))   # center, constant
            spec = torch.nn.functional.conv1d(x, self.kern, stride=self.hop)                  # [1, 2F, T]
            re, im = spec.chunk(2, dim=1)
            mag = (re * re + im * im).transpose(1, 2)                                         # [1, T, F]
            f = self.fbank(mag)
            f = f - f.mean(dim=1, keepdim=True)               # InputNormalization(sentence, std_norm=False)
            return self.cls(self.emb(f)).squeeze(1)           # [1, 107] log-probs

    model = LID(sb.mods).eval()
    wav = torch.randn(1, 16000 * 5)
    with torch.inference_mode():                              # convolutional STFT == speechbrain Fbank
        a = sb.mods.compute_features(wav)
        b = model.fbank((lambda s: (s[0] ** 2 + s[1] ** 2).transpose(1, 2))(
            torch.nn.functional.conv1d(torch.nn.functional.pad(wav[:, None, :], (200, 200)), model.kern,
                                       stride=160).chunk(2, dim=1)))
        print("fbank max abs diff:", float((a - b).abs().max()))
    torch.onnx.export(model, (wav,), os.path.join(out, "lid.onnx"), input_names=["wav"], output_names=["logp"],
                      dynamic_axes={"wav": {1: "n"}}, opset_version=17, dynamo=False)
    labels = [sb.hparams.label_encoder.decode_ndim(i).split(":")[0].strip() for i in range(107)]
    with open(os.path.join(out, "lid_labels.txt"), "w") as f:
        f.write("\n".join(labels) + "\n")
    with torch.inference_mode():                              # reference for the ONNX check
        ref = model(wav).numpy()
    import numpy as np
    import onnxruntime as rt
    got = rt.InferenceSession(os.path.join(out, "lid.onnx")).run(None, {"wav": wav.numpy()})[0]
    diff = float(np.abs(ref - got).max())
    print("lid max abs diff vs torch:", diff)
    shutil.rmtree(os.path.join(out, "sb"), ignore_errors=True)
    if diff > 1e-2:
        sys.exit(f"lid.onnx differs from the torch model too much ({diff})")


if __name__ == "__main__":
    what, args = sys.argv[1], sys.argv[2:]
    os.makedirs(args[-1], exist_ok=True)
    torch.set_num_threads(int(os.environ.get("NUM_THREADS", "2")))
    {"gigaam": gigaam, "gigaam-quantize": gigaam_quantize, "lid": lid}[what](*args)
