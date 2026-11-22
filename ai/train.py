"""Train the 7-class CNN in PyTorch and export weights for NumPy serving.

    python -m ai.dataset            # make ai/data/windows.npz
    python -m ai.train              # train, export ai/weights/cnn_v1.npz, write ai/weights/report.json

Training data = synthetic windows + live-world windows. The held-out test set is live-world windows
from different seeds, never seen in training. The report has per-class accuracy and the confusion matrix.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np
import torch
from torch import nn

from .cnn import CNN
from .features import CLASSES

OUT = Path(__file__).resolve().parent / "weights"


class Net(nn.Module):
    def __init__(self, n_cls: int = len(CLASSES)):
        super().__init__()
        self.c1 = nn.Conv2d(1, 16, (3, 5), padding=(1, 2))
        self.c2 = nn.Conv2d(16, 32, 3, padding=1)
        self.c3 = nn.Conv2d(32, 48, 3, padding=1)
        self.fc = nn.Linear(48, n_cls)
        self.pool = nn.MaxPool2d((1, 2))

    def forward(self, x):
        x = self.pool(torch.relu(self.c1(x)))
        x = self.pool(torch.relu(self.c2(x)))
        x = torch.relu(self.c3(x))
        return self.fc(x.mean(dim=(2, 3)))


def augment(x: torch.Tensor) -> torch.Tensor:
    """Random level shift (receiver gain / distance) and a random roll in time."""
    shift = (torch.rand(x.shape[0], 1, 1, 1) - 0.5) * 0.3
    x = x + shift * (x > 0.25).float()           # move signals, not the noise floor
    k = int(torch.randint(0, 12, (1,)))
    return torch.roll(x, k, dims=2) if torch.rand(1) < 0.3 else x


def export(model: Net, path: Path, meta: dict) -> None:
    sd = {k: v.detach().cpu().numpy() for k, v in model.state_dict().items()}
    np.savez(path, **{"c1.w": sd["c1.weight"], "c1.b": sd["c1.bias"], "c2.w": sd["c2.weight"], "c2.b": sd["c2.bias"],
                      "c3.w": sd["c3.weight"], "c3.b": sd["c3.bias"], "fc.w": sd["fc.weight"], "fc.b": sd["fc.bias"]},
             classes=np.array(CLASSES), meta=np.array(json.dumps(meta)))


N_CLASSES = len(CLASSES)


def confusion(y_true, y_pred, n=N_CLASSES):
    m = np.zeros((n, n), dtype=int)
    for t, p in zip(y_true, y_pred):
        m[t, p] += 1
    return m


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="ai/data/windows.npz")
    ap.add_argument("--epochs", type=int, default=14)
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()
    torch.manual_seed(a.seed)
    d = np.load(a.data)
    X = np.concatenate([d["Xs"], d["Xl"], d["Xl"]]).astype(np.float32)          # live windows weighted x2
    y = np.concatenate([d["ys"], d["yl"], d["yl"]])
    Xt, yt = d["Xt"].astype(np.float32), d["yt"]
    rng = np.random.default_rng(a.seed)
    idx = rng.permutation(len(X))
    n_val = len(X) // 10
    val, tr = idx[:n_val], idx[n_val:]
    Xtr, ytr = torch.tensor(X[tr][:, None]), torch.tensor(y[tr])
    Xva, yva = torch.tensor(X[val][:, None]), torch.tensor(y[val])

    model = Net()
    opt = torch.optim.AdamW(model.parameters(), lr=3e-3, weight_decay=1e-4)
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=4e-3, total_steps=a.epochs * (len(tr) // 128 + 1))
    lossf = nn.CrossEntropyLoss(label_smoothing=0.05)
    t0 = time.time()
    for ep in range(a.epochs):
        model.train()
        perm = torch.randperm(len(Xtr))
        tot = 0.0
        for i in range(0, len(perm), 128):
            b = perm[i:i + 128]
            opt.zero_grad()
            loss = lossf(model(augment(Xtr[b])), ytr[b])
            loss.backward()
            opt.step()
            sched.step()
            tot += loss.item() * len(b)
        model.eval()
        with torch.no_grad():
            va = (model(Xva).argmax(1) == yva).float().mean().item()
            te = (model(torch.tensor(Xt[:, None])).argmax(1).numpy() == yt).mean()
        print(f"epoch {ep + 1:2d}  loss {tot / len(tr):.3f}  val {va:.3f}  live-test {te:.3f}  ({time.time() - t0:.0f}s)")

    OUT.mkdir(parents=True, exist_ok=True)
    meta = {"epochs": a.epochs, "train_windows": int(len(tr)), "trained_s": round(time.time() - t0)}
    export(model, OUT / "cnn_v1.npz", meta)

    # evaluate the exported NumPy model (what the server actually runs) on held-out live windows
    cnn = CNN(OUT / "cnn_v1.npz")
    from .features import NOISE_BIN_DBM
    preds, times = [], []
    for x in Xt:
        win_db = x * 20.0 + NOISE_BIN_DBM                    # undo normalize (clipping is idempotent)
        r = cnn.predict(win_db)
        preds.append(CLASSES.index(r["class"]))
        times.append(r["ms"])
    preds = np.array(preds)
    cm = confusion(yt, preds)
    per = {c: round(float(cm[i, i] / max(cm[i].sum(), 1)), 3) for i, c in enumerate(CLASSES)}
    report = {"live_test_windows": int(len(yt)), "accuracy": round(float((preds == yt).mean()), 4), "per_class": per,
              "confusion": cm.tolist(), "classes": CLASSES, "numpy_inference_ms_median": round(float(np.median(times)), 2),
              **meta}
    (OUT / "report.json").write_text(json.dumps(report, indent=2))
    print(json.dumps({k: report[k] for k in ("accuracy", "per_class", "numpy_inference_ms_median")}, indent=1))
    print("confusion (rows = true):", *CLASSES)
    for c, row in zip(CLASSES, cm):
        print(f"  {c:10s}", row)


if __name__ == "__main__":
    main()
