import os
import logging
import shutil
from collections import defaultdict
from osgeo import gdal, osr

# Setup logger
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s") # Set to INFO for more verbosity
logger = logging.getLogger(__name__) # Logger for this script

def analyze_rasters(files):
    """Analyze rasters to determine projection and average resolution"""
    logger.info("Analyzing rasters to determine optimal mosaic parameters...")
    proj_counts = defaultdict(int) # Count occurrences of each EPSG code
    x_res_list = [] # List to store x resolutions
    y_res_list = [] # List to store y resolutions

    for f in files: # Loop through each file
        ds = gdal.Open(f) # Open the raster file
        if ds is None: # Check if the file was opened successfully
            logger.warning(f"Cannot open {f} for analysis") # Log a warning
            continue # Skip to the next file

        # Projection
        srs = osr.SpatialReference() # Create spatial reference object
        srs.ImportFromWkt(ds.GetProjection()) # Import projection from the dataset
        if srs.IsProjected(): # Check if the projection is projected
            epsg = srs.GetAuthorityCode(None) # Get EPSG code
            proj_counts[epsg] += 1 # Increment count for this EPSG code

        # Resolution 
        gt = ds.GetGeoTransform() # Get geo-transform
        x_res_list.append(abs(gt[1])) # Pixel width
        y_res_list.append(abs(gt[5])) # Pixel height (usually negative, so take abs)
        ds = None # Close the dataset

    # Most common projection
    if not proj_counts:
        logger.warning("No projections found. Defaulting to EPSG:4326")
        target_epsg = "EPSG:4326"

    else:
        # GDAL requires one target CRS.  Use the CRS shared by the largest
        # number of inputs, then reproject the remaining rasters to it.
        target_epsg = f"EPSG:{max(proj_counts, key=proj_counts.get)}"

    # Average resolution - check if lists are empty to avoid ZeroDivisionError
    if not x_res_list or not y_res_list: # Check if resolution lists are empty
        logger.error("Could not determine average resolution from any input files.") # Log an error
        return target_epsg, None, None # Indicate failure to calculate resolution
    avg_x_res = sum(x_res_list) / float(len(x_res_list)) # division by float to ensure float result
    avg_y_res = sum(y_res_list) / float(len(y_res_list)) # Use float for division to ensure float result

    logger.info(f"Target EPSG: {target_epsg}, Avg Res: {avg_x_res}, {avg_y_res}") # Log the results
    return target_epsg, avg_x_res, avg_y_res # Return the results

def build_overviews(filepath, overview_levels=[2, 4, 8, 16, 32], resampling_method='nearest'):
    """
    Builds raster pyramid overviews for a given GeoTIFF file.

    Args:
        filepath (str): Path to the GeoTIFF file.
        overview_levels (list): List of integers representing the downsampling factors
                                for each overview level. Default is [2, 4, 8, 16, 32].
        resampling_method (str): Resampling method to use for overview creation.
                                 Common options include 'average', 'nearest', 'cubic', 'mode',
                                 'lanczos'. Default is 'nearest'.
    """
    logger.info(f"Building overviews for {filepath} using levels {overview_levels} with {resampling_method} resampling...")
    try:
        # Perform Raster Pyramid with Internal Pyramid
        ds = gdal.Open(filepath, gdal.GA_ReadOnly) # Open the file in ovr for open file in ArcGIS
        if ds is None: # Check if the file was opened successfully
            logger.error(f"Cannot open {filepath} to build overviews.") # Log an error
            return # Exit the function

        # Build overviews
        ds.BuildOverviews(resampling_method, overview_levels) # Build the overviews with specified levels and method
        ds = None # Close the dataset 

        logger.info(f"Successfully built overviews for {filepath}") # Log success
    except Exception as e: # Catch any exceptions
        logger.error(f"Failed to build overviews for {filepath}: {e}") # Log the error

def find_raster_files(directory):
    """Return GeoTIFF files directly inside *directory*, case-insensitively."""
    return [
        entry.path
        for entry in os.scandir(directory)
        if entry.is_file() and entry.name.lower().endswith(('.tif', '.tiff'))
    ]

def main():
    # Configure input and output directories/paths
    root_dir = r'Raster_Pyramid_Output' # This is now the parent directory containing subfolders
    output_dir = r'Raster_Mosaic' # Output directory for mosaics

    if not os.path.isdir(root_dir):
        logger.error(f"Input directory '{root_dir}' does not exist. Exiting.")
        return

    # Find raster-containing directories at the root or anywhere below it.
    # This supports both a flat input folder and an input folder organized into
    # one or more subfolders.  The set prevents a directory being processed twice.
    processing_dirs = []
    for current_dir, _, _ in os.walk(root_dir):
        if find_raster_files(current_dir):
            processing_dirs.append(current_dir)

    processing_dirs.sort()
    if processing_dirs:
        logger.info(f"Found raster files in {len(processing_dirs)} directory/directories under '{root_dir}'.")

    if not processing_dirs: # If no directories to process were found
        logger.warning(f"No subfolders or raster files found in '{root_dir}'. Exiting.") # Log a warning
        return # Exit the script

    multiple_mosaics = len(processing_dirs) > 1
    os.makedirs(output_dir, exist_ok=True)

    for processing_path in processing_dirs: # Loop through each processing directory
        relative_dir = os.path.relpath(processing_path, root_dir)
        dir_name = 'root' if relative_dir == '.' else relative_dir.replace(os.sep, '_')
        logger.info(f"\n--- Processing directory: {dir_name} ---") # Log the current directory being processed

        # Define output path for the current subfolder's mosaic
        final_output_filename = (
            f"Carbon_Stock_2025_TH_{dir_name}.tif"
            if multiple_mosaics else "Carbon_Stock_2025_TH.tif"
        )
        final_output_path = os.path.join(output_dir, final_output_filename)

        # Find all raster files within the current processing directory
        raster_files = find_raster_files(processing_path)

        if not raster_files:
            logger.warning(f"No raster files found in {processing_path}. Skipping.") # Log a warning
            continue # Skip to the next directory

        logger.info(f"Found {len(raster_files)} raster files in {dir_name} to process") # Log the number of raster files found

        # Analyze rasters for the current directory
        target_epsg, x_res, y_res = analyze_rasters(raster_files) # Analyze rasters to get target EPSG and resolutions
        if x_res is None or y_res is None: # Check if resolution analysis failed
            logger.error(f"Failed to determine average resolution for {dir_name}. Skipping this directory.") # Log an error
            continue # Skip to the next directory

        # Create a single temporary directory for this processing task 
        reprojected_temp_dir = os.path.join(output_dir, f"temp_{dir_name}_reprojected") # Temp directory for reprojected files
        os.makedirs(reprojected_temp_dir, exist_ok=True) # Create the temp directory

        # Reproject all rasters for the current directory
        all_reprojected = [] # List to hold paths of reprojected rasters
        for i, raster_file in enumerate(raster_files): # Loop through each raster file
            try:
                ds = gdal.Open(raster_file)
                if ds is None:
                    logger.warning(f"Cannot open {raster_file}. Skipping.")
                    continue

                srs = osr.SpatialReference()
                srs.ImportFromWkt(ds.GetProjection())
                source_epsg = srs.GetAuthorityCode(None)
                source_band = ds.GetRasterBand(1)
                if source_band is None:
                    logger.warning(f"Cannot read the first band of {raster_file}. Skipping.")
                    ds = None
                    continue
                source_data_type = source_band.DataType
                source_data_type_name = gdal.GetDataTypeName(source_data_type)
                ds = None

                # Reprojection is unnecessary only when this raster already
                # uses the target CRS selected for the current mosaic.
                if f"EPSG:{source_epsg}" == target_epsg:
                    logger.info(f"{os.path.basename(raster_file)} is already in {target_epsg}; skipping reprojection.")
                    all_reprojected.append(raster_file)
                    continue

                base_name = os.path.basename(raster_file)
                reprojected_path = os.path.join(reprojected_temp_dir, f"reproj_{i}_{base_name}")

                logger.info(
                    f"Reprojecting {base_name} to {target_epsg} with resolution {x_res}, {y_res}, "
                    f"aligned pixels, and {source_data_type_name} output"
                )
                warp_options = gdal.WarpOptions(
                    dstSRS=target_epsg,
                    xRes=x_res,
                    yRes=y_res,
                    targetAlignedPixels=True, # Ensure pixels are aligned to the resolution grid for reprojection
                    resampleAlg='near',
                    srcNodata=0, # Assuming 0 is nodata in source
                    dstNodata=0, # Set nodata in output
                    outputType=source_data_type, # Preserve the source raster data type
                    creationOptions=['TILED=YES', 'COMPRESS=LZW', 'BIGTIFF=YES'], # Creation options
                    errorThreshold=0.0 # Set error threshold to 0.0 for strict reprojection
                )
                ds = gdal.Warp(reprojected_path, raster_file, options=warp_options) # Perform the reprojection
                if ds is None: # Check if the warp operation was successful
                    logger.error(f"gdal.Warp failed for {raster_file} and returned None.") # Log an error
                else: # If successful
                    all_reprojected.append(reprojected_path) # Add reprojected file to the list
                ds = None # Close the dataset
  
            except Exception as e: # Catch any exceptions
                logger.error(f"Failed to reproject {raster_file}: {e}") # Log the error
                continue # Skip to the next file
 
        if not all_reprojected: # Check if any rasters were successfully reprojected
            logger.error(f"No rasters were successfully processed for directory {dir_name}!") # Log an error
            # Clean up the temp directory that was created for this failed task
            try:
                shutil.rmtree(reprojected_temp_dir) # Remove the temporary directory
                logger.info(f"Removed temporary directory for failed task: {reprojected_temp_dir}") # Log the cleanup
            except Exception as e: # Catch any exceptions during cleanup
                logger.warning(f"Could not remove temporary directory {reprojected_temp_dir}: {e}") # Log a warning
            continue # Skip to the next directory

        # Build VRT for the current directory's processed files
        vrt_path = os.path.join(reprojected_temp_dir, f'aligned_mosaic_{dir_name}.vrt') # Path for the VRT file
        logger.info(f"Building VRT for {dir_name} from processed files...") # Log VRT building
        vrt = gdal.BuildVRT( # Build the VRT
            vrt_path, # Path to save the VRT
            all_reprojected, # List of reprojected files
            options=gdal.BuildVRTOptions( # VRT build options
                resampleAlg='nearest', # 'nearest' is correct for BuildVRTOptions
                addAlpha=False, # Do not add alpha band
                separate=False, # Do not separate bands
                srcNodata=0, # Assuming 0 is nodata in source
                VRTNodata=0 # Set nodata in VRT
            )
        )
        if vrt is None:# Check if VRT was built successfully
            logger.error(f"Failed to build VRT for {dir_name}") # Log an error
            # Clean up before skipping
            try:
                shutil.rmtree(reprojected_temp_dir) # Remove the temporary directory
            except Exception as e: # Catch any exceptions during cleanup
                logger.warning(f"Could not remove temporary directory {reprojected_temp_dir} after VRT failure: {e}") # Log a warning
            continue # Skip to the next directory
        vrt = None # Close the VRT dataset

        # Translate VRT to final GeoTIFF for the current directory
        logger.info(f"Creating final mosaic for {dir_name}...") # Log final mosaic creation
        gdal.Translate( # Translate VRT to GeoTIFF
            final_output_path, # Output path for the final mosaic
            vrt_path, # Input VRT file
            options=gdal.TranslateOptions( # Translation options
                format='GTiff', # Output format
                creationOptions=['TILED=YES', 'COMPRESS=LZW', 'BIGTIFF=YES', 'PREDICTOR=2'] # Creation options
            )
        )
        logger.info(f"Final mosaic for {dir_name} saved to: {final_output_path}") # Log the output path

        # Build overviews for the final mosaic
        build_overviews(final_output_path) # Build overviews for the final mosaic

        # Clean up temporary files for the current directory
        if os.path.exists(final_output_path): # Check if the final output was created successfully
            logger.info(f"Cleaning up temporary files for {dir_name}...") # Log cleanup
            try:
                shutil.rmtree(reprojected_temp_dir) # Remove the temporary directory
                logger.info(f"Successfully removed temporary directory: {reprojected_temp_dir}") # Log success message
            except Exception as e: # Catch any exceptions during cleanup
                logger.warning(f"Failed to remove temporary directory {reprojected_temp_dir}: {e}") # Log a warning

        logger.info(f"Mosaic creation for {dir_name} complete!") # Log completion of the current directory

    logger.info("All processing complete!") # Log overall completion

if __name__ == "__main__": # Run the main function if this script is executed
    main() # Run the main function
