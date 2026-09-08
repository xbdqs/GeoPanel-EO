"""Generate publication-ready SoftwareX evidence assets from the locked Stage 5 result."""
from pathlib import Path
import csv
import json
import math

import matplotlib.pyplot as plt
from matplotlib.collections import PolyCollection
from matplotlib.colors import Normalize
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
OUT = ROOT / "output"
OUT.mkdir(exist_ok=True)

plt.rcParams.update({
    "font.family": "DejaVu Sans", "font.size": 8.5,
    "axes.labelsize": 8.5, "axes.titlesize": 9.5,
    "figure.dpi": 160, "savefig.dpi": 600,
    "axes.spines.top": False, "axes.spines.right": False,
})


def save(fig, stem):
    fig.savefig(OUT / f"{stem}.png", bbox_inches="tight", facecolor="white")
    fig.savefig(OUT / f"{stem}.pdf", bbox_inches="tight", facecolor="white")
    plt.close(fig)


def figure1():
    fig, ax = plt.subplots(figsize=(10.2, 4.4))
    ax.set_xlim(0, 10.2); ax.set_ylim(0, 4.4); ax.axis("off")
    boxes = [
        (0.25, 2.55, 1.65, 1.1, "Inputs", "NetCDF / GeoTIFF\npolygons / time"),
        (2.3, 2.55, 1.65, 1.1, "Semantic contract", "state / accumulation\nclass / support"),
        (4.35, 2.55, 1.65, 1.1, "Spatial engine", "CRS validation\narea-weighted overlay"),
        (6.4, 2.55, 1.65, 1.1, "Temporal engine", "window completeness\nsum / mean / snapshot"),
        (8.45, 2.55, 1.5, 1.1, "Panel outputs", "long / wide\nCSV / Parquet / DTA"),
        (1.65, 0.55, 2.0, 1.2, "Diagnostics", "coverage / weights\neffective sample size"),
        (4.10, 0.55, 2.0, 1.2, "Provenance", "resolved configuration\nmetadata / SHA-256 hashes"),
        (6.55, 0.55, 2.0, 1.2, "Quality report", "balance / quality flags\nmachine + human readable"),
    ]
    for x,y,w,h,title,body in boxes:
        ax.add_patch(plt.Rectangle((x,y),w,h,facecolor="#eef4f8",edgecolor="#315b75",lw=1.1))
        ax.text(x+w/2,y+h*.68,title,ha="center",va="center",weight="bold",color="#173a4f")
        ax.text(x+w/2,y+h*.31,body,ha="center",va="center",fontsize=7.1,color="#33434c")
    for x1,x2 in [(1.9,2.3),(3.95,4.35),(6.0,6.4),(8.05,8.45)]:
        ax.annotate("",(x2,3.1),(x1,3.1),arrowprops=dict(arrowstyle="->",lw=1.2,color="#607d8b"))
    for x0,x1 in [(2.65,3.125),(5.10,5.175),(7.55,7.225)]:
        ax.annotate("",(x1,2.55),(x0,1.75),arrowprops=dict(arrowstyle="->",lw=1.1,color="#607d8b"))
    save(fig,"Figure_1_GeoPanel_EO_workflow")


def polygons_with_values(geo, values):
    parts, colors = [], []
    for f in geo["features"]:
        entity = f["properties"]["entity_id"]
        geom = f["geometry"]
        polys = [geom["coordinates"]] if geom["type"] == "Polygon" else geom["coordinates"]
        for poly in polys:
            parts.append(np.asarray(poly[0],float)); colors.append(values[entity])
    return parts, np.asarray(colors)


def figure2():
    geo=json.loads((CASE_DATA/"chicago_community_areas_77.geojson").read_text(encoding="utf8"))
    d=pd.read_csv(CASE_DATA/"combined_panel_wide.csv",dtype={"entity_id":str,"time":str})
    d=d[d.time=="2019"].copy()
    specs=[("impervious_percent","Impervious surface","%","YlOrRd"),
           ("landcover_class_24_fraction","High-intensity developed","fraction","OrRd"),
           ("prcp","Annual precipitation","mm","Blues"),
           ("tmax","Annual mean maximum temperature","°C","magma")]
    fig,axs=plt.subplots(2,2,figsize=(8.2,9.0),constrained_layout=True)
    for label,(col,title,unit,cmap) in zip("abcd",specs):
        ax=axs.flat["abcd".index(label)]
        vals=dict(zip(d.entity_id,d[col])); parts,c=polygons_with_values(geo,vals)
        norm=Normalize(float(c.min()),float(c.max()))
        coll=PolyCollection(parts,array=c,cmap=cmap,norm=norm,edgecolor="white",linewidth=.25)
        ax.add_collection(coll); ax.autoscale_view(); ax.set_aspect("equal"); ax.axis("off")
        cb=fig.colorbar(coll,ax=ax,shrink=.74,pad=.01); cb.set_label(unit)
        ax.set_title(f"({label}) {title}",loc="left")
    save(fig,"Figure_2_Chicago_case_maps_2019")


def figure3():
    d=pd.read_csv(FIGURE_DATA/"exact_vs_center_77.csv")
    specs=[("prcp","Annual precipitation (mm)"),("tmax","Maximum temperature (°C)"),("tmin","Minimum temperature (°C)")]
    fig,axs=plt.subplots(1,3,figsize=(10.8,3.55),constrained_layout=True)
    for ax,(var,label),letter in zip(axs,specs,"abc"):
        q=d[d.variable_id==var]
        x=q.exact_area_weighted_value.to_numpy(); y=q.center_point_value.to_numpy()
        lo=min(x.min(),y.min()); hi=max(x.max(),y.max()); pad=(hi-lo)*.04
        ax.scatter(x,y,s=12,alpha=.58,color="#246a8d",edgecolors="none")
        ax.plot([lo-pad,hi+pad],[lo-pad,hi+pad],color="#b23a48",lw=1,ls="--")
        ax.set(xlim=(lo-pad,hi+pad),ylim=(lo-pad,hi+pad),xlabel="Exact area-weighted",ylabel="Cell-centre approximation")
        ax.set_title(f"({letter}) {label}",loc="left")
        ax.grid(alpha=.18,lw=.5)
    save(fig,"Figure_3_exact_vs_cell_centre")


def figure4():
    d=pd.read_csv(CASE_DATA/"combined_panel_long.csv",dtype={"entity_id":str,"time":str})
    q=d[((d.variable_id=="prcp")|(d.variable_id=="impervious_percent")) & (d.time=="2019")].copy()
    fig,axs=plt.subplots(1,2,figsize=(8.4,3.7),constrained_layout=True)
    for ax,var,title,letter,color in [
        (axs[0],"prcp","Daymet 1 km","a","#2a6fbb"),
        (axs[1],"impervious_percent","Annual NLCD 30 m","b","#dd6b20")]:
        z=q[q.variable_id==var]
        ax.scatter(z.total_unit_area/1e6,z.effective_sample_size,s=18,alpha=.7,color=color,edgecolors="none")
        ax.set_xscale("log"); ax.set_yscale("log")
        ax.set_xlabel("Community area (km²)"); ax.set_ylabel("Effective sample size")
        ax.set_title(f"({letter}) {title}",loc="left"); ax.grid(alpha=.2,which="both",lw=.5)
    save(fig,"Figure_4_spatial_support_diagnostics")


def tables():
    report=json.loads((CASE_DATA/"stage5_report.json").read_text())
    rows=[
        ["Analysis units","Chicago community areas","Vector polygons","77","CRS normalization and geometry validation"],
        ["Climate","Daymet V4 R1","NetCDF; 1 km","2019–2020 monthly","Coverage-weighted mean; sum or mean over time"],
        ["Land cover","Annual NLCD C1.2","GeoTIFF; 30 m","2019–2020 annual","Area and fraction by declared class"],
        ["Impervious surface","Annual NLCD C1.2","GeoTIFF; 30 m","2019–2020 annual","Coverage-weighted mean"],
    ]
    pd.DataFrame(rows,columns=["Role","Dataset","Format_resolution","Temporal_extent","GeoPanel_EO_operation"]).to_csv(TABLE_OUT/"Table_1_case_inputs.csv",index=False)
    metrics=[
        ["Community units",77,"count"],["Monthly climate panel",report["daymet_monthly_rows"],"rows"],
        ["Annual climate panel",report["daymet_annual_rows"],"rows"],["Annual NLCD panel",report["nlcd_rows"],"rows"],
        ["Combined long panel",report["combined_long_rows"],"rows"],["Combined wide panel",report["combined_wide_rows"],"rows"],
        ["Total workflow",report["timings_seconds"]["total"],"seconds"],
    ]
    pd.DataFrame(metrics,columns=["Metric","Value","Unit"]).to_csv(TABLE_OUT/"Table_2_case_validation.csv",index=False)
    pd.read_csv(FIGURE_DATA/"comparison_summary_77.csv").to_csv(TABLE_OUT/"Table_3_weighting_comparison.csv",index=False)


if __name__ == "__main__":
    figure1(); figure2(); figure3(); figure4(); tables()
    print("SOFTWAREX_ASSETS_PASS")
