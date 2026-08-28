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


def get_nodata_values(dataset, raster_path):
    """Return declared NoData values for every band and log what was found.

    ``None`` means that the corresponding band has no NoData value in its
    metadata.  GDAL can then use the source band's own mask/metadata instead
    of treating a valid value (such as zero) as NoData.
    """
    nodata_values = []
    for band_index in range(1, dataset.RasterCount + 1):
        band = dataset.GetRasterBand(band_index)
        nodata_values.append(band.GetNoDataValue() if band is not None else None)

    formatted_values = ', '.join(
        f'band {band_index}: {value!r}'
        for band_index, value in enumerate(nodata_values, start=1)
    )
    logger.info(
        f"Declared NoData for {os.path.basename(raster_path)}: "
        f"{formatted_values or 'no raster bands'}"
    )
    return nodata_values


def nodata_option_value(nodata_values):
    """Return the GDAL option value for one or more fully defined bands."""
    return nodata_values[0] if len(nodata_values) == 1 else nodata_values


def common_nodata_values(raster_files):
    """Return a per-band NoData value only when every input declares the same one.

    A VRT/GeoTIFF band can advertise only one NoData value.  When inputs use
    different values, leaving VRTNodata unset preserves the NoData metadata of
    each source instead of incorrectly applying one source's value to another.
    """
    common_values = None
    for raster_file in raster_files:
        dataset = gdal.Open(raster_file)
        if dataset is None:
            logger.warning(f"Cannot open {raster_file} to inspect NoData.")
            return None

        values = get_nodata_values(dataset, raster_file)
        dataset = None
        if not values or any(value is None for value in values):
            return None

        if common_values is None:
            common_values = values
        elif values != common_values:
            return None

    return common_values

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
    files = [
        entry.path
        for entry in os.scandir(directory)
        if entry.is_file() and entry.name.lower().endswith(('.tif', '.tiff'))
    ]
    return sorted(files, key=lambda path: path.casefold())


def build_mosaic_filename(raster_files, directory_name, multiple_mosaics):
    """Build a readable, collision-resistant mosaic name from its inputs.

    A single raster keeps its complete stem.  For several rasters, use their
    shared underscore-delimited prefix and replace the varying tile section
    with ``Mosaic``.  If the files have no shared prefix, use the input
    directory name instead.
    """
    stems = [os.path.splitext(os.path.basename(path))[0] for path in raster_files]

    if len(stems) == 1:
        name_parts = [stems[0]]
    else:
        shared_prefix = os.path.commonprefix(stems)
        # Do not leave a partial field such as ``D_0`` in the output name.
        shared_prefix = shared_prefix.rsplit('_', 1)[0].rstrip('_-.')
        name_parts = [shared_prefix or directory_name]

    # Separate input directories may contain identically named raster sets.
    if multiple_mosaics and directory_name != 'root':
        name_parts.append(directory_name)

    return f"{'_'.join(name_parts)}_Mosaic.tif"

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

        # Find all raster files within the current processing directory
        raster_files = find_raster_files(processing_path)

        if not raster_files:
            logger.warning(f"No raster files found in {processing_path}. Skipping.") # Log a warning
            continue # Skip to the next directory

        final_output_filename = build_mosaic_filename(
            raster_files, dir_name, multiple_mosaics
        )
        final_output_path = os.path.join(output_dir, final_output_filename)

        logger.info(f"Found {len(raster_files)} raster files in {dir_name} to process") # Log the number of raster files found
        logger.info(f"Mosaic output filename: {final_output_filename}")

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
                source_nodata_values = get_nodata_values(ds, raster_file)
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
                warp_options_kwargs = dict(
                    dstSRS=target_epsg,
                    xRes=x_res,
                    yRes=y_res,
                    targetAlignedPixels=True, # Ensure pixels are aligned to the resolution grid for reprojection
                    resampleAlg='near',
                    outputType=source_data_type, # Preserve the source raster data type
                    creationOptions=['TILED=YES', 'COMPRESS=LZW', 'BIGTIFF=YES'], # Creation options
                    errorThreshold=0.0 # Set error threshold to 0.0 for strict reprojection
                )

                # Never assume zero is NoData.  Explicitly propagate declared
                # source values when every band defines one.  For bands without
                # a declared value (or mixed definitions), omit these options
                # so GDAL uses each band's native NoData metadata/mask.
                if source_nodata_values and all(value is not None for value in source_nodata_values):
                    nodata_value = nodata_option_value(source_nodata_values)
                    warp_options_kwargs['srcNodata'] = nodata_value
                    warp_options_kwargs['dstNodata'] = nodata_value
                else:
                    logger.info(
                        f"{base_name} has no complete declared NoData definition; "
                        "using GDAL's native source metadata/mask."
                    )

                warp_options = gdal.WarpOptions(**warp_options_kwargs)
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
        vrt_options_kwargs = dict(
            resampleAlg='nearest', # 'nearest' is correct for BuildVRTOptions
            addAlpha=False, # Do not add alpha band
            separate=False, # Do not separate bands
        )
        shared_nodata_values = common_nodata_values(all_reprojected)
        if shared_nodata_values is not None:
            vrt_options_kwargs['VRTNodata'] = nodata_option_value(shared_nodata_values)
            logger.info(
                f"All inputs for {dir_name} share declared NoData values "
                f"{shared_nodata_values}; applying them to the VRT."
            )
        else:
            logger.info(
                f"Inputs for {dir_name} have mixed or missing NoData values; "
                "preserving NoData metadata per source in the VRT."
            )
        vrt = gdal.BuildVRT( # Build the VRT
            vrt_path, # Path to save the VRT
            all_reprojected, # List of reprojected files
            options=gdal.BuildVRTOptions(**vrt_options_kwargs) # VRT build options
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
