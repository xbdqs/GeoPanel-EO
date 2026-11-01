"""Offline unit tests for R5 follow-up diagnostic, no real data needed."""
from pathlib import Path
import sys
import numpy as np
import rasterio
import pytest
from rasterio.transform import from_origin
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from review_tools.chicago_nlcd_hash_diagnose import classify_semantics, raster_fingerprint, sha256, walk_matching_files


def config(label='monthly_snapshot'):
    return {'sources': [
        {'source_id':f'annual_nlcd_{p}', 'timestamps':['2019-01-01','2020-01-01'],
         'paths':[f'annual_nlcd_c1_2_{p}_{y}_chicago.tif' for y in (2019,2020)]}
        for p in ('landcover','impervious')],
        'variables': [{'variable_id':v,'temporal_support':label,'temporal_reducer':'snapshot',
                       'temporal_window':{'frequency':'annual','months':[1]}}
                      for v in ('landcover','impervious_percent')]}


def test_legacy_label_is_disclosed_not_retroactively_passed():
    s=classify_semantics(config())
    assert s['landcover'].startswith('LEGACY_')
    assert s['impervious_percent'].startswith('LEGACY_')
    assert s['annual_nlcd_landcover']=='YEAR_ANCHORS_AND_FILES_VALID'


def test_modern_label_and_wrong_anchor():
    modern=config('annual_product')
    assert classify_semantics(modern)['landcover']=='MODERN_ANNUAL_PRODUCT'
    modern['sources'][0]['timestamps'][0]='2019-12-31'
    assert classify_semantics(modern)['annual_nlcd_landcover']=='YEAR_ANCHOR_OR_FILENAME_MISMATCH'


def test_raster_metadata_and_checksum(tmp_path):
    p=tmp_path/'annual_nlcd_c1_2_landcover_2019_chicago.tif'
    with rasterio.open(p,'w',driver='GTiff',height=2,width=3,count=1,dtype='uint8',crs='EPSG:5070',nodata=250,
                       transform=from_origin(400000,2000000,30,30)) as ds:
        ds.write(np.array([[11,21,250],[24,41,95]],dtype='uint8'),1)
    meta=raster_fingerprint(p,{'width':3,'height':2,'crs':'EPSG:5070','valid_pixels':5,'minimum':11,'maximum':95,'size_bytes':p.stat().st_size},'landcover')
    assert meta['all_comparable_metadata_match']
    assert meta['valid_range']
    assert sha256(p)==sha256(p)
    found=walk_matching_files([tmp_path],{p.name})
    assert len(found[p.name])==1


def test_wrong_raster_metadata_is_detected(tmp_path):
    p=tmp_path/'annual_nlcd_c1_2_impervious_2020_chicago.tif'
    with rasterio.open(p,'w',driver='GTiff',height=2,width=2,count=1,dtype='uint8',crs='EPSG:5070',nodata=250,
                       transform=from_origin(400000,2000000,30,30)) as ds:
        ds.write(np.array([[10,20],[30,40]],dtype='uint8'),1)
    meta=raster_fingerprint(p,{'width':3,'height':2},'impervious')
    assert not meta['all_comparable_metadata_match']
