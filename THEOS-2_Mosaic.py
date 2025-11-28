import os
import glob
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
        target_epsg = "EPSG:32647" & "EPSG:32648" # Use EPSG:32647 and 32648 as default which is WGS84 UTM Zone 47 and 48 North (UTM Zone 47N and 48N) Coverage Thailand.

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

def main():
    # Configure input and output directories/paths
    root_dir = r'THEOS-2_Raster_Pyramid_Output' # This is now the parent directory containing subfolders
    output_dir = r'Raster_Mosaic' # Output directory for mosaics

    # Determine processing directories.
    # First, look for subdirectories.
    subfolders = [f.path for f in os.scandir(root_dir) if f.is_dir()] # List of subdirectories

    processing_dirs = [] # List to hold directories to process
    if subfolders: # Check if subfolders were found
        # Case 1: Subfolders exist, so we'll process each one.
        processing_dirs = subfolders # Use subfolders as processing directories
        logger.info(f"Found {len(subfolders)} subfolders to process in '{root_dir}'.") # Log the number of subfolders
        
    else:
        # Case 2: No subfolders. Check for raster files in the root directory.
        raster_files_in_root = glob.glob(os.path.join(root_dir, '*.tif')) + \
                               glob.glob(os.path.join(root_dir, '*.tiff')) # List of raster files in root
        if raster_files_in_root:
            # If rasters are present, the root directory itself is the single item to process.
            processing_dirs = [root_dir] # Process the root directory
            logger.info(f"No subfolders found. Processing raster files directly in '{root_dir}'.") # Log this case

    if not processing_dirs: # If no directories to process were found
        logger.warning(f"No subfolders or raster files found in '{root_dir}'. Exiting.") # Log a warning
        return # Exit the script

    for processing_path in processing_dirs: # Loop through each processing directory
        dir_name = os.path.basename(processing_path) # Get the directory name
        logger.info(f"\n--- Processing directory: {dir_name} ---") # Log the current directory being processed

        # Define output path for the current subfolder's mosaic
        final_output_filename = "FL_20251126_TH2_20251126_1041_ENT.tif" #f"{dir_name}_Mosaic.tif"
        final_output_path = os.path.join(output_dir, final_output_filename)

        # Find all raster files within the current processing directory
        raster_files = glob.glob(os.path.join(processing_path, '*.tif')) + \
                       glob.glob(os.path.join(processing_path, '*.tiff')) # List of raster files

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
                ds = None

                # If projection of raster is EPSG:4326, EPSG:32647 or EPSG:32648, just skipping reprojection
                if source_epsg in ("4326", "32647" "32648"):
                    logger.info(f"{os.path.basename(raster_file)} is in a supported CRS (EPSG:{source_epsg}), skipping reprojection.")
                    all_reprojected.append(raster_file)
                    continue

                base_name = os.path.basename(raster_file)
                reprojected_path = os.path.join(reprojected_temp_dir, f"reproj_{i}_{base_name}")

                logger.info(f"Reprojecting {base_name} to {target_epsg} with resolution {x_res}, {y_res} and aligned pixels")
                warp_options = gdal.WarpOptions(
                    dstSRS=target_epsg,
                    xRes=x_res,
                    yRes=y_res,
                    targetAlignedPixels=True, # Ensure pixels are aligned to the resolution grid for reprojection
                    resampleAlg='near',
                    srcNodata=0, # Assuming 0 is nodata in source
                    dstNodata=0, # Set nodata in output
                    outputType=gdal.GDT_UInt16, # Use UInt16 for output data type
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