from flask import Flask, jsonify, request, render_template
from flask_restful import Resource, Api
from flask_cache import Cache
import sys
import requests
import json
import pandas as pd
import datetime
from colour import Color
import datetime
from predictions import Predictions
import random
from scipy.spatial import ConvexHull


app = Flask(__name__)

# set up api and caching
api = Api(app)
cache = Cache(app, config={'CACHE_TYPE': 'simple'})

with app.app_context():
	# set context for future api calls
	station_url = "https://gbfs.citibikenyc.com/gbfs/en/station_information.json"
	stations = requests.get(station_url)

	station_json = json.loads(stations.text)
	stations_array = station_json["data"]["stations"]

	stations = pd.DataFrame.from_records(stations_array)
	stations.drop(['region_id', 'rental_methods', "eightd_has_key_dispenser"], axis=1, inplace=True)

	# remove stations with no capacity
	stations = stations[stations["capacity"] != 0]
	
	# get predictions
	#preds = pd.read_csv("https://raw.githubusercontent.com/cdepeuter/citibike/master/preds/poisson_preds.csv")
	#preds = pd.read_csv("https://raw.githubusercontent.com/cdepeuter/citibike/master/preds/poisson_preds.csv")
	preds = pd.read_csv("https://raw.githubusercontent.com/cdepeuter/citibike/master/preds/station_ar_preds.csv")
	preds["station_id"] = preds.station_id.astype('str')
	preds["bike_delta"] = preds["avg(in_count)"] - preds["avg(out_count)"]
	
	# get cluster assignments
	ar_preds = pd.read_csv("https://raw.githubusercontent.com/cdepeuter/citibike/master/preds/ar_preds.csv")
	clusters = pd.read_csv("https://raw.githubusercontent.com/cdepeuter/citibike/master/preds/clusters_new.csv")
	clusters["ID"] = clusters.ID.astype('str')
	clusters.drop(["end_latitude", "end_longitude"], inplace=True, axis=1)
	
	# get color range for station status and bike angels
	red = Color("red")
	black = Color("black")
	colors = list(red.range_to(Color("green"),101))
	cluster_colors = list(Color('yellow').range_to(Color('blue'), len(set(clusters["cluster"])) + 1 ))
	random.shuffle(cluster_colors)


@app.route('/')
def index():
    return render_template('leaflet.html')


@app.route("/map")
def map():
	return render_template('leaflet.html')


def boundRoundPercentage(x):
		# for a percentage, make sure its >= 0, <= 100, an integer
		x = round(x)
		if x < 0:
			x = 0
		if x > 100:
			x = 100

		return x


# get convex hull for points
def get_hull(x):
    #print(x.columns)
    points = list(zip(x["lat"].values, x["lon"].values))

    # if not enough points for a hull just return (will remove)
    if len(points) <= 3:
    	return {"remove": True}

    thisHull = ConvexHull(points)
    coords = [[points[p][1],points[p][0]] for p in reversed(thisHull.vertices) ]
    # complete loop
    coords.append(coords[0])
    capacity = sum(x["capacity"])
    available = sum(x["num_bikes_available"])
    expected_change = x["in_rate"].values[0] - x["out_rate"].values[0]
    expected = int(available + expected_change)
    expected_pct = int(boundRoundPercentage(100 * expected / capacity))
    avaliable_pct = int(boundRoundPercentage(100* available / capacity))

    respData = {
                "type": "Feature", 'id': x['station_id'].values[0], 
                "properties": { "capacity" : str(capacity), "available":str(available), 
                               "predcolor":colors[expected_pct].hex,
                               "statuscolor":colors[avaliable_pct].hex, 
                               "expected": str(expected),
                               "rate":str(expected_change),
                               "id": x['station_id'].values[0],},
                               
                "geometry": {"type": "Polygon","coordinates": [coords]},
                "style" : {"fill":colors[expected_pct].hex}
               }
    
    # dont want to return hull of not clustered points
    if thisHull.area > .4:
        respData["remove"] = True
      
    return respData


class Stations(Resource):	
	@cache.cached(timeout=50)
	def get(self):
		# get current status and merge that with stations
		status_url = "https://gbfs.citibikenyc.com/gbfs/en/station_status.json"
		status_req = requests.get(status_url)
		status_json = json.loads(status_req.text)
		station_status_array = status_json["data"]["stations"]

		# put into data frame
		station_status = pd.DataFrame.from_records(station_status_array)
		station_status.drop(["eightd_has_available_keys", "last_reported", "is_installed", "is_renting", "is_returning"], axis=1, inplace=True)

		# add bike angels score, put into data frame
		# bike_angels_url = "https://bikeangels-api.citibikenyc.com/bikeangels/v1/geojson_scores"
		# angels_status_req = requests.get(bike_angels_url)
		# angels_status_json = json.loads(angels_status_req.text)
		# angels_station_array = angels_status_json["features"]
		# angels_stations = [{"station_id" : str(stat["properties"]["id"]), "score": stat["properties"]["score"]}  for stat in angels_station_array]
		# angels_stations_df = pd.DataFrame.from_records(angels_stations)
		#print("max score", angels_stations_df.score.max(), angels_stations_df.score.min())
		# create colors for angels here cuz max score varies
		#sscore_colors = list(black.range_to(Color("white"),angels_stations_df.score.max() + 1 - angels_stations_df.score.min()))

		# merge stations and scores
		station_status = station_status.merge(stations, on = 'station_id')
		# station_status = station_status.merge(angels_stations_df, on = 'station_id',  how='left')
		#station_status.score.fillna(0, inplace=True)

		# merge predictions
		today = datetime.datetime.today()
		thisHour = today.hour
		thisWeekday = today.weekday() < 6

		relevant_preds = preds[(preds["weekday"] == thisWeekday) & (preds["hour"] == thisHour)].copy()

		relevant_preds.drop(["weekday", "hour"], inplace=True, axis=1)
		station_status = station_status.merge(relevant_preds, on = 'station_id',  how='left')

		station_status["avg(out_count)"].fillna(0, inplace=True)
		station_status["avg(in_count)"].fillna(0, inplace=True)
		station_status["bike_delta"].fillna(0, inplace=True)

		station_status["future_stock"] = station_status["num_bikes_available"] + station_status["bike_delta"]

		station_status["future_pct_available"] = 100 * station_status["future_stock"] / station_status["capacity"]
		station_status["future_pct_available"].fillna(0, inplace=True)
		station_status["future_pct_available"] = station_status["future_pct_available"].map(boundRoundPercentage)	
		
		station_status["pct_available"] = 100 * station_status["num_bikes_available"] / station_status["capacity"]
		station_status.pct_available.fillna(0, inplace=True)
		station_status["pct_available"] = station_status["pct_available"].map(boundRoundPercentage)	

		# add cluster assignemnt
		station_status = station_status.merge(clusters, left_on="station_id", right_on="ID", how='left')
		station_status.cluster.fillna(0, inplace=True)
		station_status.ID.fillna(0, inplace=True)

		# get color for dashboard
		#negative_colors = -1 * angels_stations_df.score.min()
		station_status["status_color"] = station_status["pct_available"].map(lambda x: colors[int(x)].hex)
		#station_status["score_color"] = station_status["score"].map(lambda x: score_colors[int(x)+negative_colors].hex)
		station_status["prediction_color"] = station_status["future_pct_available"].map(lambda x: colors[int(x)].hex)
		station_status["cluster_color"] = station_status["cluster"].map(lambda x: cluster_colors[int(x)].hex)

		response_json = {"time" : datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "stations" : station_status.to_dict(orient='records')}

		return response_json


class GeoJSON(Resource):
	@cache.cached(timeout=50)
	def get(self):

		status_url = "https://gbfs.citibikenyc.com/gbfs/en/station_status.json"
		status_req = requests.get(status_url)
		status_json = json.loads(status_req.text)
		station_status_array = status_json["data"]["stations"]

		# put into data frame
		station_status = pd.DataFrame.from_records(station_status_array)
		station_status.drop(["eightd_has_available_keys", "last_reported", "is_installed", "is_renting", "is_returning"], axis=1, inplace=True)

		# merge stations and scores
		station_status = station_status.merge(stations, on = 'station_id')
		station_status.drop(['num_bikes_disabled', 'num_docks_available', 'num_docks_disabled', "name", "short_name"], axis=1, inplace=True)
		# merge predictions
		today = datetime.datetime.today()
		thisHour = today.hour
		thisWeekday = today.weekday() < 6

	
		# merge predictions
		today = datetime.datetime.today()
		thisHour = today.hour
		thisWeekday = today.weekday() < 6

		relevant_preds = ar_preds[(ar_preds["weekday"] == thisWeekday) & (ar_preds["hour"] == thisHour)].copy()
		#print(relevant_preds.shape)
		# merge clusers with statuions
		stations_clusters = station_status.merge(clusters, left_on="station_id", right_on="ID", how='left')
		stations_clusters = stations_clusters.merge(relevant_preds, on='cluster', how='outer')
		
		stations_clusters.fillna(0, inplace=True)
		stations_clusters.cluster.astype(int, inplace=True)

		hulls = stations_clusters.groupby('cluster').apply(get_hull)
		goodHulls = [h for h in hulls if not h.get("remove")]
		response = {"type": "FeatureCollection", "features": goodHulls}

		return response


api.add_resource(Predictions, '/predictions')
api.add_resource(Stations, '/stations')
api.add_resource(GeoJSON, '/clusters')

if __name__ == "__main__":
    #app.run(debug=True) # for dev
    app.run(host='0.0.0.0') # for prod

