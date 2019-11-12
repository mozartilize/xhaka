import json
import orjson
import requests
import flask
import werkzeug

orjson.JSONEncoder = json.JSONEncoder

requests.models.complexjson = orjson

werkzeug.wrappers.json._json = orjson

flask.json = orjson
