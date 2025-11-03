import os
import rasterio
import numpy as np

def chop_rasters(input_dir, output_dir, tile_size=(256, 256), extensions=('.tif', '.tiff')):
    """
    Chops all GeoTIFF raster files in the input directory (recursively) into smaller tiles
    and saves them in the output directory. Handles multi-band GeoTIFFs and preserves
    geospatial metadata where possible. Output filenames are based on the input filename,
    including relative path to avoid conflicts.

    Args:
    - input_dir (str): Path to the input directory containing GeoTIFF files.
    - output_dir (str): Path to the output directory where chopped tiles will be saved.
    - tile_size (tuple): (height, width) of each tile in pixels. Default is (256, 256).
    - extensions (tuple): File extensions to consider as rasters. Default includes GeoTIFF formats.

    Note: Uses rasterio for GeoTIFF handling. Tiles are saved as GeoTIFFs to preserve metadata.
    For multi-band rasters, all bands are included in the output tiles.
    """
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    for root, dirs, files in os.walk(input_dir):
        for file in files:
            if file.lower().endswith(extensions):
                input_path = os.path.join(root, file)
                try:
                    with rasterio.open(input_path) as src:
                        # Get raster dimensions and metadata
                        height, width = src.height, src.width
                        tile_height, tile_width = tile_size
                        meta = src.meta.copy()

                        # Compute relative path for output filename
                        rel_path = os.path.relpath(root, input_dir)
                        prefix = rel_path.replace(os.sep, '_') + '_' if rel_path != '.' else ''
                        base_name, _ = os.path.splitext(file)

                        # Update metadata for output tiles (PNG or GeoTIFF)
                        meta.update({
                            'driver': 'GTiff',  # Save as GeoTIFF
                            'height': tile_height,
                            'width': tile_width,
                            'transform': None  # Will be updated per tile
                        })

                        for row in range(0, height, tile_height):
                            for col in range(0, width, tile_width):
                                # Define window for the tile
                                window = rasterio.windows.Window(
                                    col_off=col,
                                    row_off=row,
                                    width=min(tile_width, width - col),
                                    height=min(tile_height, height - row)
                                )

                                # Read the tile data
                                tile_data = src.read(window=window)

                                # Update metadata for this tile
                                tile_meta = meta.copy()
                                tile_meta['height'] = tile_data.shape[1]
                                tile_meta['width'] = tile_data.shape[2]
                                tile_meta['transform'] = src.window_transform(window)

                                # Dynamic output filename: prefix_originalbase_row_col.tif
                                tile_name = f"{prefix}{base_name}_{row // tile_height}_{col // tile_width}.tif"
                                output_path = os.path.join(output_dir, tile_name)

                                # Save the tile
                                with rasterio.open(output_path, 'w', **tile_meta) as dst:
                                    dst.write(tile_data)

                                print(f"Saved tile: {output_path}")

                except Exception as e:
                    print(f"Error processing {input_path}: {e}")

# Example usage:
if __name__ == "__main__":
    chop_rasters('Raster_Composite', 'Raster_Mosaic', tile_size=(512, 512))