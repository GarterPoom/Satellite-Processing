import os
import glob
import rasterio
from rasterio.enums import Resampling
from PIL import Image
import numpy as np
import subprocess
import shutil

# ================= CONFIG =================

# Point to the Month folder (The parent of the day folders)
root_dir = r"Y:\Burnscar2025_B\96\2025\12"

target_epsg = 32647
jpeg_scale_factor = 0.05  # downscale to 5%

# ================= FUNCTION DEFINITION =================

def process_day_folder(tif_dir):
    """
    Processes a single day folder: Reproject -> VRT -> JPEG -> Cleanup
    Returns True if processed, False if skipped.
    """
    
    # 1. Extract date string from folder path for naming
    # Assumption: Path ends in .../Year/Month/Day
    parts = os.path.normpath(tif_dir).split(os.sep)
    if len(parts) < 3:
        print(f"Skipping {tif_dir}: Path structure too short.")
        return False
        
    year, month, day = parts[-3], parts[-2], parts[-1]
    date_str = f"{year}{month}{day}"
    
    jpeg_filename = f"{date_str}_Preview.jpeg"
    jpeg_path = os.path.join(tif_dir, jpeg_filename)

    # 2. CHECK: Does JPEG already exist?
    if os.path.exists(jpeg_path):
        print(f"[-] JPEG found in {day}, skipping processing.")
        return False

    print(f"[+] Processing folder: {day} ...")

    # 3. Find TIFFs
    tif_list = sorted(glob.glob(os.path.join(tif_dir, "*.tif")))
    if len(tif_list) == 0:
        print(f"    ! No TIFFs found in {day}, skipping.")
        return False

    # 4. Setup temporary paths
    reproj_dir = os.path.join(tif_dir, "reprojected")
    vrt_path = os.path.join(tif_dir, "mosaic.vrt")
    os.makedirs(reproj_dir, exist_ok=True)

    try:
        # 5. REPROJECT TIFFs
        reproj_tifs = []
        for tif in tif_list:
            out_tif = os.path.join(reproj_dir, os.path.basename(tif))
            
            # Only reproject if the reprojected file doesn't already exist 
            # (Optional optimization, but good for safety)
            if not os.path.exists(out_tif):
                cmd = [
                    "gdalwarp",
                    "-t_srs", f"EPSG:{target_epsg}",
                    "-r", "bilinear",
                    "-overwrite",
                    tif,
                    out_tif
                ]
                # Suppress extensive GDAL output, only show errors
                subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL)
            
            reproj_tifs.append(out_tif)

        # 6. BUILD VRT
        cmd_vrt = ["gdalbuildvrt", vrt_path] + reproj_tifs
        subprocess.run(cmd_vrt, check=True, stdout=subprocess.DEVNULL)

        # 7. OPEN VRT AND CREATE JPEG
        with rasterio.open(vrt_path) as src:
            height, width = src.height, src.width
            new_h = max(1, int(height * jpeg_scale_factor))
            new_w = max(1, int(width * jpeg_scale_factor))
            
            img_uint8 = np.zeros((new_h, new_w, 3), dtype=np.uint8)
            
            # Loop through bands 1, 2, 3
            for b in range(1, 4): 
                if b > src.count: break # Safety check if file has < 3 bands
                
                band_data = src.read(
                    b,
                    out_shape=(new_h, new_w),
                    resampling=Resampling.bilinear
                )
                
                # Normalize to 0-255
                min_val = band_data.min()
                max_val = band_data.max()
                
                if max_val == min_val:
                    band_data = np.zeros_like(band_data, dtype=np.uint8)
                else:
                    # Float calculation then cast to uint8
                    band_data = ((band_data - min_val) / (max_val - min_val) * 255).astype(np.uint8)
                
                img_uint8[:, :, b-1] = band_data

        # Save JPEG
        Image.fromarray(img_uint8).save(jpeg_path, "JPEG", quality=95)
        print(f"    -> Saved: {jpeg_filename}")

    except Exception as e:
        print(f"    ! Error processing {day}: {e}")
    
    finally:
        # 8. CLEANUP
        if os.path.exists(vrt_path):
            os.remove(vrt_path)
        
        if os.path.exists(reproj_dir):
            # Use ignore_errors=True to avoid crashing on permission locks
            shutil.rmtree(reproj_dir, ignore_errors=True)

    return True

# ================= MAIN EXECUTION =================

if __name__ == "__main__":
    # Check if root dir exists
    if not os.path.exists(root_dir):
        print(f"Error: Directory not found: {root_dir}")
    else:
        # Get all items in root_dir
        items = sorted(os.listdir(root_dir))
        
        for item in items:
            full_path = os.path.join(root_dir, item)
            
            # Ensure we are only looking at directories (subfolders)
            if os.path.isdir(full_path):
                process_day_folder(full_path)
                
    print("\nAll processing complete.")