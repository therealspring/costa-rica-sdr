"""Do zonal stats by raster values."""
import argparse
import collections
import logging
import os
import shutil
import tempfile

from ecoshard import geoprocessing
from osgeo import gdal
import pandas
import numpy

gdal.SetCacheMax(2**28)
logging.basicConfig(
    level=logging.DEBUG,
    format=(
        '%(asctime)s (%(relativeCreated)d) %(levelname)s %(name)s'
        ' [%(funcName)s:%(lineno)d] %(message)s'))
LOGGER = logging.getLogger(__name__)


def _base_filename(path):
    """Get the filename without dir or extension."""
    return os.path.basename(os.path.splitext(path)[0])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Zonal stats by raster value.')
    parser.add_argument(
        'zone_raster_path', type=str, help='path to raster with integer zones')
    parser.add_argument(
        'value_raster_path', type=str,
        help='path to value raster to aggregate')
    parser.add_argument(
        '--interpolation_mode', default='near',
        help='interpolation mode for sampling, default "near"')
    args = parser.parse_args()

    target_zonal_table = f'''zonal_{args.value_raster_path}.csv'''

    working_dir = tempfile.mkdtemp(
        dir='.', prefix=_base_filename(target_zonal_table))

    raster_info = geoprocessing.get_raster_info(args.zone_raster_path)

    LOGGER.info(f'''aligning {_base_filename(args.value_raster_path)} to {
        _base_filename(args.zone_raster_path)}''')
    clipped_value_raster_path = os.path.join(
        working_dir, os.path.basename(args.value_raster_path))
    geoprocessing.warp_raster(
        args.value_raster_path, raster_info['pixel_size'],
        clipped_value_raster_path, args.interpolation_mode,
        target_bb=raster_info['bounding_box'],
        target_projection_wkt=raster_info['projection_wkt'])

    stats_dict = collections.defaultdict(lambda: {
        'min': numpy.iinfo(int).max,
        'max': numpy.iinfo(int).min,
        'count': 0.0,
        'sum': 0.0
    })

    offset_blocks = list(geoprocessing.iterblocks(
        (clipped_value_raster_path, 1), offset_only=True))

    zone_raster = gdal.OpenEx(args.zone_raster_path, gdal.OF_RASTER)
    zone_band = zone_raster.GetRasterBand(1)
    clipped_value_raster = gdal.OpenEx(
        clipped_value_raster_path, gdal.OF_RASTER)
    clipped_value_band = clipped_value_raster.GetRasterBand(1)
    value_nodata = clipped_value_band.GetNoDataValue()
    zone_nodata = zone_band.GetNoDataValue()

    LOGGER.info(f'collecting stats for {len(offset_blocks)} raster blocks')
    for offset_block in offset_blocks:
        value_array = clipped_value_band.ReadAsArray(**offset_block)
        zone_array = zone_band.ReadAsArray(**offset_block)
        valid_mask = ~numpy.isclose(value_array, value_nodata) & (
            zone_array != zone_nodata)
        valid_value = value_array[valid_mask]
        valid_zone = zone_array[valid_mask]
        for zone_val in numpy.unique(zone_array[valid_mask]):
            zone_mask = valid_zone == zone_val
            stats_dict[zone_val]['min'] = min(
                stats_dict[zone_val]['min'], numpy.min(valid_value[zone_mask]))
            stats_dict[zone_val]['max'] = max(
                stats_dict[zone_val]['max'], numpy.max(valid_value[zone_mask]))
            stats_dict[zone_val]['count'] += numpy.count_nonzero(zone_mask)
            stats_dict[zone_val]['sum'] += numpy.sum(valid_value[zone_mask])

    LOGGER.info(f'writing stats to {target_zonal_table}')
    zone_list = sorted(stats_dict)
    csv_table_dict = collections.defaultdict(list)
    for zone_id in zone_list:
        csv_table_dict['zone'].append(zone_id)
        csv_table_dict['min'].append(stats_dict[zone_id]['min'])
        csv_table_dict['max'].append(stats_dict[zone_id]['max'])
        csv_table_dict['count'].append(stats_dict[zone_id]['count'])
        csv_table_dict['sum'].append(stats_dict[zone_id]['sum'])
        if stats_dict[zone_id]['count'] > 0:
            csv_table_dict['mean'].append(
                stats_dict[zone_id]['sum']/stats_dict[zone_id]['count'])
        else:
            csv_table_dict['mean'].append(0)

    LOGGER.info(f'saving to {target_zonal_table}')
    df = pandas.DataFrame.from_dict(csv_table_dict)
    column_names = ['zone', 'min', 'max', 'mean', 'count', 'sum']
    df.to_csv(target_zonal_table, columns=column_names, index=False)

    zone_band = None
    zone_raster = None
    clipped_value_band = None
    clipped_value_raster = None

    try:
        shutil.rmtree(working_dir)
    except Exception:
        LOGGER.exception(f'could not clean up {working_dir}')
