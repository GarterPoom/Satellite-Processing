# Force GDAL/PROJ to use the proj.db bundled with this conda env's rasterio,
# overriding any system PROJ_LIB (e.g. PostgreSQL/PostGIS) that ships an
# incompatible, older proj.db. Must run before importing rasterio.
import os                       # filesystem paths, environment variables, directory checks
import sysconfig               # locates this Python install's site-packages directory

_site = sysconfig.get_paths()["purelib"]                                  # absolute path to site-packages (where rasterio lives)
os.environ["PROJ_LIB"] = os.path.join(_site, "rasterio", "proj_data")     # point legacy PROJ at rasterio's bundled proj.db
os.environ["PROJ_DATA"] = os.path.join(_site, "rasterio", "proj_data")    # newer PROJ env var name for the same data dir
os.environ["GDAL_DATA"] = os.path.join(_site, "rasterio", "gdal_data")    # point GDAL at rasterio's bundled support files

# PyTorch and the scikit-image/opensr_model stack each link their own OpenMP
# runtime (libiomp5md.dll) on Windows, which aborts the process with
# "OMP: Error #15". Allow the duplicate so inference can run. Must be set
# before torch is imported below.
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"

import numpy as np                          # array math for the raster tiles
import rasterio                             # reading/writing GeoTIFFs with georeferencing
from rasterio.windows import Window         # rectangular subregion (col, row, width, height) for partial reads/writes
from rasterio.enums import Resampling       # resampling algorithms (used here for overview building)
from affine import Affine                   # affine transform math to derive the output pixel grid
import torch                                # tensor library running the super-resolution model
import mlstac                               # downloads/loads the SEN2SR model from its mlstac package
from sen2sr.utils import predict_large      # tiled inference helper that runs the model over a large array
from tqdm import tqdm                       # progress bar over the tile loop
import time                                 # wall-clock stopwatch for the SR / index phases
import glob                                 # enumerate the input rasters in INPUT_DIR

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
INPUT_DIR   = r"Sentinel-2"         # directory of input rasters; every *.tif inside is super-resolved
OUTPUT_DIR  = r"Sentinel-2_SR"      # directory the SR reflectance + spectral-index outputs are written to
MODEL_DIR   = "model/SEN2SRLite"                                # downloaded mlstac model
INCLUDE_SUBDIRS = True              # Toggle to search subdirectories within INPUT_DIR

# Each input's output filenames are derived from its basename plus a resolution
# tag (e.g. T47PQS_20260331T033541.tif -> T47PQS_20260331T033541_SR2.5m.tif and
# ..._SR2.5m_indices.tif); see output_paths().

# The model REQUIRES these 10 bands, in this exact order, as reflectance in [0, 1]
# (the mlm manifest fixes the input at shape [-1, 10, 128, 128]). We read them by
# name so a different band order in the source still works. This list is the
# FIXED model INPUT and must stay at exactly 10 bands.
SR_BANDS = ["B02", "B03", "B04", "B05", "B06", "B07", "B08", "B8A", "B11", "B12"]  # 10 Sentinel-2 bands in model order

# Spectral indices written to a SEPARATE output file (INDEX_OUTPUT_PATH) in a
# second pass that reads the super-resolved reflectance back. They are derived
# from the SR reflectance (not fed to the model), so they share the same
# upscaled resolution as the SR bands. Each is clamped to its physical range
# (see compute_indices) so divide-by-Red outliers can't wreck GIS auto-stretch:
#   NBR   = (B08 - B12) / (B08 + B12)                          burn ratio, [-1, 1]
#   BAIS2 = (1 - sqrt(B06*B07*B8A / B04)) * ((B12-B8A)/sqrt(B12+B8A) + 1)  burned-area, [-1, 2]
#   NDVI  = (B08 - B04) / (B08 + B04)                          vegetation, [-1, 1]
INDEX_BANDS = ["NBR", "BAIS2", "NDVI"]                  # derived index bands, in output order

_B = {name: i for i, name in enumerate(SR_BANDS)}       # band name -> 0-based row in an SR_BANDS-ordered array

SCALE = 10000.0   # Sentinel-2 L2A is stored as reflectance * 10000

# The SEN2SR model always upscales 10 m -> 2.5 m (a fixed 4x; see detect_upscale_factor).
# To produce a coarser product we super-resolve at the model's native 2.5 m and then
# average-downsample to OUT_FACTOR. OUT_FACTOR is the OUTPUT upscale relative to the 10 m
# input: 4 -> 2.5 m, 2 -> 5 m, 1 -> 10 m. It must divide the model factor (rn).
OUT_FACTOR = 4   # 10 m input / 4 = 2.5 m output, the model's native 2.5 m downsampled by 4x with area interpolation. 10 m / 4 = 2.5 m, 10 m / 2 = 5 m, 10 m / 1 = 10 m (no SR, just reformatting)

# Tiling: we keep CORE input pixels per tile and read an extra HALO border of
# real context on every side. The halo is super-resolved but discarded so that
# seams between tiles disappear. predict_large needs a SQUARE input, so every
# tile fed to it is (CORE + 2*HALO) on a side.
CORE = 2048   # input pixels written out per tile
HALO = 64    # input pixels of context discarded on each side (suppresses seams)

OVERVIEW_LEVELS = [2, 4, 8, 16, 32]   # nearest-neighbour pyramid levels

# --------------------------------------------------------------------------- #
# Model
# --------------------------------------------------------------------------- #
# Download once if not already present (same model as the AOI script).
if not os.path.isdir(MODEL_DIR):                # only fetch the model when its folder is absent
    mlstac.download(                            # pull the SEN2SRLite model definition + weights
        file="https://huggingface.co/tacofoundation/sen2sr/resolve/main/SEN2SRLite/main/mlm.json",  # model manifest URL
        output_dir=MODEL_DIR,                   # save everything under MODEL_DIR
    )

device = torch.device("cuda" if torch.cuda.is_available() else "cpu")     # use the GPU when available, else fall back to CPU
model = mlstac.load(MODEL_DIR).compiled_model(device=device)              # load the model and compile it onto the chosen device

def resolve_band_indexes(src):
    """Map the required band NAMES to 1-based rasterio band indexes."""
    desc = {name: i + 1 for i, name in enumerate(src.descriptions) if name}   # {band name: 1-based index} for every named band
    missing = [b for b in SR_BANDS if b not in desc]                          # required bands not found in the source
    if missing:                                                               # bail out if any required band is absent
        raise ValueError(
            f"Input is missing required bands {missing}. "
            f"Available band descriptions: {src.descriptions}"
        )
    return [desc[b] for b in SR_BANDS]  # reorders to the model's expected order


def detect_upscale_factor(src, idx):
    """Run the model on one real 128x128 patch to learn res_n (e.g. 10m -> 2.5m = 4)."""
    patch = src.read(idx, window=Window(0, 0, 128, 128)).astype("float32") / SCALE  # read a 128x128 corner patch, scale to reflectance
    patch = np.nan_to_num(patch, nan=0.0, posinf=0.0, neginf=0.0)                    # replace NaN/Inf with 0 so the model gets clean input
    with torch.no_grad():                                                            # disable gradient tracking — inference only
        out = model(torch.from_numpy(patch).float().to(device)[None]).squeeze(0)     # add batch dim, run model, drop batch dim
    return out.shape[-1] // 128             # output width / input width = integer upscale factor (res_n)


def compute_indices(refl):
    """Compute the INDEX_BANDS from super-resolved reflectance in [0, 1].

    `refl` is a torch tensor (len(SR_BANDS), H, W). Returns a tensor
    (len(INDEX_BANDS), H, W) in INDEX_BANDS order. Division artefacts over
    no-data (0/0, x/0) become NaN/Inf and are scrubbed to 0 by the caller's
    nan_to_num, matching how the rest of the pipeline handles no-data.

    Each index is clamped to its physically meaningful range. Without this,
    BAIS2 divides by Red (B04) and by sqrt(B12+B8A): over water / shadow /
    cloud / the no-data collar, where Red approaches 0, the result blows up to
    huge finite magnitudes. A single such outlier wrecks the band's min/max so
    GIS default stretch renders the whole index band as a flat, "unviewable"
    image. clamp() propagates NaN, so collar pixels still scrub to 0 below.
    """
    b04 = refl[_B["B04"]]   # Red
    b06 = refl[_B["B06"]]   # Red-edge 2
    b07 = refl[_B["B07"]]   # Red-edge 3
    b08 = refl[_B["B08"]]   # NIR (broad, 10 m)
    b8a = refl[_B["B8A"]]   # NIR (narrow)
    b12 = refl[_B["B12"]]   # SWIR 2

    nbr   = ((b08 - b12) / (b08 + b12)).clamp(-1, 1)       # Normalized Burn Ratio, [-1, 1]
    bais2 = ((1.0 - torch.sqrt((b06 * b07 * b8a) / b04))
             * ((b12 - b8a) / torch.sqrt(b12 + b8a) + 1.0)).clamp(-1, 2)  # Burned Area Index, clamped to suppress divide-by-Red outliers
    ndvi  = ((b08 - b04) / (b08 + b04)).clamp(-1, 1)       # Normalized Difference Vegetation Index, [-1, 1]

    return torch.stack([nbr, bais2, ndvi], dim=0)          # order must match INDEX_BANDS


def build_internal_overviews(path, levels=OVERVIEW_LEVELS):
    """Build INTERNAL (embedded) nearest-neighbour pyramids — no sidecar .ovr.

    Opening the dataset in update mode ('r+') makes GDAL store the overviews
    inside the GeoTIFF itself. Opening read-only (GA_ReadOnly) is what forces an
    external .ovr file, which is what the mosaic scripts deliberately do.
    """
    with rasterio.Env(                       # set GDAL config options just for this overview build
        COMPRESS_OVERVIEW="DEFLATE",  # compress the pyramids like the main image
        PREDICTOR_OVERVIEW="3",              # floating-point predictor (PRED=3) for the float32 output
        BIGTIFF_OVERVIEW="YES",       # pyramids can exceed 4 GB on a raster this large
    ):
        with rasterio.open(path, "r+") as ds:                 # reopen the output in update mode (embeds overviews internally)
            ds.build_overviews(levels, Resampling.nearest)    # generate nearest-neighbour pyramids at the configured levels


def _fmt(seconds):
    """Format a duration in seconds as a compact h/m/s string for the log."""
    m, s = divmod(seconds, 60)              # split into whole minutes and remaining seconds
    h, m = divmod(int(m), 60)               # split minutes into hours and minutes
    if h:                                   # show hours only when there are any
        return f"{h}h {m}m {s:.1f}s"
    if m:                                   # otherwise show minutes when there are any
        return f"{m}m {s:.1f}s"
    return f"{s:.1f}s"                       # sub-minute durations: seconds only


def compute_index_file(sr_path, index_path, chunk=2048):
    """Pass 2: derive INDEX_BANDS from the super-resolved reflectance file.

    Reads `sr_path` (the reflectance product written by the SR pass) back in
    `chunk`-sized blocks, recovers reflectance in [0, 1] by dividing out SCALE,
    computes the indices with compute_indices, and writes them to `index_path`
    as a SEPARATE GeoTIFF at the same grid / CRS / resolution as the SR output.
    """
    with rasterio.open(sr_path) as src:                       # the reflectance product from pass 1
        idx = resolve_band_indexes(src)                       # SR_BANDS as 1-based indexes (robust to band order)
        W, H = src.width, src.height                          # output grid dimensions (already upscaled)
        profile = src.profile.copy()                          # inherit CRS, transform, tiling, compression
        profile.update(count=len(INDEX_BANDS), dtype="float32", nodata=0,  # 3 index bands, float32, 0 = no-data
                       BIGTIFF="YES")                         # force BigTIFF: a creation-only option, NOT carried in src.profile, and DEFLATE defeats GDAL's IF_NEEDED auto-promotion
        profile.pop("photometric", None)                      # drop any inherited photometric tag

        with rasterio.open(index_path, "w", **profile) as dst:   # create the separate index raster
            dst.descriptions = tuple(INDEX_BANDS)                # label the index bands (NBR, BAIS2, NDVI)
            blocks = [(x, y) for y in range(0, H, chunk) for x in range(0, W, chunk)]  # top-left of each chunk
            for x, y in tqdm(blocks, desc="Computing indices"):  # iterate chunks with a progress bar
                w, h = min(chunk, W - x), min(chunk, H - y)      # chunk size (shrinks at the right/bottom edges)
                refl = src.read(                                 # read the reflectance bands for this chunk
                    idx, window=Window(x, y, w, h)
                ).astype("float32") / SCALE                      # back to reflectance in [0, 1]
                refl = np.nan_to_num(refl, nan=0.0, posinf=0.0, neginf=0.0)  # scrub NaN/Inf to 0
                t = torch.from_numpy(refl).to(device).clamp(0, 1)  # to tensor on the compute device, clamp to [0, 1]
                indices = compute_indices(t)                     # (len(INDEX_BANDS), h, w): NBR, BAIS2, NDVI
                indices = torch.nan_to_num(indices, nan=0.0, posinf=0.0, neginf=0.0)  # scrub division artefacts to 0
                dst.write(                                       # write the index block into the output raster
                    indices.to(torch.float32).cpu().numpy(),
                    window=Window(x, y, w, h),
                )


def _res_tag(out_res):
    """Compact resolution tag for output filenames: 2.5 -> 'SR2.5m', 5.0 -> 'SR5m', 10 -> 'SR10m'."""
    return f"SR{out_res:g}m"          # %g trims trailing zeros so 5.0 m reads as 'SR5m', not 'SR5.0m'


def output_paths(input_path, out_res, input_root=INPUT_DIR):
    """Derive the SR and spectral-index output paths from an input filename.
    Preserves subdirectory structure relative to input_root within OUTPUT_DIR.
    """
    rel_path = os.path.relpath(input_path, input_root)
    rel_dir = os.path.dirname(rel_path)
    stem = os.path.splitext(os.path.basename(input_path))[0]
    tag = _res_tag(out_res)
    
    # Create output subfolder matching input structure
    target_output_dir = os.path.join(OUTPUT_DIR, rel_dir)
    os.makedirs(target_output_dir, exist_ok=True)

    sr_path    = os.path.join(target_output_dir, f"{stem}_{tag}.tif")
    index_path = os.path.join(target_output_dir, f"{stem}_{tag}_indices.tif")
    return sr_path, index_path


def process_file(input_path):
    """Super-resolve one input raster, writing its SR reflectance and spectral-index outputs.

    Output paths are derived from `input_path` and the achieved output pixel
    size (see output_paths), so the input filename drives the output names.
    """
    t_start = time.perf_counter()                           # stopwatch start (this file)
    with rasterio.open(input_path) as src:                  # open the input raster for reading
        idx = resolve_band_indexes(src)                     # 1-based band indexes in the model's required order
        rn = detect_upscale_factor(src, idx)                # model's native upscale factor (e.g. 4 for 10m -> 2.5m)
        if OUT_FACTOR > rn or rn % OUT_FACTOR != 0:         # OUT_FACTOR must be a whole-number divisor of rn
            raise ValueError(
                f"OUT_FACTOR={OUT_FACTOR} must be a divisor of the model factor rn={rn} "
                f"(valid choices: {[f for f in range(1, rn + 1) if rn % f == 0]})"
            )
        ds_factor = rn // OUT_FACTOR                         # extra downsampling applied to the SR result (e.g. 4//2=2)
        W, H = src.width, src.height                         # input raster width and height in pixels
        out_res = src.res[0] / OUT_FACTOR                    # output pixel size in CRS units (e.g. 10 m / 4 = 2.5 m)
        output_path, index_output_path = output_paths(input_path, out_res)  # names derived from the input filename
        print(f"Input {W}x{H}, model factor rn={rn}, output factor={OUT_FACTOR} "  # report input/output dimensions
              f"-> output {W*OUT_FACTOR}x{H*OUT_FACTOR}, {len(SR_BANDS)} reflectance bands "
              f"(indices written separately to {index_output_path})")

        # Output profile: same origin & CRS, pixel size / rn, the 10 SR reflectance
        # bands, written as float32 (the indices go to their own file in pass 2).
        profile = src.profile.copy()                        # start from the input's metadata (CRS, dtype, etc.)
        profile.update(                                     # override the fields that change for the SR output
            driver="GTiff",                                 # write a GeoTIFF
            count=len(SR_BANDS),                            # the 10 super-resolved reflectance bands
            dtype="float32",                                # float32: reflectance keeps its 0-10000 scale
            width=W * OUT_FACTOR,                           # output is OUT_FACTOR times wider than the 10 m input
            height=H * OUT_FACTOR,                          # output is OUT_FACTOR times taller than the 10 m input
            transform=src.transform * Affine.scale(1.0 / OUT_FACTOR),  # OUT_FACTOR x finer pixels; keeps the top-left origin
            nodata=0,                                       # 0 marks no-data (also the out-of-raster fill)
            tiled=True, blockxsize=256, blockysize=256,     # internally tiled for efficient windowed I/O
            compress="DEFLATE", predictor=3, BIGTIFF="YES", # lossless compression (PRED=3 floating-point); allow files > 4 GB
        )
        profile.pop("photometric", None)                    # drop any inherited photometric tag (irrelevant for 10 bands)

        S_in = CORE + 2 * HALO  # square tile size fed to the model

        with rasterio.open(output_path, "w", **profile) as dst:   # create the output raster for writing
            dst.descriptions = tuple(SR_BANDS)                    # label output bands (reflectance only)

            tiles = [(cx, cy) for cy in range(0, H, CORE) for cx in range(0, W, CORE)]  # top-left core corner of each tile
            for cx, cy in tqdm(tiles, desc="Super-resolving"):    # iterate tiles with a progress bar
                # Square source window starting HALO pixels before the core.
                sx0, sy0 = cx - HALO, cy - HALO               # ideal (possibly off-raster) top-left of the haloed window
                # Intersect with the raster bounds (edges have less/no halo).
                vx0, vy0 = max(sx0, 0), max(sy0, 0)           # clamp top-left to the valid raster area
                vx1, vy1 = min(sx0 + S_in, W), min(sy0 + S_in, H)  # clamp bottom-right to the valid raster area
                data = src.read(                              # read the valid (clamped) portion of the haloed window
                    idx, window=Window(vx0, vy0, vx1 - vx0, vy1 - vy0)
                ).astype("float32") / SCALE                   # convert to float reflectance in [0, 1]
                data = np.nan_to_num(data, nan=0.0, posinf=0.0, neginf=0.0)  # scrub NaN/Inf to 0 before inference

                # Place the read data into a fixed SxS buffer. Out-of-raster
                # margins stay 0 — this matches predict_large's native behaviour
                # at the true image border (it pads with nothing either).
                buf = np.zeros((len(idx), S_in, S_in), dtype="float32")  # zero-filled square buffer (bands, S_in, S_in)
                ox, oy = vx0 - sx0, vy0 - sy0                            # offset of valid data inside the square buffer
                buf[:, oy:oy + data.shape[1], ox:ox + data.shape[2]] = data  # drop the read data into its place in the buffer

                # Super-resolve the square tile (tiles internally into 128px chunks).
                with torch.no_grad():                          # inference only — no gradients
                    sr = predict_large(                        # run the model over the whole square buffer
                        torch.from_numpy(buf).float().to(device), model, overlap=32  # 32px internal overlap blends sub-chunks
                    )

                # Crop back to the core region (drop the super-resolved halo).
                core_w = min(CORE, W - cx)                     # core width (shrinks for the last column of tiles)
                core_h = min(CORE, H - cy)                     # core height (shrinks for the last row of tiles)
                y0, x0 = HALO * rn, HALO * rn                  # where the core starts inside the upscaled (2.5 m) tile
                core = sr[:, y0:y0 + core_h * rn, x0:x0 + core_w * rn]  # slice out just the core (halo discarded), still at 2.5 m

                # Average-downsample the 2.5 m core to the requested OUT_FACTOR (e.g. -> 5 m).
                # 'area' interpolation averages the rn/OUT_FACTOR source pixels per output pixel.
                if ds_factor > 1:                              # skip when OUT_FACTOR == rn (already at native 2.5 m)
                    core = torch.nn.functional.interpolate(    # resize the tensor before the uint16 cast
                        core[None],                            # add a batch dim: interpolate expects (N, C, H, W)
                        size=(core_h * OUT_FACTOR, core_w * OUT_FACTOR),  # exact target size (no rounding ambiguity)
                        mode="area",                           # area = average pooling, the right filter for downsampling
                    )[0]                                       # drop the batch dim again

                # Scale the super-resolved reflectance back to the 0-10000 integer
                # convention and write it out. Indices are NOT computed here; a
                # second pass derives them from this file (see compute_index_file).
                refl = core.clamp(0, 1)                        # super-resolved reflectance in [0, 1]
                block = refl * SCALE                           # reflectance back to the 0-10000 scale
                block = torch.nan_to_num(block, nan=0.0, posinf=0.0, neginf=0.0)  # scrub no-data division artefacts to 0
                block = block.to(torch.float32).cpu().numpy()  # float32 output array (len(SR_BANDS), h, w)
                dst.write(                                     # write the core block into the output raster
                    block,
                    window=Window(cx * OUT_FACTOR, cy * OUT_FACTOR, core_w * OUT_FACTOR, core_h * OUT_FACTOR),  # output window at OUT_FACTOR scale
                )

    print(f"Done: {output_path}")                            # super-resolution pass complete

    print("Building internal nearest-neighbour overviews...")  # announce overview building
    build_internal_overviews(output_path)                      # embed pyramids inside the output GeoTIFF
    print("Overviews embedded (no .ovr file).")               # confirm overviews are internal
    t_sr = time.perf_counter()                                 # stopwatch after the SR pass + its overviews
    print(f"Super-resolution + overviews took {_fmt(t_sr - t_start)}")

    # Pass 2: derive the spectral indices from the SR reflectance we just wrote
    # and save them to their OWN file, then give that file overviews as well.
    print(f"Computing spectral indices -> {index_output_path}")
    compute_index_file(output_path, index_output_path)
    print("Building internal nearest-neighbour overviews for the index file...")
    build_internal_overviews(index_output_path)
    print("Overviews embedded (no .ovr file).")
    t_idx = time.perf_counter()                                # stopwatch after the index pass + its overviews
    print(f"Done: {index_output_path}")
    print(f"Index computation + overviews took {_fmt(t_idx - t_sr)}")
    print(f"Total elapsed: {_fmt(t_idx - t_start)}")


def main():
    """Super-resolve every *.tif in INPUT_DIR, naming each output from its input filename."""
    os.makedirs(OUTPUT_DIR, exist_ok=True)                  # ensure the output directory exists
    
    # Define the search pattern based on recursion toggle
    if INCLUDE_SUBDIRS:
        pattern = os.path.join(INPUT_DIR, '**', '*.tif')
    else:
        pattern = os.path.join(INPUT_DIR, '*.tif')

    inputs = sorted(glob.glob(pattern, recursive=INCLUDE_SUBDIRS)) # find input rasters
    if not inputs:                                          # nothing to do — fail loudly rather than silently
        raise SystemExit(f"No .tif inputs found in {INPUT_DIR!r}")
    print(f"Found {len(inputs)} input raster(s) in {INPUT_DIR!r}")
    t_all = time.perf_counter()                             # stopwatch for the whole batch
    for i, input_path in enumerate(inputs, 1):              # process each input in turn
        print(f"\n=== [{i}/{len(inputs)}] {input_path} ===")
        process_file(input_path)                            # SR + indices for this one file
    print(f"\nAll done: {len(inputs)} file(s) in {_fmt(time.perf_counter() - t_all)}")


if __name__ == "__main__":      # run main() only when executed directly, not when imported
    main()                      # entry point
