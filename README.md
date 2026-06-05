# Satellite Imagery Processing Toolkit

A collection of Python scripts for processing satellite imagery and other raster/vector geospatial datasets. The toolkit covers the full pipeline: file ingestion, reprojection, mosaicking, clipping by administrative boundaries, brightness normalization, band composition, polygonization, pyramid overview generation, and flood mapping from Sentinel-1 SAR data.

The scripts are built on top of GDAL, Rasterio, GeoPandas, and Shapely. The default target projection is **EPSG:32647** (WGS 84 / UTM Zone 47N), tuned for Thailand coverage; many scripts also accept EPSG:32648 and EPSG:4326 as supported inputs.

## Prerequisites

The geospatial stack (GDAL, Rasterio, Fiona, GeoPandas) is easiest to install with Conda.

1. **Install Conda** (Miniconda or Anaconda).

2. **Create an environment**:
    ```bash
    conda create -n geo_env python=3.10
    conda activate geo_env
    ```

3. **Install dependencies**:
    ```bash
    conda install -c conda-forge gdal rasterio geopandas fiona shapely numpy scipy scikit-image pillow tqdm
    # For Earth Engine scripts only:
    pip install earthengine-api geemap
    ```

## Repository Layout

| Type | Path / Pattern | Purpose |
|------|---------------|---------|
| Input | `THEOS-2/`, `LANDSAT_9/`, `Sentinel-2/`, `Raster_Image/` | Source raster folders |
| Intermediate | `THEOS-2_Raster_Pyramid_Output/`, `Raster_Resample/`, `Raster_Composite/` | Per-stage outputs |
| Final | `Raster_Mosaic/`, `Raster_Mosaic_JPEG/`, `Clipped_Rasters/`, `Polygonized_Rasters/`, `GeoJSON_Polygons/` | End-products |
| Boundaries | `Thailand/*.shp` | Administrative shapefiles used for clipping |

## Scripts

### Mosaicking

#### `raster_mosiac.py`
Generic raster mosaicker. Walks a root directory, analyzes each subfolder of GeoTIFFs to choose a target CRS and average resolution, reprojects with `gdal.Warp` (skipping rasters already in EPSG:4326/32647/32648), builds a VRT, translates it to a compressed LZW GeoTIFF named `<subfolder>_Mosaic.tif`, and adds internal pyramid overviews.

#### `THEOS-2_Mosaic.py`
THEOS-2 specific variant of the mosaic pipeline. Defaults: input `THEOS-2_Raster_Pyramid_Output/`, output `Raster_Mosaic/`. Forces `UInt16` output, nodata = 0, and `targetAlignedPixels=True` during reprojection.

#### `raster_mosaic_by_orbit.py`
Groups rasters by acquisition date and orbit ID parsed from the filename (regex `_YYYYMMDD_RNNN_`), then mosaics each `(date, orbit)` group into `Raster_Mosaic/<date>_<orbit>_Mosaic.tif`.

#### `canopy_raster_mosiac.py`
Mosaic pipeline tuned for canopy / Sentinel-2 datasets. Performs a shapefile-driven overlap test using a cleaned cutline (dissolved + `buffer(0)` to fix invalid geometry) and only mosaics rasters that intersect the AOI. Output: `<subfolder>_Canopy_Mosaic.tif`.

#### `raster_mosiac_JPEG.py`
Produces small JPEG previews from per-day folders of TIFFs (path structure `.../YYYY/MM/DD`). Reprojects with `gdalwarp`, builds a VRT, downscales (default 5%) and writes a `YYYYMMDD_Preview.jpeg` next to the source TIFFs. Skips folders that already have a preview.

### Pyramid Overviews

#### `raster_pyramid.py`
Recursively finds `*.tif` files under `THEOS-2/` and builds external `.ovr` overviews (`[2, 4, 8, 16, 32]`, `nearest`). Copies the source file into `Raster_Pyramid_Output/` first so the originals stay untouched.

#### `THEOS-2_raster_pyramid.py`
Same logic as `raster_pyramid.py` but with output dir `THEOS-2_Raster_Pyramid_Output/` and special handling for Windows "disk full" errors (`winerror 112`).

### Clipping

#### `clip_raster.py`
Clips every raster in a folder to a single shapefile cutline using `gdal.Warp` with `cropToCutline=True` and `dstAlpha=True`. Detects and propagates the source NoData value. Builds internal overviews after each clip.

#### `cliping_raster.py`
Hierarchical clipper using a Thailand administrative shapefile (province / district / subdistrict — attributes `PV_TN`, `AP_TN`, `TB_TN`). For each group key (e.g. `TB_IDN`), clips every raster, builds a hierarchical output folder structure, attaches overviews and copies the result to its final path using `rasterio.shutil.copy`. Supports toggling district / subdistrict inclusion in the output path.

#### `clip_raster_polygonise.py`
Convenience wrapper that runs `raster_multiply.main()` → `cliping_raster.main()` → `polygonise_GeoJSON.main()` in sequence.

### Polygonization

#### `polygonise_GeoJSON.py`
Recursively walks an input directory of clipped rasters and produces a **binary** polygon mask per file (valid vs nodata). Saves both Shapefiles and GeoJSON, mirroring the input directory tree.

#### `polygonise_GeoJSON_pixel.py`
Same as above but preserves the **actual pixel value** in each output polygon instead of collapsing to a binary mask — useful when each integer pixel value represents a class.

### Radiometric / Band Operations

#### `raster_multiply.py`
Multiplies every pixel of every raster in a directory by a constant factor (default `0.47` against `LANDSAT_9/`). Promotes data to `float32`, writes outputs with the `_multiplied` suffix into `LANDSAT_9_Multiplied/`, and builds Rasterio overviews. Handles `.tif`, `.tiff`, `.img`, `.jp2`, `.envi`, `.hdr`, `.dat`.

#### `raster_brightness_normalize.py`
Per-band min/max normalization to 8-bit (0–255), respecting NoData. Walks subfolders under `Raster_แม่น้ำโขง/`, writes normalized rasters to `Raster_Mosaic/<subfolder>/`, and builds pyramids.

#### `LANDSAT_COMPOSITE.py`
Creates a 3-band composite (default bands 7, 5, 4 → SWIR/NIR/Red, false-color) from Landsat scenes in `LANDSAT_9/`. Output suffix `_Com754.tif` in `Raster_Composite/`.

#### `raster_image_resample.py`
Sentinel-2 JP2 pipeline: resamples every band to 10 m with `gdal.Translate`, stacks bands B02–B12 (excluding SCL) into a single multi-band GeoTIFF using a VRT, sets band descriptions, exports the SCL layer separately, and builds overviews. Input `Raster_Image/`, output `Raster_Resample/`, SCL in `SCL_Classified/`. Logs to `sentinel_processing.log`.

#### `raster_chop.py`
Tiles every raster under an input directory into fixed-size sub-rasters (default `512x512`). Preserves geotransform and CRS per tile; output names encode the source path and tile row/col.

### File Management

#### `THEOS-2_file_rename.py`
Recursively finds `IMG_T2V*.tif` files under a THEOS-2 raw folder, renames each with a custom suffix and counter (`<stem>_<suffix>_<i>.tif`), and copies into the destination folder.

#### `shapefile_merge.py`
Walks a directory of shapefiles, reprojects each to EPSG:4326, and concatenates them into one merged shapefile.

#### `shape_union.py`
Performs an `overlay(..., how='union')` between every shapefile in an input tree and a single base shapefile. Preserves the input directory structure in the output, reprojecting on the fly when CRSes don't match.

### Flood Mapping

#### `flood_mapping.py`
CLI tool for local Sentinel-1 GRD flood mapping. Stacks pre- and during-event scenes (VV and/or VH), reprojects everything to a reference grid, applies a median speckle filter, computes a change image (`pre − dur` in dB), then thresholds with either fixed values or Otsu. Writes `flood_mask.tif`, `change_max_drop_db.tif`, `during_mean_db.tif`, and a parameter log.

Example:
```bash
python flood_mapping.py \
  --pre-dir D:/data/s1/pre --dur-dir D:/data/s1/during --out-dir D:/data/s1/out \
  --bands VV,VH --units db --change-thresh-db 1.5 --dur-max-db -13 --speckle-kernel 3
```

#### `GEE_Flood_Mapping.py`
Google Earth Engine notebook-style script for flood mapping over Central Thailand using Sentinel-1 VV backscatter. Compares pre- and post-flood backscatter, applies a fixed threshold, masks JRC permanent-water pixels, and visualizes the result via `geemap`. Requires `ee.Authenticate()` and a valid GEE project ID.

## Usage Pattern

Most scripts are configured by editing constants in `main()` rather than via CLI flags. The typical workflow is:

1. Drop source rasters into the expected input folder (e.g. `THEOS-2/`, `LANDSAT_9/`).
2. Open the script and update the `root_dir` / `output_dir` / shapefile paths to match your data.
3. Run from the project root:
   ```bash
   python THEOS-2_Mosaic.py
   ```
4. Pipeline scripts like `clip_raster_polygonise.py` chain several stages together; review the constants in each sub-script before running.

`flood_mapping.py` is the only script with a real argparse CLI.

## Notes

* Default output creation options across the toolkit: `TILED=YES`, `COMPRESS=LZW`, `BIGTIFF=YES`, often `PREDICTOR=2` for the final translate step.
* Default pyramid levels: `[2, 4, 8, 16, 32]` with `nearest` resampling.
* `targetAlignedPixels=True` is used during reprojection to keep mosaic tiles on a consistent pixel grid.
* The toolkit assumes Thailand-centric data; if you work elsewhere, change `EPSG:32647` and the shapefile paths accordingly.

---

*This README is a guide; paths, parameters, and pipeline order should be adjusted to fit your specific data and workflow.*
