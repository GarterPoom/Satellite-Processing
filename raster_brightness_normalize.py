import os
import glob
import logging
import shutil
import tempfile
from collections import defaultdict
from pathlib import Path

import numpy as np
from osgeo import gdal, osr

# --- Configuration ---
# Set the root directory containing subfolders of raster images.
# Each subfolder will be processed into a separate mosaic.
ROOT_DIR = Path("Raster_แม่น้ำโขง")

# Set the directory where the final mosaics will be saved.
OUTPUT_DIR = Path("Raster_Mosaic")

# --- Logging Setup ---
# Configures logging to print informative messages during the process.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] - %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

def normalize_brightness(input_path: str, output_path: str) -> str:
    """
    Normalizes raster pixel values to the 0-255 range (8-bit) for each band,
    while respecting NoData values.

    Args:
        input_path (str): Path to the source raster.
        output_path (str): Path to save the normalized raster.

    Returns:
        str: The path to the created normalized raster, or None if it fails.
    """
    try:
        ds = gdal.Open(input_path)
        if ds is None:
            logger.warning(f"Could not open {input_path} for normalization.")
            return None

        driver = gdal.GetDriverByName('GTiff')
        out_ds = driver.Create(
            output_path,
            ds.RasterXSize,
            ds.RasterYSize,
            ds.RasterCount,
            gdal.GDT_Byte,  # 8-bit output
            options=['TILED=YES', 'COMPRESS=LZW', 'BIGTIFF=YES']
        )
        out_ds.SetGeoTransform(ds.GetGeoTransform())
        out_ds.SetProjection(ds.GetProjection())

        for i in range(1, ds.RasterCount + 1):
            band = ds.GetRasterBand(i)
            nodata_val = band.GetNoDataValue()
            arr = band.ReadAsArray().astype(np.float32)

            if nodata_val is not None:
                # Create a mask for valid data
                valid_data_mask = arr != nodata_val
                # Use the mask to calculate min/max only on valid data
                min_val = np.min(arr[valid_data_mask])
                max_val = np.max(arr[valid_data_mask])
            else:
                # If no nodata value, use the whole array
                valid_data_mask = np.ones_like(arr, dtype=bool)
                min_val = np.min(arr)
                max_val = np.max(arr)

            # Avoid division by zero if the band is a solid color
            if max_val > min_val:
                scaled = 255 * (arr - min_val) / (max_val - min_val)
            else:
                scaled = np.zeros_like(arr)

            # Apply the nodata mask back to the scaled array
            scaled[~valid_data_mask] = 0  # Set nodata pixels to 0 in the output
            
            out_band = out_ds.GetRasterBand(i)
            out_band.WriteArray(scaled.astype(np.uint8))
            out_band.SetNoDataValue(0)  # Set nodata value for the output band
            out_band.FlushCache()

        logger.info(f"Normalized '{os.path.basename(input_path)}'")
        return output_path
    except Exception as e:
        logger.error(f"Failed to normalize {input_path}: {e}")
        return None
    finally:
        ds = None
        out_ds = None

def raster_pyramid(filepath, overview_levels=[2, 4, 8, 16, 32], resampling_method='nearest'):
    """
    Builds raster pyramid overviews for a given GeoTIFF file.

    Args:
        filepath (str): Path to the GeoTIFF file.
        overview_levels (list): List of overview levels to generate. Default is [2, 4, 8, 16, 32].
        resampling_method (str): Resampling method to use. Default is 'nearest'.

    Returns:
        str: Path to the created overview GeoTIFF file.
    """
    try:
        ds = gdal.Open(filepath, gdal.GA_Update)
        if ds is None:
            logger.warning(f"Could not open {filepath} for pyramid creation.")
            return None

        # Create overviews
        ds.BuildOverviews(resampling_method, overview_levels)
        logger.info(f"Created pyramid overviews for '{os.path.basename(filepath)}'")
        return filepath
    except Exception as e:
        logger.error(f"Failed to create pyramid for {filepath}: {e}")
        return None
    finally:
        ds = None

def process_raster_folder(folder_path: Path, output_folder: Path):
    """
    Processes all raster files in a given folder, normalizing brightness and creating pyramids.

    Args:
        folder_path (Path): Path to the folder containing raster files.
        output_folder (Path): Path to the output folder for normalized rasters.
    """
    if not output_folder.exists():
        output_folder.mkdir(parents=True)

    for raster_file in glob.glob(str(folder_path / "*.tif")):
        logger.info(f"Processing {raster_file}...")
        normalized_raster = normalize_brightness(raster_file, output_folder / Path(raster_file).name)
        if normalized_raster:
            raster_pyramid(normalized_raster)

def main():
    """
    Main function to process all subfolders in the ROOT_DIR.
    Each subfolder will be processed into a separate mosaic.
    """
    if not ROOT_DIR.exists():
        logger.error(f"Root directory '{ROOT_DIR}' does not exist.")
        return

    for subfolder in ROOT_DIR.iterdir():
        if subfolder.is_dir():
            output_subfolder = OUTPUT_DIR / subfolder.name
            logger.info(f"Processing folder: {subfolder.name}")
            process_raster_folder(subfolder, output_subfolder)

if __name__ == "__main__":
    main()