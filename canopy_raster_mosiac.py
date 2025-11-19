import os # Library with methods for interacting with the operating system, like creating files and directories.
import glob # Library for finding files and directories that match a specified pattern
import logging # Library for logging messages for debugging and information
import shutil # Library for copying files and directories, including metadata and overviews
from collections import defaultdict # Library for creating dictionaries with default values
from osgeo import gdal, osr, ogr # Library for working with geospatial data, like GDAL (Geospatial Data Abstraction Library) and OGR (OpenGIS)

# Setup logger
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s") # Set up logging configuration
logger = logging.getLogger(__name__) # Create a logger object

# --- HELPER FUNCTIONS (No changes here) ---
def create_clean_cutline(source_shp_path):
    """
    Reads a shapefile, dissolves all features into a single geometry,
    fixes the geometry using a buffer(0) operation, and saves it
    to a new in-memory shapefile.

    Args:
        source_shp_path (str): Path to the source shapefile.

    Returns:
        str: The in-memory path to the new, clean shapefile, or None on failure.
    """
    memory_shp_path = f"/vsimem/{os.path.basename(source_shp_path)}_clean.shp" # Create a temporary in-memory path for the shapefile
    logger.info(f"Creating a clean, dissolved cutline from {source_shp_path}...") # Log the operation

    try:
        source_ds = ogr.Open(source_shp_path) # Open the source shapefile using OGR
        if not source_ds: # Check if the shapefile could be opened
            logger.error(f"Unable to open shapefile: {source_shp_path}") # Log the error message
            return None # Return None to indicate failure
        source_layer = source_ds.GetLayer() # Get the layer from the shapefile
        srs = source_layer.GetSpatialRef() # Get the spatial reference system from the layer

        dissolved_geom = ogr.Geometry(ogr.wkbMultiPolygon) # Create an empty multi-polygon geometry
        for feature in source_layer: # Loop through all features in the layer
            geom = feature.GetGeometryRef() # Get the geometry of the feature
            if geom: # Check if the feature has a geometry
                dissolved_geom = dissolved_geom.Union(geom) # Add the geometry to the dissolved geometry

        logger.info("Applying buffer(0) to fix potential geometry issues...") # Log the operation
        clean_geom = dissolved_geom.Buffer(0) # Apply a buffer(0) operation to the dissolved geometry to fix potential geometry issues

        if clean_geom.IsEmpty(): # Check if the geometry is empty after cleaning 
            logger.error("Geometry is empty after cleaning. Cannot create cutline.") # Log the error message
            return None # Return None to indicate failure

        driver = ogr.GetDriverByName('ESRI Shapefile') # Get the ESRI Shapefile driver to create the in-memory shapefile
        if os.path.exists(memory_shp_path): # Check if the in-memory shapefile already exists
              driver.DeleteDataSource(memory_shp_path) # Delete the existing in-memory shapefile

        mem_ds = driver.CreateDataSource(memory_shp_path) # Create the in-memory shapefile
        mem_layer = mem_ds.CreateLayer('clean', srs, geom_type=ogr.wkbPolygon) # Create a layer in the in-memory shapefile

        feature_defn = mem_layer.GetLayerDefn() # Get the definition of the layer
        new_feature = ogr.Feature(feature_defn) # Create a new feature
        new_feature.SetGeometry(clean_geom) # Set the geometry of the feature
        mem_layer.CreateFeature(new_feature) # Create the feature in the layer

        new_feature = None # Clean up the feature
        mem_ds = None   # Clean up the in-memory shapefile
        source_ds = None # Clean up the source shapefile

        logger.info(f"Clean cutline created at in-memory path: {memory_shp_path}") # Log the operation success
        return memory_shp_path # Return the in-memory path to the new, clean shapefile
    
    except Exception as e: # Catch any exceptions that may occur
        logger.error(f"Failed to create clean cutline: {e}") # Log the error message
        return None # Return None to indicate failure

def analyze_rasters(files):
    """
    Analyze rasters to determine the most common projection and average resolution.
    """
    logger.info("Analyzing rasters to determine optimal mosaic parameters...")
    srs_counts = defaultdict(int) # Create a dictionary to store projection counts
    x_res_list = [] # Create a list to store x-resolution values
    y_res_list = [] # Create a list to store y-resolution values

    for f in files: # Loop through all rasters
        ds = gdal.Open(f) # Open the raster
        if ds is None: # Check if the raster could be opened
            logger.warning(f"Cannot open {f} for analysis") # Log the warning
            continue # Skip to the next raster

        srs = osr.SpatialReference() # Create a SpatialReference object
        proj_wkt = ds.GetProjection() # Get the projection WKT
        if proj_wkt: # Check if the projection WKT is not empty
            srs.ImportFromWkt(proj_wkt) # Import the projection WKT into the SpatialReference object
            authority_name = srs.GetAuthorityName(None) # Get the authority name
            authority_code = srs.GetAuthorityCode(None) # Get the authority code
            
            if authority_name and authority_name.upper() == "EPSG" and authority_code: # Check if the projection is EPSG
                srs_counts[f"EPSG:{authority_code}"] += 1 # Increment the count for the EPSG code

        gt = ds.GetGeoTransform() # Get the geotransform values
        x_res_list.append(abs(gt[1])) # Append the absolute value of the x-resolution
        y_res_list.append(abs(gt[5])) # Append the absolute value of the y-resolution
        ds = None

    # Determine target EPSG, prioritizing the local projected CRS for Thailand (32647)
    if "EPSG:32647" in srs_counts: # If 32647 is found
        target_epsg = "EPSG:32647" # Set target EPSG to 32647
        logger.info("Detected EPSG:32647 (UTM Zone 47N). This will be the target.") # Log the operation
    elif "EPSG:4326" in srs_counts: # If 4326 is found
        target_epsg = "EPSG:4326" # Set target EPSG to 4326
        logger.info("Detected EPSG:4326. This will be the target.") # Log the operation
    elif srs_counts:  # Other projections found, but not 32647 or 4326
        target_epsg = "EPSG:32647"
        logger.info(f"Detected other projected CRS. Defaulting to {target_epsg} for Thailand.") # Log the operation
    else:  # No projections found
        target_epsg = "EPSG:4326" # Set target EPSG to 4326 as a default
        logger.warning("Could not determine a common EPSG code. Defaulting to EPSG:4326.") # Log the warning message

    if not x_res_list or not y_res_list: # If there are no resolution values 
        logger.error("Could not determine average resolution from any input files.") # Log the error
        return target_epsg, None, None # Return the target EPSG and None for resolution
    
    avg_x_res = sum(x_res_list) / len(x_res_list) # Calculate the average x-resolution
    avg_y_res = sum(y_res_list) / len(y_res_list) # Calculate the average y-resolution

    logger.info(f"Target EPSG: {target_epsg}, Avg Res: {avg_x_res}, {avg_y_res}") # Log the analysis results
    return target_epsg, avg_x_res, avg_y_res # Return the target EPSG, average x-resolution, and average y-resolution

def raster_pyramid(filepath, overview_levels=[2, 4, 8, 16, 32], resampling_method='nearest'):
    """
    Build internal raster pyramid for a raster file. For efficient to display in GIS Software like QGIS.

    Args:
        filepath (str): Path to the raster file.
    """
    logger.info(f"Building raster pyramid for {filepath} using levels {overview_levels} with {resampling_method} resampling...") # Log the operation message

    try:
        ds = gdal.Open(filepath, gdal.GA_Update) # Open the raster file in internal raster pyramid 
        if ds is None:
            logger.error(f"Cannot open {filepath} to build raster pyramid.") # Log the error message
            return # Return

        ds.BuildOverviews(resampling_method.upper(), overview_levels) # Build the internal raster pyramid using the specified resampling method and overview levels

        logger.info(f"Successfully built raster pyramid for {filepath}") # Log the success message for the operation

        ds = None # Close the dataset to flush changes 
    except Exception as e: # Catch any exceptions that may occur
        logger.error(f"Failed to build overviews for {filepath}: {e}") # Log the error message with the exception details

def raster_overlays_shapefile(raster_path, shapefile_path):
    """
    Return True if raster spatially overlaps with any feature in the shapefile
    """
    try:
        shapefile = ogr.Open(shapefile_path) # Open the shapefile using OGR
        if not shapefile: # Check if the shapefile could be opened 
            logger.error(f"Unable to open shapefile: {shapefile_path}") # Log the error message
            return False # Return False to indicate failure
        layer = shapefile.GetLayer() # Get the layer from the shapefile
 
        ds = gdal.Open(raster_path) # Open the raster using GDAL
        if not ds: # Check if the raster could be opened
            logger.warning(f"Cannot open raster {raster_path} to check overlay") # Log the warning
            return False # Return False to indicate failure
        gt = ds.GetGeoTransform() # Get the geotransform
        cols = ds.RasterXSize # Get the number of columns
        rows = ds.RasterYSize # Get the number of rows
        minx, maxx = gt[0], gt[0] + cols * gt[1] # Calculate the minimum and maximum x-coordinates
        maxy, miny = gt[3], gt[3] + rows * gt[5] # Calculate the maximum and minimum y-coordinates
        ds = None # Close the dataset

        ring = ogr.Geometry(ogr.wkbLinearRing) # Create a linear ring
        ring.AddPoint(minx, miny) # Add the minimum x- and y-coordinates 
        ring.AddPoint(minx, maxy) # Add the maximum x- and y-coordinates
        ring.AddPoint(maxx, maxy) # Add the maximum x- and y-coordinates
        ring.AddPoint(maxx, miny) # Add the minimum x- and y-coordinates
        ring.AddPoint(minx, miny) # Add the minimum x- and y-coordinates

        raster_geom = ogr.Geometry(ogr.wkbPolygon) # Create a polygon
        raster_geom.AddGeometry(ring) # Add the linear ring

        for feature in layer: # Loop through all features in the layer
            geom = feature.GetGeometryRef() # Get the geometry of the feature
            if geom and raster_geom.Intersects(geom): # Check if the raster intersects with the feature
                return True # Return True to indicate success

        return False # Return False to indicate failure

    except Exception as e: # Catch any exceptions that may occur
        logger.error(f"Overlay test failed for {raster_path}: {e}") # Log the error message
        return False # Return False to indicate failure

def main():
    """
    Script entry point.

    This script takes a directory of GeoTIFFs with different resolutions and projections
    and creates a mosaic for each subfolder within. The mosaic is reprojected to the
    average resolution of the input rasters and aligned to the Thailand subnational
    administrative boundaries shapefile.

    The script will:

    1. Create a clean version of the shapefile by dissolving all features into a single
       geometry and fixing any geometry issues with a buffer(0) operation.
    2. Loop through all subfolders in the input directory and check which rasters
       overlap with the shapefile.
    3. For each subfolder, determine the average resolution of the overlapping rasters.
    4. Reproject each overlapping raster to the average resolution and aligned to the
       shapefile.
    5. Build a VRT file from the reprojected rasters.
    6. Create a final mosaic from the VRT file using gdal.Translate.
    7. Build overviews for the final mosaic.
    8. Clean up temporary files.

    The script will output one mosaic per subfolder in the input directory, with the
    filename format <subfolder_name>_Canopy_Mosaic.tif.

    Example usage:

        python canopy_raster_mosiac.py

    """
    root_dir = r'3_ETH_Canopy_Sentinel-2_10m' # Input directory containing subfolders of rasters
    output_dir = r'Raster_Mosaic' # Output directory for the mosaics
    shapefile_path = r'Thailand\L05_Province_ESRI_2559.shp' # Path to the shapefile for clipping and alignment
    os.makedirs(output_dir, exist_ok=True) # Create the output directory if it doesn't exist
    nodata_value = -9999 # NoData value to use for the output mosaics

    clean_shapefile_path = create_clean_cutline(shapefile_path) # Create a clean version of the shapefile
    if not clean_shapefile_path: # Check if the clean shapefile was created successfully
        logger.error("Could not create a clean cutline from the source shapefile. Exiting.") # Log the error message  
        return # Exit the script

    subfolders = [f.path for f in os.scandir(root_dir) if f.is_dir()] # Get a list of all subfolders in the input directory
    root_rasters = glob.glob(os.path.join(root_dir, '*.tif')) + \
                   glob.glob(os.path.join(root_dir, '*.tiff')) # Get a list of all rasters in the root directory

    # If no subfolders, treat root_dir as a single folder to process
    if not subfolders and root_rasters:
        logger.info(f"No subfolders found in {root_dir}. Processing root-level rasters instead.") # Log the operation
        subfolders = [root_dir] # Treat root_dir as a single folder to process

    if not subfolders: # If still no subfolders or rasters, exit
        logger.warning(f"No raster files or subfolders found in {root_dir}. Exiting.") # Log the warning message 
        return # Exit the script

    for subfolder_path in subfolders: # Loop through all subfolders
        subfolder_name = os.path.basename(subfolder_path.rstrip(os.sep)) or "Root" # Get the name of the subfolder
        logger.info(f"\n--- Processing folder: {subfolder_name} ---") # Log the operation

        final_output_filename = f"{subfolder_name}_Canopy_Mosaic.tif" # Define the output filename
        final_output_path = os.path.join(output_dir, final_output_filename) # Define the full output path

        raster_files = glob.glob(os.path.join(subfolder_path, '*.tif')) + \
                       glob.glob(os.path.join(subfolder_path, '*.tiff')) # Get a list of all rasters in the subfolder
        
        if not raster_files: # If no rasters found, skip to the next subfolder
            logger.warning(f"No raster files found in {subfolder_path}. Skipping.") # Log the warning message
            continue # Skip to the next subfolder

        overlaying_rasters = [] # List to store rasters that overlap with the shapefile
        for raster_file in raster_files: # Loop through all rasters in the subfolder
            logger.info(f"Checking overlap for: {raster_file}") # Log the operation
            if raster_overlays_shapefile(raster_file, clean_shapefile_path): # Check if the raster overlaps with the shapefile
                logger.info(" -> Overlaps shapefile") # Log the operation
                overlaying_rasters.append(raster_file) # Add the raster to the list of overlaying rasters
            else: # If no overlap
                logger.info(" -> No overlap") # Log the operation

        if not overlaying_rasters: # If no rasters overlap with the shapefile, skip to the next subfolder
            logger.warning(f"No rasters overlap the shapefile in {subfolder_path}. Skipping.") # Log the warning message
            continue # Skip to the next subfolder

        target_epsg, x_res, y_res = analyze_rasters(overlaying_rasters) # Analyze the overlaying rasters to determine target EPSG and average resolution
        if x_res is None or y_res is None: # If average resolution could not be determined, skip to the next subfolder
            logger.error(f"Failed to determine average resolution for {subfolder_name}. Skipping.") # Log the error message
            continue # Skip to the next subfolder

        reprojected_temp_dir = os.path.join(output_dir, f"temp_{subfolder_name}_reprojected") # Temporary directory for reprojected rasters
        os.makedirs(reprojected_temp_dir, exist_ok=True) # Create the temporary directory
        all_reprojected = [] # List to store paths of all reprojected rasters

        for i, raster_file in enumerate(overlaying_rasters): # Loop through all overlaying rasters
            try:
                ds_check = gdal.Open(raster_file) # Open the raster to check its projection
                if ds_check is None: # If the raster could not be opened, skip to the next raster
                    logger.warning(f"Cannot open {raster_file} to check CRS. Skipping.") # Log the warning message
                    continue # Skip to the next raster
                srs_check = osr.SpatialReference() # Create a SpatialReference object
                source_epsg_str = None # Variable to store the source EPSG code
                proj_wkt_check = ds_check.GetProjection() # Get the projection WKT
                if proj_wkt_check: # If the projection WKT is not empty
                    srs_check.ImportFromWkt(proj_wkt_check) # Import the projection WKT into the SpatialReference object
                    authority_name = srs_check.GetAuthorityName(None) # Get the authority name
                    authority_code = srs_check.GetAuthorityCode(None) # Get the authority code
                    if authority_name and authority_name.upper() == "EPSG" and authority_code: # If the projection is EPSG
                        source_epsg_str = f"EPSG:{authority_code}" # Set the source EPSG code
                ds_check = None # Close the dataset

                if source_epsg_str == target_epsg: # If the raster is already in the target projection, skip reprojection
                    logger.info(f"{os.path.basename(raster_file)} already in {target_epsg}. Skipping reprojection.") # Log the operation
                    all_reprojected.append(raster_file) # Add the raster to the list of reprojected rasters
                    continue # Skip to the next raster

                reprojected_path = os.path.join(reprojected_temp_dir, f"reproj_{i}_{os.path.basename(raster_file)}") # Define the path for the reprojected raster
                logger.info(f"Reprojecting '{raster_file}' from {source_epsg_str or 'Unknown'} to {target_epsg}...") # Log the operation 
                warp_options = gdal.WarpOptions( # Define the warp options for reprojection
                    dstSRS=target_epsg, xRes=x_res, yRes=y_res, targetAlignedPixels=True, # Align pixels to the target resolution
                    srcNodata=0, resampleAlg='near', dstNodata=nodata_value, # Set NoData values
                    outputType=gdal.GDT_UInt16, # Set output data type
                    creationOptions=['TILED=YES', 'COMPRESS=LZW', 'BIGTIFF=YES'], # Set creation options
                    errorThreshold=0.0 # Set error threshold for reprojection
                )
                ds = gdal.Warp(reprojected_path, raster_file, options=warp_options) # Reproject the raster
                if ds:
                    all_reprojected.append(reprojected_path) # Add the reprojected raster to the list
                ds = None # Close the dataset

            except Exception as e: # Catch any exceptions that may occur
                logger.error(f"Failed to process {raster_file}: {e}") # Log the error message
                continue # Skip to the next raster

        if not all_reprojected: # If no rasters were successfully reprojected, skip to the next subfolder
            logger.error(f"No rasters successfully reprojected for {subfolder_name}!") # Log the error message
            shutil.rmtree(reprojected_temp_dir, ignore_errors=True) # Clean up the temporary directory
            continue # Skip to the next subfolder

        vrt_path = os.path.join(reprojected_temp_dir, f"aligned_mosaic_{subfolder_name}.vrt") # Path for the VRT file
        logger.info(f"Building VRT for {subfolder_name}...") # Log the operation
        vrt = gdal.BuildVRT(vrt_path, all_reprojected, options=gdal.BuildVRTOptions( # Build the VRT from the reprojected rasters
            resampleAlg='nearest', srcNodata=nodata_value, VRTNodata=nodata_value)) # Set VRT options
        if vrt is None: # If the VRT could not be built, skip to the next subfolder
            logger.error(f"Failed to build VRT for {subfolder_name}") # Log the error message
            shutil.rmtree(reprojected_temp_dir, ignore_errors=True) # Clean up the temporary directory
            continue # Skip to the next subfolder
        vrt = None # Close the VRT dataset

        logger.info(f"Creating final mosaic for {subfolder_name}...") # Log the operation
        gdal.Translate(final_output_path, vrt_path, # Create the final mosaic from the VRT
            options=gdal.TranslateOptions(format='GTiff', 
                                          creationOptions=['TILED=YES', 'COMPRESS=LZW', 'BIGTIFF=YES', 'PREDICTOR=2'],
                                          noData=nodata_value)) # Set translation options
        raster_pyramid(final_output_path) # Build the internal raster pyramid for the final mosaic

        if os.path.exists(final_output_path): # If the final mosaic was created successfully
            shutil.rmtree(reprojected_temp_dir, ignore_errors=True) # Clean up the temporary directory
            logger.info(f"Mosaic for {subfolder_name} complete at {final_output_path}") # Log the success message

    logger.info("✅ All mosaics processed successfully!") # Log the completion message

if __name__ == "__main__":
    main()
