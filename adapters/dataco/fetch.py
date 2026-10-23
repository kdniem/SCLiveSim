"""Download the DataCo Smart Supply Chain dataset from Mendeley Data.

Dataset: Constante, F., Silva, F., Pereira, A. (2019). DataCo SMART SUPPLY CHAIN
FOR BIG DATA ANALYSIS. Mendeley Data, V5. https://doi.org/10.17632/8gx2fvg2k6.5
License: CC BY 4.0. The files are never committed to this repository.

Usage: `uv run python -m adapters.dataco.fetch [target_dir]` (default: data/raw)
"""

import hashlib
import os
import shutil
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path

DATASET_DOI = "10.17632/8gx2fvg2k6.5"
DATASET_VERSION = "5"
LICENSE = "CC BY 4.0"
ATTRIBUTION = (
    "DataCo Smart Supply Chain for Big Data Analysis by Constante, Silva and Pereira "
    f"(2019), Mendeley Data V{DATASET_VERSION}, https://doi.org/{DATASET_DOI}, {LICENSE}"
)
_BASE = "https://data.mendeley.com/public-files/datasets/8gx2fvg2k6/files"
USER_AGENT = "sc-livesim (+https://github.com/kdniem/SCLiveSim)"


@dataclass(frozen=True)
class RemoteFile:
    name: str
    url: str
    sha256: str


MAIN_FILE = RemoteFile(
    "DataCoSupplyChainDataset.csv",
    f"{_BASE}/72784be5-36d3-44fe-b75d-0edbf1999f65/file_downloaded",
    "fa6d022ed437155e1a2f0378710602848703c8a7f203f7ff5d77805bf8480aa6",
)
DESCRIPTION_FILE = RemoteFile(
    "DescriptionDataCoSupplyChain.csv",
    f"{_BASE}/29dc7b05-dda6-4834-8354-5b5cc44430df/file_downloaded",
    "9828e34669bd6d77e3b4463364cc44a5d52446b5e246fc258758cfe592566c4b",
)


def default_raw_dir() -> Path:
    return Path(os.environ.get("LIVESIM_DATA_DIR", "data")) / "raw"


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download(remote: RemoteFile, target_dir: Path) -> Path:
    """Download one file, verify its checksum and return its path."""
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / remote.name
    if target.exists() and sha256_of(target) == remote.sha256:
        return target
    partial = target.with_suffix(".part")
    print(f"Downloading {remote.name} from Mendeley Data ({LICENSE}) ...", flush=True)
    # Mendeley rejects urllib's default User-Agent with 403.
    request = urllib.request.Request(remote.url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response, partial.open("wb") as out:
        shutil.copyfileobj(response, out, length=1 << 20)
    actual = sha256_of(partial)
    if actual != remote.sha256:
        partial.unlink(missing_ok=True)
        raise RuntimeError(f"checksum mismatch for {remote.name}: {actual}")
    partial.replace(target)
    return target


def ensure_dataset(raw_dir: Path | None = None, *, auto_download: bool = True) -> Path:
    """Return the path of the main CSV, downloading it first if allowed."""
    raw_dir = raw_dir or default_raw_dir()
    path = raw_dir / MAIN_FILE.name
    if path.exists():
        return path
    if not auto_download:
        raise FileNotFoundError(
            f"{path} not found. Run `uv run python -m adapters.dataco.fetch` "
            "or see docs/datasets.md."
        )
    print(f"Using {ATTRIBUTION}", flush=True)
    return download(MAIN_FILE, raw_dir)


def main() -> None:
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else default_raw_dir()
    for remote in (MAIN_FILE, DESCRIPTION_FILE):
        print(download(remote, target))
    print(f"License: {ATTRIBUTION}")


if __name__ == "__main__":
    main()
