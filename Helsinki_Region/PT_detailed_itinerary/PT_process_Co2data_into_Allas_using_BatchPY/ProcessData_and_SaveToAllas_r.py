import pandas as pd
import h3
import geopandas as gpd
import shapely




EM_BUS_PATH = "Data_CO2/emissions_bus_w_engine.csv"  # <-- set to your em_bus CSV
em_bus = pd.read_csv(EM_BUS_PATH)

# hard fail if the required columns are missing
required_cols = {"route_id", "assigned_engine_factor_gCO2_per_km"}
missing = required_cols - set(em_bus.columns)
if missing:
    raise KeyError(f"em_bus is missing columns: {missing}")

# ensure uniqueness + numeric factor
if em_bus["route_id"].duplicated().any():
    dups = em_bus.loc[em_bus["route_id"].duplicated(keep=False), "route_id"].unique()
    raise ValueError(f"Duplicate route_id(s) in em_bus: {list(dups)}")

em_bus["assigned_engine_factor_gCO2_per_km"] = pd.to_numeric(
    em_bus["assigned_engine_factor_gCO2_per_km"], errors="raise"
)

# simple deterministic mapping
route_factor_map = dict(
    zip(em_bus["route_id"].astype(str), em_bus["assigned_engine_factor_gCO2_per_km"].astype(float))
)



ghg_factors = pd.read_csv("Data_CO2/LCA_gCO2_per_pkm_by_transport_mode.csv",index_col=0)
ghg_factors.loc['Total_gCO2'] = ghg_factors.sum(axis=0)
ghg_factors.head()

# Create a function that returns travel mode specific co2 emission factors
# === replace your function ===
def CO2_emission_factors(row, ghg_factors, route_factor_map):
    """
    Returns a per-km gCO2 factor for a single segment row.
    - For BUS: uses route-specific factor from em_bus if available, else falls back to average bus factor.
    - For TRAM/SUBWAY/RAIL: uses Metro/urban train factor (as in your original code).
    - WALK and FERRY handled as before.
    """
    mode = row.get("mode")

    if mode == "WALK":
        return 0.0

    elif mode in ["TRAM", "SUBWAY", "RAIL"]:
        return float(ghg_factors.loc['Total_gCO2', ['Metro/urban train']].mean())

    elif mode == "BUS":
        # try to find a route identifier in the row
        route_key = None
        for k in ("route", "route_id", "route_short_name"):
            if k in row and pd.notna(row[k]):
                route_key = str(row[k])
                break

        # route-specific factor from em_bus if available
        if route_key and route_key in route_factor_map:
            return float(route_factor_map[route_key])

        # fallback: your original average over bus engines
        return float(ghg_factors.loc['Total_gCO2',
            ['Bus - ICE', 'Bus - HEV', 'Bus - BEV', 'Bus - BEV (two packs)', 'Bus - FCEV']].mean())

    elif mode == "FERRY":
        return 36.0  # TODO: replace with proper value when available

    else:
        raise ValueError(f"Unknown Transit mode found: {mode}")


import os, shutil
import glob    
data_dir = '/scratch/project_2011005/Helsinki_Region_Emission_Calculation/Codes_Car_PT_CO2_calculation_Sept2024/PT_detailed_itinerary/Batch_output'

out_dir = '/scratch/project_2011005/Helsinki_Region_Emission_Calculation/Codes_Car_PT_CO2_calculation_Sept2024/PT_detailed_itinerary/PT_process_Co2data_into_Allas_using_BatchPY/Helsinki_GTFS_April_2023/'

files = glob.glob(os.path.join(data_dir, '*/*.csv'))    

import pandas as pd
import boto3

# create connection s3
s3_client = boto3.client("s3", endpoint_url='https://a3s.fi')
# create connection s3
s3_resource = boto3.resource('s3', aws_access_key_id = "271ca5498e654d628fb064f5b922bfc7",aws_secret_access_key = "e12b4e8987d04b7bbc4e0c52e638c07d", endpoint_url='https://a3s.fi') 

# define bucket name and object name
raw_bucket_name = "Helsinki_GTFS_April_2023_rawcsv"
# create a new bucket
s3_resource.create_bucket(Bucket=raw_bucket_name)


##read local files one by one, upload to allas, process and save to local disk again , then upload to allas again 
for f in sorted(files):
    # Allas name
    object_name = ("/").join(f.split('/')[-1::])
    print(f)
    s3_resource.Object(raw_bucket_name, object_name).upload_file(f)

#my_bucket = s3_resource.Bucket(raw_bucket_name)
#for my_bucket_object in my_bucket.objects.all():
##    temp_object_name = my_bucket_object.key
##    f = temp_object_name.split("/")[-1]
##    response = s3_client.get_object(Bucket=raw_bucket_name, Key=temp_object_name)
##    temp_travel_details= pd.read_csv(response.get("Body"), sep=",")
    temp_travel_details= pd.read_csv(f)
    temp_travel_details['ghg_emission_factor'] = temp_travel_details.apply(lambda r: CO2_emission_factors(r, ghg_factors, route_factor_map), axis=1)

    temp_travel_details["GHG_emissions_in_grams"] = temp_travel_details["distance"]/1000 * temp_travel_details["ghg_emission_factor"]
    temp_df = pd.DataFrame()
    temp_df["pt_departure_time"] = temp_travel_details.groupby(["from_id", "to_id"])["departure_time"].unique().apply(lambda x:x[0])
    temp_df["pt_dist"] = temp_travel_details.groupby(["from_id", "to_id"])["total_distance"].unique().apply(lambda x:x[0])
    temp_df["pt_time"] = temp_travel_details.groupby(["from_id", "to_id"])["total_duration"].unique().apply(lambda x:x[0])
    temp_df["pt_co2"] = temp_travel_details.groupby(["from_id", "to_id"]).GHG_emissions_in_grams.sum()
    temp_df["pt_trip_seq"] = temp_travel_details.groupby(["from_id", "to_id"]).agg({"mode":pd.Series.to_list})
    temp_df["pt_time_seq"]= temp_travel_details.groupby(["from_id", "to_id"]).agg({"segment_duration":pd.Series.to_list})  #,])#.to_list()
    temp_df["pt_dist_seq"]= temp_travel_details.groupby(["from_id", "to_id"]).agg({"distance":pd.Series.to_list}) 
    temp_df["pt_unique_modes"] = temp_travel_details.groupby(["from_id", "to_id"])["mode"].nunique()
    temp_df.reset_index(inplace=True)
    temp_df["geometry"] =temp_df.to_id.apply(lambda x: shapely.geometry.Polygon(h3.h3_to_geo_boundary(x, geo_json=False)))
    new_fname =  out_dir + object_name[0:-4] + "_wCO2.parquet"
    gpd.GeoDataFrame(temp_df.round(2),crs="EPSG:4326").to_parquet(new_fname,compression=None)

    
### Upload to allas
output_bucket_name = "Helsinki_GTFS_April_2023_wCO2"#"Helsinki_PT_detailed_itinerary_r5r_wCO2"
# create connection s3
s3_resource = boto3.resource('s3', aws_access_key_id = "271ca5498e654d628fb064f5b922bfc7",aws_secret_access_key = "e12b4e8987d04b7bbc4e0c52e638c07d",endpoint_url='https://a3s.fi')
# list uploaded files in Bucket

#data_bucket_name = "Helsinki_PT_detailed_itinerary_r5r_wCO2data"
# create a new bucket
s3_resource.create_bucket(Bucket=output_bucket_name)
#s3_resource.Object(output_bucket_name, object_name).upload_file(source_path)
import os, glob
data_dir = out_dir  # 'Helsinki_GTFS_April_2023/Batch_output_Transit_r5r_wco2_parquet/'
my_out_bucket = s3_resource.Bucket(output_bucket_name)

files = glob.glob(os.path.join(data_dir, '*.parquet'))
for source_file_path in sorted(files):
    
    # Allas name
    object_name = ("/").join(source_file_path.split('/')[-1::])

    print(object_name)
    s3_resource.Object(output_bucket_name, object_name).upload_file(source_file_path)
