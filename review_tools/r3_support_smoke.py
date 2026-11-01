"""Standalone R3 support diagnostic (no external raster downloads)."""
import json
from geopanel_eo.config import Window
from geopanel_eo.support import spatial_support_diagnostics
from types import SimpleNamespace
var=SimpleNamespace(variable_id='synthetic_temperature',temporal_window=Window(frequency='annual',months=[1,2]),temporal_reducer='mean')
records=[]
for month,missing in [(1,0),(2,9)]:
    records.append(dict(entity_id='u',source_month=f'2020-{month:02d}',grid_signature=('same_grid',),
                        valid_cells={i:10.0 for i in range(10) if i!=missing},
                        total_unit_area=100.0,spatial_value_usable=True))
print(json.dumps(spatial_support_diagnostics(records,var,[dict(entity_id='u',time='2020')]),indent=2,ensure_ascii=False))
