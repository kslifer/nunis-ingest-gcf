import sys
import json
import gzip
import requests
import os
import configparser
import time
import datetime
from io import BytesIO
from io import StringIO
import base64
import json
from google.cloud import bigquery
from google.cloud import storage
from google.cloud import logging

FUNCTION_NAME = 'nunis-ingest-function'

KEY_EV_GCS_BUCKET = 'GCS_BUCKET'
KEY_EV_CONFIG_FILE = 'CONFIG_FILE'

ACTION_LOAD_NEW = 'load_new'
ACTION_LOAD_ALL = 'load_all'
ACTION = ACTION_LOAD_NEW
CONFIGURATION = configparser.ConfigParser()
STRAVA_ACCESS_TOKEN = ''
CALLED_EPOCH = ''
WRITE_EPOCH = False

logging_client = logging.Client()
logger = logging_client.logger(FUNCTION_NAME)


def read_config_from_bucket():
    global CONFIGURATION
    GCS_BUCKET = os.environ.get(KEY_EV_GCS_BUCKET)
    logger.log_text(f'{FUNCTION_NAME}: Environment variable GCS_BUCKET: {GCS_BUCKET}')
    CONFIG_FILE = os.environ.get(KEY_EV_CONFIG_FILE)
    logger.log_text(f'{FUNCTION_NAME}: Environment variable CONFIG_FILE: {CONFIG_FILE}')
    if GCS_BUCKET is None or CONFIG_FILE is None:
        logger.log_text(f'{FUNCTION_NAME}: Expected environment variables are missing; throwing RuntimeError')
        raise RuntimeError('Expected environment variables are missing')
    try:
        client = storage.Client()
        bucket = client.get_bucket(GCS_BUCKET)
        blob = storage.Blob(CONFIG_FILE, bucket)
        localconfig = BytesIO()
        client.download_blob_to_file(blob, localconfig)
    except:
        logger.log_text(f'{FUNCTION_NAME}: Error while transacting with GCS: {sys.exc_info()}')
        raise RuntimeError(f'Error while transacting with GCS: {sys.exc_info()}')
    localconfig.seek(0)
    logger.log_text(f'{FUNCTION_NAME}: Read {CONFIG_FILE} from bucket: {localconfig.read().decode("utf-8")}')
    localconfig.seek(0)
    CONFIGURATION.read_string(localconfig.read().decode('utf-8'))
    localconfig.close()


def load_strava_access_token():
    global CONFIGURATION
    global STRAVA_ACCESS_TOKEN
    logger.log_text(f"{FUNCTION_NAME}: Fetching Strava access token data with refresh token: {CONFIGURATION['strava_client']['strava_refresh_token']}")
    try:
        resp = requests.post(
                'https://www.strava.com/api/v3/oauth/token',
                params={f'client_id': {CONFIGURATION['strava_client']['strava_client_id']},
                        'client_secret': {CONFIGURATION['strava_client']['strava_client_secret']},
                        'grant_type': 'refresh_token',
                        'refresh_token': {CONFIGURATION['strava_client']['strava_refresh_token']}},
                timeout=30
            )
    except:
        logger.log_text(f'{FUNCTION_NAME}: Error while transacting with Strava API: {sys.exc_info()}')
        raise RuntimeError(f'Error while transacting with Strava API: {sys.exc_info()}')
    response = resp.json()
    logger.log_text(f"{FUNCTION_NAME}: Updating configuration with refresh token from API: {response['refresh_token']}")
    CONFIGURATION.set('strava_client', 'strava_refresh_token', f"{response['refresh_token']}")
    logger.log_text(f"{FUNCTION_NAME}: Fetched Strava access token: {response['access_token']}")
    STRAVA_ACCESS_TOKEN = response['access_token']


def fetch_strava_activities():
    global CALLED_EPOCH
    global WRITE_EPOCH
    global STRAVA_ACCESS_TOKEN
    page, activities = 1, []
    after_epoch = 0

    current_epoch = CONFIGURATION['strava_client']['strava_current_epoch']
    logger.log_text(f'{FUNCTION_NAME}: current_epoch in configuration: {current_epoch}')
    if ACTION == ACTION_LOAD_ALL:
        logger.log_text(f'{FUNCTION_NAME}: Requested action is: {ACTION}; resetting epoch')
        after_epoch = 0
    elif not current_epoch and not current_epoch.strip():
        logger.log_text(f'{FUNCTION_NAME}: current_epoch is empty; fetching all activities')
    else:
        logger.log_text(f"{FUNCTION_NAME}: current_epoch isnt empty; fetching new activities after: {datetime.datetime.fromtimestamp(int(float(current_epoch))).strftime('%Y-%m-%d %H:%M:%S')}")
        after_epoch = int(float(current_epoch))
    logger.log_text(f'{FUNCTION_NAME}: Fetching Strava activities with access token: {STRAVA_ACCESS_TOKEN} and after epoch: {int(after_epoch)}')
    while True:
        logger.log_text(f'{FUNCTION_NAME}: Fetching page #{page} ...')
        try:
            resp = requests.get(
                'https://www.strava.com/api/v3/athlete/activities',
                headers={'Authorization': f'Bearer {STRAVA_ACCESS_TOKEN}'},
                params={f'page': page, 'per_page': 200,
                        'after': {int(after_epoch)}},
                timeout=30
            )
        except:
            logger.log_text(f'{FUNCTION_NAME}: Error while transacting with Strava API: {sys.exc_info()}')
            raise RuntimeError(f'{FUNCTION_NAME}: Error while transacting with Strava API: {sys.exc_info()}')
        data = resp.json()
        activities += data
        if len(activities) > 0:
            logger.log_text(f'{FUNCTION_NAME}: activities returned; setting WRITE_EPOCH to True')
            WRITE_EPOCH = True
        if len(data) < 200:
            CALLED_EPOCH = time.time()
            break
        page += 1
    logger.log_text(f'{FUNCTION_NAME}: API returned {len(activities)} activities')
    return activities


def import_activites_to_bq(activity_json):
    logger.log_text(f'{FUNCTION_NAME}: Writing {len(activity_json)} activities to BigQuery')
    bq_client = bigquery.Client()
    job_config = bigquery.job.LoadJobConfig()

    job_config.source_format = bigquery.job.SourceFormat.NEWLINE_DELIMITED_JSON

    if ACTION == ACTION_LOAD_ALL:
        logger.log_text(f'{FUNCTION_NAME}: Requested action is: {ACTION}; truncating table')
        job_config.write_disposition = bigquery.job.WriteDisposition.WRITE_TRUNCATE
    else:
        job_config.write_disposition = bigquery.job.WriteDisposition.WRITE_APPEND

    job_config.create_disposition = bigquery.job.CreateDisposition.CREATE_IF_NEEDED
    job_config.autodetect = True

    logger.log_text(f"{FUNCTION_NAME}: Initiating JSON import to {CONFIGURATION['gcp_dwh']['gcp_project_id']}.{CONFIGURATION['gcp_dwh']['gcp_bq_dataset']}.{CONFIGURATION['gcp_dwh']['gcp_bq_table']}")

    job = bq_client.load_table_from_json(
        json_rows=activity_json,
        destination=f"{CONFIGURATION['gcp_dwh']['gcp_project_id']}.{CONFIGURATION['gcp_dwh']['gcp_bq_dataset']}.{CONFIGURATION['gcp_dwh']['gcp_bq_table']}",
        job_config=job_config
    )

    logger.log_text(f'{FUNCTION_NAME}: Launched BigQuery job ID: {job.job_id}')
    return job.job_id


def write_response_to_gcs(activity_json):
    logger.log_text(f'{FUNCTION_NAME}: Archiving response data for {len(activity_json)} activities to Cloud Storage')
    FILE_NAME = f"{FUNCTION_NAME}/{CONFIGURATION['gcp_dwh']['gcp_bq_table']}-{datetime.datetime.now().strftime('%Y-%m-%d-%H-%M-%S')}.json"
    logger.log_text(f"{FUNCTION_NAME}: Writing API response to bucket: {FILE_NAME}")
    GCS_BUCKET = os.environ.get(KEY_EV_GCS_BUCKET)
    try:
        client = storage.Client()
        bucket = client.get_bucket(GCS_BUCKET)
        blob = storage.Blob(FILE_NAME, bucket)
        blob.upload_from_string(json.dumps(activity_json))
    except:
        logger.log_text(f'{FUNCTION_NAME}: Error while transacting with GCS: {sys.exc_info()}')
        raise RuntimeError(f'Error while transacting with GCS: {sys.exc_info()}')


def write_config_to_bucket():
    if WRITE_EPOCH is True:
        logger.log_text(f"{FUNCTION_NAME}: Updating configuration with epoch value of the current time: {datetime.datetime.fromtimestamp(int(CALLED_EPOCH)).strftime('%Y-%m-%d %H:%M:%S')}")
        CONFIGURATION.set('strava_client', 'strava_current_epoch', f'{CALLED_EPOCH}')
    else:
        logger.log_text(f'{FUNCTION_NAME}: No activities returned; so not updating the epoch')
    
    GCS_BUCKET = os.environ.get(KEY_EV_GCS_BUCKET)
    CONFIG_FILE = os.environ.get(KEY_EV_CONFIG_FILE)
    localconfig = StringIO()
    CONFIGURATION.write(localconfig)
    localconfig.seek(0)
    logger.log_text(f'{FUNCTION_NAME}: Writing {CONFIG_FILE} to bucket: {localconfig.read()}')
    try:
        client = storage.Client()
        bucket = client.get_bucket(GCS_BUCKET)
        blob = storage.Blob(CONFIG_FILE, bucket)
        localconfig.seek(0)
        blob.upload_from_file(localconfig)
    except:
        logger.log_text(f'{FUNCTION_NAME}: Error while transacting with GCS: {sys.exc_info()}')
        raise RuntimeError(f'Error while transacting with GCS: {sys.exc_info()}')
    localconfig.close()


def run(event, context=None):
    global ACTION
    if 'data' in event:
        logger.log_text(f"{FUNCTION_NAME}: Received action: {base64.b64decode(event['data']).decode('utf-8')}")
        ACTION = base64.b64decode(event['data']).decode('utf-8')
    read_config_from_bucket()
    load_strava_access_token()
    ACTIVITIES = fetch_strava_activities()
    if len(ACTIVITIES) > 0:
        import_activites_to_bq(ACTIVITIES)
        write_response_to_gcs(ACTIVITIES)
    write_config_to_bucket()
