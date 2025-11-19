# Satellite Imagery Processing Toolkit

This repository contains a collection of Python scripts designed for processing satellite imagery and other raster datasets. The tools automate common geospatial tasks such as mosaicking multiple images into a single file and performing pixel-wise arithmetic operations.

These scripts are built using powerful open-source libraries like GDAL and Rasterio, ensuring efficient and reliable processing of large raster files.

## Features

*   **Automated Raster Mosaicking (`raster_mosiac.py`)**:
    *   Scans a root directory for subfolders containing raster files (`.tif`, `.tiff`).
    *   Analyzes rasters to determine the optimal projection (defaults to EPSG:32647) and average resolution for the mosaic.
    *   Reprojects individual rasters to a consistent coordinate reference system (CRS) and resolution.
    *   Skips reprojection for rasters already in a supported CRS (EPSG:4326, EPSG:32647, EPSG:32648) to improve efficiency.
    *   Builds a Virtual Raster (VRT) to handle the collection of processed images.
    *   Translates the VRT into a final, single GeoTIFF mosaic file.
    *   Automatically builds pyramid overviews for the final mosaic to optimize performance in GIS software.
    *   Manages temporary files and cleans up upon successful completion.

*   **Raster Pixel Multiplication (`raster_multiply.py`)**:
    *   Processes all supported raster files in a specified directory.
    *   Multiplies the pixel values of each raster by a user-defined factor.
    *   Handles various raster formats (e.g., `.tif`, `.tiff`, `.img`, `.jp2`).
    *   Saves the processed rasters to a designated output directory with a custom suffix.
    *   Builds pyramid overviews for each new raster file created.

## Prerequisites

Before running these scripts, you need to have Python and the necessary libraries installed. The primary dependencies are GDAL and Rasterio, which can be challenging to install. Using a package manager like Conda is highly recommended.

1.  **Install Conda**: If you don't have it, install Miniconda or Anaconda.

2.  **Create a Conda Environment**:
    ```bash
    conda create -n geo_env python=3.9
    conda activate geo_env
    ```

3.  **Install Dependencies**:
    ```bash
    conda install -c conda-forge gdal rasterio numpy
    ```

## Usage

1.  Clone or download this repository.
2.  Place your raster data in the appropriate folder structure as described below.
3.  Modify the configuration sections within each script to point to your data directories and set desired parameters.
4.  Run the scripts from your terminal.

### `raster_mosiac.py`

This script is designed to create a single mosaic from raster files located in one or more subdirectories.

**Configuration:**
Open `raster_mosiac.py` and modify these variables in the `main()` function:
*   `root_dir`: The parent directory containing subfolders of raster images (e.g., `r'THEOS-2'`). The script will create one mosaic per subfolder.
*   `output_dir`: The directory where the final mosaics will be saved (e.g., `r'Raster_Mosaic'`).

**Execution:**
```bash
python "d:\Satellite Processing\raster_mosiac.py"
```
The script will generate a `_Mosaic.tif` file for each subfolder found in `root_dir`.

### `raster_multiply.py`

This script multiplies the pixel values of raster files in a directory by a constant factor. This is useful for radiometric correction or unit conversion.

**Configuration:**
Open `raster_multiply.py` and modify these variables in the `main()` function:
*   `root_directory`: The directory containing the raster files you want to process (e.g., `r'LANDSAT_9'`).
*   `output_dir_path`: The directory where the multiplied rasters will be saved (e.g., `'LANDSAT_9_Multiplied'`).
*   `multiplication_factor`: The floating-point number to multiply pixel values by (e.g., `0.47`).

**Execution:**
```bash
python "d:\Satellite Processing\raster_multiply.py"
```
The script will create new raster files with the `_multiplied` suffix in the specified output directory.

---

*This README provides a general guide. You may need to adjust paths, parameters, and logic within the scripts to fit your specific data and workflow.*