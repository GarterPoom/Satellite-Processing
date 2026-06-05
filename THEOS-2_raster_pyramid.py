import os
import glob
import logging
import shutil
from collections import defaultdict
from osgeo import gdal, osr

# Setup logger
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

def build_overviews(filepath, overview_levels=[2, 4, 8, 16, 32], resampling_method='nearest', output_dir=None):
    """
    Builds raster pyramid overviews for a given GeoTIFF file.

    Args:
        filepath (str): Path to the GeoTIFF file.
        overview_levels (list): List of integers representing the downsampling factors
                                for each overview level. Default is [2, 4, 8, 16, 32].
        resampling_method (str): Resampling method to use for overview creation.
                                 Common options include 'average', 'nearest', 'cubic', 'mode',
                                 'lanczos'. Default is 'nearest'. An external .ovr file will be created.
        output_dir (str): Optional output directory to store the processed raster file.
                          If None, the overview will be created alongside the original file.
    """
    logger.info(f"Building overviews for {filepath} using levels {overview_levels} with {resampling_method} resampling...")

    # Determine the target file path early to ensure it's available for logging in case of an error.
    if output_dir:
        # Create output directory if it doesn't exist
        os.makedirs(output_dir, exist_ok=True)
        filename = os.path.basename(filepath)
        target_filepath = os.path.join(output_dir, filename)
    else:
        target_filepath = filepath

    try:
        # Determine the output file path
        if output_dir:
            # Create output directory if it doesn't exist
            os.makedirs(output_dir, exist_ok=True)
            
            # Get the filename from the original path
            filename = os.path.basename(filepath)
            output_filepath = os.path.join(output_dir, filename)
            
            # Copy the original file to output directory if it doesn't exist
            if not os.path.exists(output_filepath):
                shutil.copy2(filepath, output_filepath)
                logger.info(f"Copied {filepath} to {output_filepath}")
            
            target_filepath = output_filepath
        else:
            target_filepath = filepath
            # If an output directory is specified, copy the original file there.
            # This check prevents re-copying if the script is run multiple times.
            if not os.path.exists(target_filepath):
                shutil.copy2(filepath, target_filepath)
                logger.info(f"Copied {filepath} to {target_filepath}")
        
        ds = gdal.Open(target_filepath, gdal.GA_ReadOnly) # Open in read-only to force external .ovr file
        if ds is None:
            logger.error(f"Cannot open {target_filepath} to build overviews.")
            return

 
        # Build overviews
        ds.BuildOverviews(resampling_method, overview_levels)
        ds = None # Close the dataset

 
        logger.info(f"Successfully built overviews for {target_filepath}")
    except OSError as e:
        if e.winerror == 112: # Specifically check for "not enough space on the disk" error on Windows
            logger.error(f"Failed to process {target_filepath}: Not enough space on the disk. Please free up space and try again.")
        else:
            logger.error(f"An OS error occurred while processing {target_filepath}: {e}")
    except Exception as e:
        logger.error(f"Failed to build overviews for {target_filepath}: {e}")


def main():
    """
    Main entry point for building raster pyramid overviews.

    This function defines a search directory and pattern for finding GeoTIFF files
    recursively. It then loops through all matching files and calls the
    build_overviews function to create the overviews.

    Args:
        None

    Returns:
        None
    """
    # Define the root directory to search for GeoTIFF files
    search_directory = r'THEOS-2'
    
    # Define the output directory for processed rasters
    output_directory = r'THEOS-2_Raster_Pyramid_Output'

    # To create directory
    os.makedirs(output_directory, exist_ok=True)

    # Toggle to include subdirectories under the root directory
    include_subdirectories = True

    # Define the pattern for GeoTIFF files (e.g., .tif)
    if include_subdirectories:
        # '**' matches zero or more directories when recursive=True
        pattern = os.path.join(search_directory, '**', '*.tif')
        recursive = True
    else:
        # Only files directly inside the root directory
        pattern = os.path.join(search_directory, '*.tif')
        recursive = False

    logger.info(f"Searching for GeoTIFF files in {search_directory} using pattern: {pattern}")
    logger.info(f"Include subdirectories: {include_subdirectories}")
    logger.info(f"Output directory: {output_directory}")

    # Use glob to find all files matching the pattern
    for filepath in glob.glob(pattern, recursive=recursive):
        logger.info(f"Found GeoTIFF file: {filepath}")
        build_overviews(filepath, output_dir=output_directory)
        logger.info(f"Finished processing {filepath}")

if __name__ == "__main__":
    main()