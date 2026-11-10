import os
import shutil
from pathlib import Path
from kaggle.api.kaggle_api_extended import KaggleApi

KAGGLE_USERNAME = "amiraliaraghi"
API_TOKEN = "KGAT_836740cc96f6d44b36bc678c98af8889"

def download_shards(platform="neshan"):
    os.environ["KAGGLE_API_TOKEN"] = API_TOKEN
    os.environ["KAGGLE_USERNAME"] = KAGGLE_USERNAME

    api = KaggleApi()
    api.authenticate()

    project_root = Path(__file__).resolve().parent.parent
    results_dir = project_root / "data" / "results"
    results_dir.mkdir(parents=True, exist_ok=True)

    for i in range(1, 9):
        kid = f"{KAGGLE_USERNAME}/matchify-{platform}-shard{i}"
        try:
            status_obj = api.kernels_status(kid)
            st = str(status_obj.status)
            print(f"Shard {i} ({kid}): {st}")
            if "COMPLETE" in st:
                td = results_dir / f"tmp_{platform}_{i}"
                td.mkdir(parents=True, exist_ok=True)
                print(f"  Downloading output for Shard {i} to {td}...")
                api.kernels_output(kid, path=str(td))
                
                # Check files in td
                files = list(td.glob("*"))
                print(f"  Downloaded files: {[f.name for f in files]}")
                dbs = list(td.glob("*.db"))
                for db in dbs:
                    dest = results_dir / f"{platform}_shard_{i:02d}.db"
                    shutil.copy2(db, dest)
                    print(f"  ✅ Saved output to: {dest}")
        except Exception as e:
            print(f"  Error checking/downloading Shard {i}: {e}")

if __name__ == "__main__":
    download_shards("neshan")
