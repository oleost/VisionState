"""Train the wheel position model.

python train.py <out-name> <set>:<weight> [<set>:<weight> ...] [--epochs N] [--dryad-orient model.pt]

Sets are data/<set>.npz. A weight is the share of each batch drawn from that set.
"dryad_train" needs --dryad-orient (a model that picks each crop's orientation) or uses upright.
"""

import argparse

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

from common import BINS
from paths import WORK

HERE = WORK
DEV = "cuda" if torch.cuda.is_available() else "cpu"


class WheelNet(nn.Module):
    def __init__(self, width: int = 32):
        super().__init__()
        c = width

        def block(i, o):
            return nn.Sequential(
                nn.Conv2d(i, o, 3, padding=1, bias=False),
                nn.BatchNorm2d(o),
                nn.ReLU(inplace=True),
                nn.Conv2d(o, o, 3, padding=1, bias=False),
                nn.BatchNorm2d(o),
                nn.ReLU(inplace=True),
            )

        self.features = nn.Sequential(
            block(1, c),
            nn.MaxPool2d(2),  # 24x16
            block(c, 2 * c),
            nn.MaxPool2d(2),  # 12x8
            block(2 * c, 4 * c),
            nn.MaxPool2d(2),  # 6x4
            block(4 * c, 4 * c),
        )
        self.head = nn.Sequential(
            nn.Flatten(), nn.Dropout(0.2), nn.Linear(4 * c * 6 * 4, 256), nn.ReLU(inplace=True), nn.Linear(256, BINS)
        )

    def forward(self, x):  # x: N x 1 x H x W in 0..1
        return self.head(self.features(x * 2 - 1))


def interval_mask(mid: torch.Tensor, half: torch.Tensor) -> torch.Tensor:
    centres = torch.arange(BINS, device=mid.device, dtype=torch.float32) / 10
    dist = torch.remainder(centres[None, :] - mid[:, None] + 5, 10) - 5
    return dist.abs() <= half[:, None] + 1e-4


def interval_loss(logits, mid, half):
    logp = F.log_softmax(logits, dim=1)
    mask = interval_mask(mid, half)
    inside = torch.logsumexp(logp.masked_fill(~mask, -1e9), dim=1)
    return -inside.mean()


def augment(x: torch.Tensor) -> torch.Tensor:
    n = x.shape[0]
    inv = torch.rand(n, device=x.device) < 0.5
    x = torch.where(inv[:, None, None, None], 1 - x, x)
    # small shifts and scale
    theta = torch.zeros(n, 2, 3, device=x.device)
    s = 1 + (torch.rand(n, device=x.device) - 0.5) * 0.2
    theta[:, 0, 0] = s * (1 + (torch.rand(n, device=x.device) - 0.5) * 0.2)
    theta[:, 1, 1] = s
    theta[:, 0, 2] = (torch.rand(n, device=x.device) - 0.5) * 0.2
    theta[:, 1, 2] = (torch.rand(n, device=x.device) - 0.5) * 0.08  # small: vertical shift changes the position
    grid = F.affine_grid(theta, list(x.shape), align_corners=False)
    x = F.grid_sample(x, grid, padding_mode="border", align_corners=False)
    # contrast / brightness / noise
    a = 1 + (torch.rand(n, 1, 1, 1, device=x.device) - 0.5) * 0.6
    b = (torch.rand(n, 1, 1, 1, device=x.device) - 0.5) * 0.3
    x = (x - 0.5) * a + 0.5 + b + torch.randn_like(x) * 0.03 * torch.rand(n, 1, 1, 1, device=x.device)
    return x.clamp(0, 1)


def load(name: str):
    d = np.load(HERE / "data" / f"{name}.npz")
    return {k: d[k] for k in d.files}


@torch.no_grad()
def predict(model, x: np.ndarray, batch: int = 4096) -> np.ndarray:
    """uint8 cells -> log probabilities (N x BINS)."""
    model.eval()
    out = []
    for i in range(0, len(x), batch):
        t = torch.from_numpy(x[i : i + batch]).to(DEV).float().div(255)[:, None]
        out.append(F.log_softmax(model(t), dim=1).cpu().numpy())
    return np.concatenate(out)


def interval_logp(logp: np.ndarray, mid: np.ndarray, half: np.ndarray) -> np.ndarray:
    mask = interval_mask(torch.from_numpy(mid), torch.from_numpy(half)).numpy()
    return np.log(np.maximum(1e-12, (np.exp(logp) * mask).sum(axis=1)))


def orient_dryad(d: dict, model) -> dict:
    """Keep the orientation of each crop the model finds more likely."""
    lp = interval_logp(predict(model, d["X"]), d["mid"], d["half"])
    keys = d["img"] * 2 + d["rot"]
    score = np.bincount(keys, weights=lp, minlength=keys.max() + 1)
    n_img = d["img"].max() + 1
    best_rot = (score[1::2][:n_img] > score[0::2][:n_img]).astype(int)
    keep = d["rot"] == best_rot[d["img"]]
    print(f"dryad: {best_rot.mean():.1%} of crops turned")
    return {k: v[keep] for k, v in d.items()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("sets", nargs="+")
    ap.add_argument("--epochs", type=int, default=30)
    ap.add_argument("--steps", type=int, default=1000)
    ap.add_argument("--batch", type=int, default=512)
    ap.add_argument("--width", type=int, default=32)
    ap.add_argument("--dryad-orient")
    args = ap.parse_args()
    torch.manual_seed(0)
    np.random.seed(0)

    sets = []
    for spec in args.sets:
        name, w = spec.split(":")
        d = load(name)
        if "rot" in d:
            if args.dryad_orient:
                m = WheelNet(32).to(DEV)  # the orienting model
                m.load_state_dict(torch.load(args.dryad_orient, map_location=DEV))
                d = orient_dryad(d, m)
            else:
                d = {k: v[d["rot"] == 0] for k, v in d.items()}
        x = torch.from_numpy(d["X"]).to(DEV)
        sets.append((name, float(w), x, torch.from_numpy(d["mid"]).to(DEV), torch.from_numpy(d["half"]).to(DEV)))
        print(name, len(x), w)
    total_w = sum(s[1] for s in sets)

    model = WheelNet(args.width).to(DEV)
    print("params", sum(p.numel() for p in model.parameters()))
    opt = torch.optim.AdamW(model.parameters(), lr=2e-3, weight_decay=1e-4)
    total = args.epochs * args.steps
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=3e-3, total_steps=total, pct_start=0.1)
    step = 0
    for epoch in range(args.epochs):
        model.train()
        losses = []
        for _ in range(args.steps):
            xs, ms, hs = [], [], []
            for _name, w, x, m, h in sets:
                k = max(1, round(args.batch * w / total_w))
                idx = torch.randint(0, len(x), (k,), device=DEV)
                xs.append(x[idx])
                ms.append(m[idx])
                hs.append(h[idx])
            xb = augment(torch.cat(xs).float().div(255)[:, None])
            loss = interval_loss(model(xb), torch.cat(ms), torch.cat(hs))
            opt.zero_grad(set_to_none=True)
            loss.backward()
            opt.step()
            sched.step()
            step += 1
            losses.append(loss.item())
        print(f"epoch {epoch + 1}/{args.epochs} loss {np.mean(losses):.4f}", flush=True)
    torch.save(model.state_dict(), HERE / "runs" / f"{args.out}.pt")


if __name__ == "__main__":
    (HERE / "runs").mkdir(exist_ok=True)
    main()
