#!/usr/bin/env python3
"""
Local Sentinel-1 Flood Mapping

Usage (example):
  python flood_mapping_s1.py \
    --pre-dir D:/data/s1/pre \
    --dur-dir D:/data/s1/during \
    --out-dir D:/data/s1/out \
    --bands VV,VH \
    --units db \
    --change-thresh-db 1.5 \
    --dur-max-db -13 \
    --use-otsu-change false \
    --speckle-kernel 3

Place Sentinel-1 GRD backscatter GeoTIFFs in the pre- and during-event folders.
Files may be single-pol (VV or VH) or multi-band. Polarity is detected from
band names/descriptions or filenames (containing 'VV'/'VH').

Outputs in out-dir:
  - flood_mask.tif (Byte 0/1)
  - change_max_drop_db.tif (Float32)
  - during_mean_db.tif (Float32)

No external datasets required. Works on a single AOI grid; inputs are reprojected
and resampled to match the first discovered raster as a reference grid.
"""

import argparse
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.warp import reproject
from rasterio.io import DatasetReader
from rasterio.transform import Affine
from skimage.filters import threshold_otsu
from scipy.ndimage import median_filter


# ---------------------------- IO HELPERS ----------------------------

def list_tifs(directory: Path) -> List[Path]:
    return sorted([p for p in directory.rglob('*.tif')] + [p for p in directory.rglob('*.tiff')])


def detect_pol_from_name_or_desc(path: Path, band_index: int, dataset: Optional[DatasetReader]) -> Optional[str]:
    name = path.name.upper()
    if 'VV' in name and 'VH' not in name:
        return 'VV'
    if 'VH' in name and 'VV' not in name:
        return 'VH'
    # Try band description
    if dataset is not None:
        try:
            desc = dataset.descriptions[band_index - 1]
            if desc:
                desc_u = desc.upper()
                if 'VV' in desc_u and 'VH' not in desc_u:
                    return 'VV'
                if 'VH' in desc_u and 'VV' not in desc_u:
                    return 'VH'
        except Exception:
            pass
    return None


def open_as_reference(path: Path) -> Tuple[DatasetReader, np.ndarray]:
    ds = rasterio.open(path)
    arr = ds.read(1, masked=True)
    return ds, arr


def reproject_to_reference(src: DatasetReader, src_band: int, ref_profile: dict, ref_shape: Tuple[int, int]) -> np.ndarray:
    dst = np.zeros(ref_shape, dtype=np.float32)
    reproject(
        source=rasterio.band(src, src_band),
        destination=dst,
        src_transform=src.transform,
        src_crs=src.crs,
        dst_transform=ref_profile['transform'],
        dst_crs=ref_profile['crs'],
        dst_nodata=np.nan,
        resampling=Resampling.bilinear,
    )
    return dst


def stack_median(paths: List[Path], pol: str, ref_profile: dict, ref_shape: Tuple[int, int]) -> Optional[np.ndarray]:
    tiles: List[np.ndarray] = []
    for p in paths:
        try:
            with rasterio.open(p) as ds:
                band_indices = range(1, ds.count + 1)
                picked = []
                for b in band_indices:
                    pol_detected = detect_pol_from_name_or_desc(p, b, ds)
                    if pol_detected is None:
                        # If unknown and only one band, assume it matches requested pol when filename hints both missing
                        if ds.count == 1:
                            pol_detected = pol
                        else:
                            continue
                    if pol_detected == pol:
                        picked.append(b)
                if not picked:
                    continue
                for b in picked:
                    data = reproject_to_reference(ds, b, ref_profile, ref_shape)
                    tiles.append(data)
        except Exception:
            continue
    if not tiles:
        return None
    stack = np.stack(tiles, axis=0)
    # Ignore zeros when they likely represent nodata; convert zeros to nan if majority zeros
    zero_ratio = np.mean(stack == 0)
    if zero_ratio > 0.5:
        stack = stack.astype(np.float32)
        stack[stack == 0] = np.nan
    median = np.nanmedian(stack, axis=0).astype(np.float32)
    return median


# ---------------------------- PROCESSING ----------------------------

def ensure_db(arr: np.ndarray, units: str) -> np.ndarray:
    if units.lower() == 'db':
        return arr
    if units.lower() in ('linear', 'lin'):
        with np.errstate(divide='ignore'):
            return 10.0 * np.log10(np.clip(arr, 1e-12, None)).astype(np.float32)
    raise ValueError("units must be 'db' or 'linear'")


def smooth_speckle(arr: np.ndarray, kernel: int) -> np.ndarray:
    if kernel is None or kernel <= 1:
        return arr
    # Use odd kernel size
    k = int(kernel)
    if k % 2 == 0:
        k += 1
    arr_f = arr.astype(np.float32)
    # median filter ignores NaNs poorly; replace NaNs with local median via two passes
    nan_mask = np.isnan(arr_f)
    filled = arr_f.copy()
    # Temporary fill NaNs with global median to run filter
    global_med = np.nanmedian(arr_f)
    filled[nan_mask] = global_med
    filtered = median_filter(filled, size=k, mode='nearest')
    # Where original was valid, keep filtered; where original NaN, keep filtered as estimate
    out = filtered
    return out


def compute_change_and_flood(
    pre_composites: Dict[str, np.ndarray],
    dur_composites: Dict[str, np.ndarray],
    change_thresh_db: Optional[float],
    dur_max_db: Optional[float],
    use_otsu_change: bool,
    use_otsu_dark: bool,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, Dict[str, float]]:
    present_pols = sorted(set(pre_composites.keys()) & set(dur_composites.keys()))
    if not present_pols:
        raise RuntimeError('No overlapping polarizations between pre and during composites')

    changes = []
    dur_list = []
    for pol in present_pols:
        pre_db = pre_composites[pol]
        dur_db = dur_composites[pol]
        change = pre_db - dur_db  # positive if backscatter dropped
        changes.append(change)
        dur_list.append(dur_db)

    change_max = np.nanmax(np.stack(changes, axis=0), axis=0).astype(np.float32)
    dur_mean = np.nanmean(np.stack(dur_list, axis=0), axis=0).astype(np.float32)

    # Thresholds
    applied = {}
    if use_otsu_change:
        valid = np.isfinite(change_max)
        if np.count_nonzero(valid) == 0:
            raise RuntimeError('No valid pixels to compute Otsu threshold for change')
        change_thresh_db = float(threshold_otsu(change_max[valid]))
    if use_otsu_dark:
        valid = np.isfinite(dur_mean)
        if np.count_nonzero(valid) == 0:
            raise RuntimeError('No valid pixels to compute Otsu threshold for darkness')
        # Otsu on inverted brightness to find dark cluster tends to be unstable; operate directly on dur_mean
        dur_max_db = float(threshold_otsu(dur_mean[valid]))

    if change_thresh_db is None or dur_max_db is None:
        raise ValueError('Thresholds not fully specified or derived')

    applied['change_thresh_db'] = float(change_thresh_db)
    applied['dur_max_db'] = float(dur_max_db)

    flood_change = change_max >= change_thresh_db
    flood_dark = dur_mean <= dur_max_db
    flood = np.logical_and(flood_change, flood_dark)

    return change_max, dur_mean, flood.astype(np.uint8), applied


# ---------------------------- WRITER ----------------------------

def write_geotiff(path: Path, profile: dict, array: np.ndarray, dtype: str, nodata: Optional[float] = None) -> None:
    prof = profile.copy()
    prof.update(
        driver='GTiff',
        height=array.shape[0],
        width=array.shape[1],
        count=1,
        dtype=dtype,
        compress='deflate',
        tiled=True,
        nodata=nodata,
        BIGTIFF='IF_SAFER',
    )
    # Replace NaNs where dtype is integer
    data = array
    if np.issubdtype(np.dtype(dtype), np.integer):
        if nodata is None:
            nodata = 0
            prof['nodata'] = nodata
        data = np.where(np.isfinite(array), array, nodata).astype(dtype)
    with rasterio.open(path, 'w', **prof) as dst:
        dst.write(data.astype(dtype), 1)


# ---------------------------- MAIN ----------------------------

def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description='Local Sentinel-1 flood mapping from folders of GeoTIFFs')
    ap.add_argument('--pre-dir', required=True, help='Folder with pre-event S1 GeoTIFFs')
    ap.add_argument('--dur-dir', required=True, help='Folder with during/post-event S1 GeoTIFFs')
    ap.add_argument('--out-dir', required=True, help='Output folder')
    ap.add_argument('--bands', default='VV,VH', help='Comma-separated list of bands to use (VV,VH)')
    ap.add_argument('--units', default='db', choices=['db', 'linear'], help='Input backscatter units')
    ap.add_argument('--speckle-kernel', type=int, default=3, help='Median filter kernel size (pixels), 0/1 to disable')
    ap.add_argument('--use-otsu-change', type=lambda x: str(x).lower() in ['1','true','yes'], default=False, help='Use Otsu threshold for change')
    ap.add_argument('--use-otsu-dark', type=lambda x: str(x).lower() in ['1','true','yes'], default=False, help='Use Otsu threshold for darkness')
    ap.add_argument('--change-thresh-db', type=float, default=1.5, help='Fixed threshold for (pre - dur) in dB')
    ap.add_argument('--dur-max-db', type=float, default=-13.0, help='Max during-flood backscatter (dB)')
    return ap.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)

    pre_dir = Path(args.pre_dir)
    dur_dir = Path(args.dur_dir)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    bands = [b.strip().upper() for b in args.bands.split(',') if b.strip()]
    use_vv = 'VV' in bands
    use_vh = 'VH' in bands
    if not use_vv and not use_vh:
        print('No valid bands requested; use VV and/or VH', file=sys.stderr)
        return 2

    pre_paths = list_tifs(pre_dir)
    dur_paths = list_tifs(dur_dir)
    if not pre_paths:
        print('No GeoTIFFs found in pre-dir', file=sys.stderr)
        return 2
    if not dur_paths:
        print('No GeoTIFFs found in dur-dir', file=sys.stderr)
        return 2

    # Establish reference from first pre image
    with rasterio.open(pre_paths[0]) as ref:
        ref_profile = ref.profile.copy()
        ref_profile.update(count=1)
        ref_shape = (ref.height, ref.width)

    pre_comp: Dict[str, np.ndarray] = {}
    dur_comp: Dict[str, np.ndarray] = {}

    if use_vv:
        pre_vv = stack_median(pre_paths, 'VV', ref_profile, ref_shape)
        dur_vv = stack_median(dur_paths, 'VV', ref_profile, ref_shape)
        if pre_vv is not None and dur_vv is not None:
            pre_comp['VV'] = ensure_db(pre_vv, args.units)
            dur_comp['VV'] = ensure_db(dur_vv, args.units)

    if use_vh:
        pre_vh = stack_median(pre_paths, 'VH', ref_profile, ref_shape)
        dur_vh = stack_median(dur_paths, 'VH', ref_profile, ref_shape)
        if pre_vh is not None and dur_vh is not None:
            pre_comp['VH'] = ensure_db(pre_vh, args.units)
            dur_comp['VH'] = ensure_db(dur_vh, args.units)

    if not pre_comp or not dur_comp:
        print('Failed to build composites for requested bands. Check inputs/band detection.', file=sys.stderr)
        return 2

    # Speckle smoothing
    for k in list(pre_comp.keys()):
        pre_comp[k] = smooth_speckle(pre_comp[k], args.speckle_kernel)
    for k in list(dur_comp.keys()):
        dur_comp[k] = smooth_speckle(dur_comp[k], args.speckle_kernel)

    change_max, dur_mean, flood_mask, applied = compute_change_and_flood(
        pre_comp, dur_comp,
        args.change_thresh_db if not args.use_otsu_change else None,
        args.dur_max_db if not args.use_otsu_dark else None,
        use_otsu_change=args.use_otsu_change,
        use_otsu_dark=args.use_otsu_dark,
    )

    # Write outputs
    write_geotiff(out_dir / 'change_max_drop_db.tif', ref_profile, change_max, 'float32', nodata=np.nan)
    write_geotiff(out_dir / 'during_mean_db.tif', ref_profile, dur_mean, 'float32', nodata=np.nan)
    write_geotiff(out_dir / 'flood_mask.tif', ref_profile, flood_mask.astype(np.uint8), 'uint8', nodata=0)

    # Save a small metadata txt
    meta_txt = out_dir / 'flood_params.txt'
    with open(meta_txt, 'w', encoding='utf-8') as f:
        f.write('Applied thresholds:\n')
        for k, v in applied.items():
            f.write(f'- {k}: {v}\n')
        f.write(f'Bands used: {sorted(set(pre_comp.keys()) & set(dur_comp.keys()))}\n')
        f.write(f'Units: db\n')

    print('Done. Outputs written to:', str(out_dir))
    print('Applied thresholds:', applied)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
