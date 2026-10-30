"""Download the three public benchmark datasets into ENGINE_DATA_DIR (idempotent, stdlib only).

Sources are the datasets' own public releases (licenses verified in docs/specs/classification-engine.md):
CLINC150 (clinc/oos-eval), BANKING77 (PolyAI task-specific-datasets), MASSIVE 1.0 (Amazon).
"""

from __future__ import annotations

import tarfile
import urllib.request
from pathlib import Path

from engine.data import DATA_DIR

GH = "https://raw.githubusercontent.com"
FILES = {
    "clinc/data_full.json": f"{GH}/clinc/oos-eval/master/data/data_full.json",
    "clinc/domains.json": f"{GH}/clinc/oos-eval/master/data/domains.json",
    "banking77/train.csv": f"{GH}/PolyAI-LDN/task-specific-datasets/master/banking_data/train.csv",
    "banking77/test.csv": f"{GH}/PolyAI-LDN/task-specific-datasets/master/banking_data/test.csv",
}
MASSIVE = "https://amazon-massive-nlu-dataset.s3.amazonaws.com/amazon-massive-dataset-1.0.tar.gz"


def _get(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    urllib.request.urlretrieve(url, dest)  # noqa: S310 (fixed https URLs above, no user input)


def main() -> None:
    for rel, url in FILES.items():
        if not (DATA_DIR / rel).exists():
            _get(url, DATA_DIR / rel)
    if not (DATA_DIR / "massive" / "1.0" / "data").exists():
        tgz = DATA_DIR / "massive" / "m.tar.gz"
        _get(MASSIVE, tgz)
        with tarfile.open(tgz) as t:
            t.extractall(DATA_DIR / "massive", filter="data")
    print("data ready in", DATA_DIR)


if __name__ == "__main__":
    main()
