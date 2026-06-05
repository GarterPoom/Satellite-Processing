import ee
import geemap
import datetime

# Authenticate and initialize Earth Engine
ee.Authenticate()
ee.Initialize(project='ee-siripoomsu') # Replace with your project ID

# =============================
# 1. Define AOI = Central Thailand
# =============================
# Rough bounding box for Central Thailand region
# Latitude approx: 12.0 N to 18.0 N
# Longitude approx: 98.0 E to 103.0 E
aoi = ee.Geometry.Rectangle([98.0, 12.0, 103.0, 18.0])

#aoi = ee.Geometry.Polygon([
    #[[100.45, 13.70],
     #[100.85, 13.70],
     #[100.85, 13.95],
     #[100.45, 13.95]]
#])

# Pre- and post-flood periods
pre_flood_start = '2025-04-01' # Changed to a past date (dry season)
pre_flood_end   = '2025-04-30'
post_flood_start = '2025-11-01' # Changed to a past date (peak flood season)
post_flood_end   = '2025-11-10'

# =============================
# 2. Load Sentinel-1 GRD Data
# =============================
def get_s1_collection(start, end):
    collection = (ee.ImageCollection('COPERNICUS/S1_GRD')
                  .filterDate(start, end)
                  .filterBounds(aoi)
                  .filter(ee.Filter.eq('instrumentMode', 'IW'))
                  .filter(ee.Filter.eq('orbitProperties_pass', 'DESCENDING'))
                  .filter(ee.Filter.eq('resolution_meters', 10))
                  .filter(ee.Filter.listContains('transmitterReceiverPolarisation', 'VV')))
    return collection.mean().select('VV')

pre_flood = get_s1_collection(pre_flood_start, pre_flood_end)
post_flood = get_s1_collection(post_flood_start, post_flood_end)

# =============================
# 3. Compute Flooded Areas
# =============================
# Compute change in backscatter (in dB)
diff = post_flood.subtract(pre_flood)

# Threshold (tune this value)
threshold = -1.5
flood_mask = diff.lt(threshold)

# Mask out permanent water (using JRC water dataset)
jrc = ee.Image('JRC/GSW1_4/GlobalSurfaceWater')
perm_water = jrc.select('occurrence').gt(80)
flood_mask = flood_mask.And(perm_water.Not())

# =============================
# 4. Visualization
# =============================
Map = geemap.Map(center=[13.8, 100.65], zoom=11)
Map.addLayer(pre_flood, {'min': -20, 'max': 0}, 'Pre-flood VV')
Map.addLayer(post_flood, {'min': -20, 'max': 0}, 'Post-flood VV')
Map.addLayer(diff, {'min': -5, 'max': 5, 'palette': ['red', 'white', 'blue']}, 'VV Change')
Map.addLayer(flood_mask.updateMask(flood_mask),
             {'palette': ['0000FF']}, 'Flooded Areas')

Map.addLayerControl()   
Map
