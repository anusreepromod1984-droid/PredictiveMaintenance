"""Train PINN only when enough run-to-failure trajectories exist."""

from datetime import datetime, timezone
from typing import Any, Dict

import torch
from torch.utils.data import DataLoader, TensorDataset

from src.config import settings
from src.mlops.store import load_trajectories, registry_path, save_registry_json
from src.models.pinns_rul import PINNModel, ParisLawLoss


def fit_pinn(asset_class: str = "SKF-6208", experimental: bool = False, epochs: int = 80) -> Dict[str, Any]:
    trajs = [t for t in load_trajectories(asset_class) if len(t.points) >= settings.MIN_PINN_POINTS]
    minimum = 2 if experimental else settings.MIN_PINN_TRAJECTORIES
    if len(trajs) < minimum:
        return {
            "ok": False,
            "reason": (
                f"Need ≥{minimum} trajectories with ≥{settings.MIN_PINN_POINTS} points each for {asset_class}; "
                f"have {len(trajs)}. PINN stays off until run-to-failure curves exist."
            ),
            "n_trajectories": len(trajs),
        }

    xs = []
    ys = []
    for traj in trajs:
        for p in traj.points:
            xs.append([
                p.imu_acceleration / 10.0,
                p.temp_compressor / 100.0,
                p.run_hours / 10000.0,
                p.rpm / 3000.0,
                p.em_machine_load / 100.0,
            ])
            ys.append([max(p.true_rul_hours, 0.0) / 1000.0])

    x = torch.tensor(xs, dtype=torch.float32)
    y = torch.tensor(ys, dtype=torch.float32)
    loader = DataLoader(TensorDataset(x, y), batch_size=min(64, len(xs)), shuffle=True)

    model = PINNModel(input_dim=5, hidden_dim=32)
    physics = ParisLawLoss()
    opt = torch.optim.Adam(model.parameters(), lr=1e-3)
    model.train()
    last_loss = 0.0
    for _ in range(epochs):
        for xb, yb in loader:
            opt.zero_grad()
            pred = model(xb)
            data_loss = torch.mean((pred - yb) ** 2)
            phys_loss = physics(pred.squeeze(-1), xb[:, 0])
            loss = data_loss + 0.05 * phys_loss
            loss.backward()
            opt.step()
            last_loss = float(loss.item())

    path = registry_path("pinn.pt")
    torch.save({"state_dict": model.state_dict(), "asset_class": asset_class}, path)
    meta = {
        "version": f"pinn-{asset_class}-{datetime.now(timezone.utc).strftime('%Y%m%d')}",
        "asset_class": asset_class,
        "n_trajectories": len(trajs),
        "n_points": len(xs),
        "final_loss": round(last_loss, 6),
        "production_grade": len(trajs) >= settings.MIN_PINN_TRAJECTORIES,
        "experimental": experimental,
    }
    save_registry_json("pinn.json", meta)
    return {"ok": True, "path": str(path), **meta}
