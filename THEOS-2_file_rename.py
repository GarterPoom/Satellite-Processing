import os
import shutil
import logging
from pathlib import Path

# ======== USER SETTINGS ========

# Root folder to search in (contains multiple subfolders)
root_folder = r"F:\\THEOS-2_Raw\\INT_69_113_THA_PATTANI_YALA_FL_20251217"

# Destination folder to copy renamed files to
destination_folder = r"THEOS-2"

# Make Sure Directories Exist
os.makedirs(destination_folder, exist_ok=True)

# Suffix you want to add
custom_suffix = "INT_69_113_THA_PATTANI_YALA_FL"

# =================================

# --- Logging Setup ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] - %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)


def rename_and_copy_tifs(root_dir: str, dest_dir: str, suffix: str):
    """
    Recursively finds TIFF files starting with 'IMG_T2V' in a root directory,
    renames them with a suffix and a counter, and copies them to a destination directory.

    Args:
        root_dir (str): The root folder to search for TIFF files.
        dest_dir (str): The destination folder to save renamed files.
        suffix (str): A custom suffix to add to the filenames.
    """
    root_path = Path(root_dir)
    dest_path = Path(dest_dir)

    if not root_path.exists() or not root_path.is_dir():
        logger.error(f"Root directory not found: {root_path}")
        return

    # Ensure destination folder exists
    dest_path.mkdir(parents=True, exist_ok=True)
    logger.info(f"Output will be saved to: {dest_path}")

    # Use rglob to efficiently find all .tif files recursively. This is a generator.
    tif_files = [p for p in root_path.rglob('*.tif') if p.name.startswith("IMG_T2V")]

    if not tif_files:
        logger.warning(f"No TIFF files starting with 'IMG_T2V' found in {root_path}.")
        return

    logger.info(f"Found {len(tif_files)} files to process.")

    for i, old_path in enumerate(tif_files, 1):
        # New filename format: original_base + _suffix + _counter.ext
        new_name = f"{old_path.stem}_{suffix}_{i}{old_path.suffix}"
        new_path = dest_path / new_name

        # Copy and rename file
        shutil.copy2(old_path, new_path)
        logger.info(f"Copied: {old_path} -> {new_path}")

    logger.info(f"\n✅ Completed! Total {len(tif_files)} files processed.")

if __name__ == "__main__":
    rename_and_copy_tifs(root_folder, destination_folder, custom_suffix)
