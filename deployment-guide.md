# Deployment Guide: nunis-ingest-gcf
## Table of Contents
- [Overview](#overview)
- [Prerequisites and Inputs](#prerequisites-and-inputs)
- [Prepare Cloud Console and Cloud Shell Interfaces](#prepare-cloud-console-and-cloud-shell-interfaces)
- [Provision Google Cloud Infrastructure](#provision-google-cloud-infrastructure)
- [Build Athlete Configurations](#build-athlete-configurations)
- [Provision Continuous Integration and Continuous Deployment Pipeline](#provision-continuous-integration-and-continuous-deployment-pipeline)
- [Deploy the Solution](#deploy-the-solution)
- [Run and Verify The Deployment](#run-and-verify-the-deployment)



# Overview
This deployment guide provides detailed instructions to build, configure, and deploy a fully functional instance of **nunis-ingest-gcf** into a Google Cloud Platform (GCP) project. Previous exposure to GCP and Linux are beneficial, but not required. Experience with Git is assumed.

These instructions will leverage the use of two interfaces into your GCP environment:
- The [Cloud Console](https://cloud.google.com/cloud-console): a web-based admin interface
- The [Cloud Shell](https://cloud.google.com/shell): a Linux terminal command line interface

Provisioning steps will be a combination of [Cloud SDK](https://cloud.google.com/sdk/) `gcloud` | `gsutil` | `bq` commands run in the Cloud Shell terminal and point-and-click steps in the Cloud Console.

This user guide is written in a "ride along" format, where I'll be deploying an instance of the solution for two athletes whose data I want to ingest. Most of the provisioning steps are singular, regardless of the number of athletes. Where there are athlete-specific steps, I'll be executing them for two athletes. If you're configuring this for a single athlete, just do them one time.



# Prerequisites and Inputs
The following prerequisites should be completed before continuing:
- This repo has been forked into your own Github account
  - Your Github username (e.g. github.com/**username**) is an input
- A GCP project has been provisioned, with billing enabled
  - Your **Project ID** is an input
- Your Google Account has the **Owner** IAM Role in the target project
  - If you created the project yourself, you'll have this by default
- You've determined the athlete (or athletes) whose data you'll be ingesting; each athlete will require:
  - A Strava account with running activities and an API application
    - See [Getting Started with the Strava API](https://developers.strava.com/docs/getting-started/) for directions on creating an API application
    - The **Client ID**, **Client Secret**, and **Refresh Token** (with activity:read scope) are inputs
  - A unique identifier will be reuired for each athlete; I recommend using the first letter of the first name, followed by the last name
    - Example athlete identifiers used in this guide are **kslifer** and **thebner**



# Prepare Cloud Console and Cloud Shell Interfaces
Start by [logging into your Cloud Console](https://console.cloud.google.com/) in a web browser, and navigating to your project. The project selector menu in the upper left corner of the console should match the name of the project that you intend to deploy this solution into.

Once you've navigated to the project, launch the **Cloud Shell** by clicking on the terminal icon in the upper right corner of the console. The mouseover text is "Activate Cloud Shell". Clicking this icon will launch an embedded terminal session to a Linux Virtual Machine inside of Google Cloud, directly in your web browser window. Cloud Shell comes pre-loaded with the tools and authentication to run the commands in this guide.

Once your terminal session has launched, click on the kebab menu in the upper right corner and select "Boost Cloud Shell" to relaunch your terminal session with an instance that has more CPU and Memory. (Note: This is optional... but why not?)


# Provision Google Cloud Infrastructure
This section contains the steps to provision the infrastructure within Google Cloud that will run the solution.


## Set the PROJECT_ID Linux Environment Variable
To simplify the usage of `gcloud` commands, your **Project ID** will be exported into a Linux environment variable. It will be set one time, and then the variable will be referenced going forward.

Set the PROJECT_ID environment variable by running the command below in the Cloud Shell, replacing *"nunis-analytics-d123"*  with your GCP Project ID:

    export PROJECT_ID="nunis-analytics-d123"

To verify that it's been correctly set, run the echo command below. You should see **your** Project ID as the output:

    echo ${PROJECT_ID}


## Set the Cloud SDK Project Configuration
Run the command below to set your Project ID (as stored in the PROJECT_ID environment variable) as the active configuration:

    gcloud config set project ${PROJECT_ID}

You should see your Project ID highlighted in yellow text at your command line.

`Note:`*This step may not be necessary if this is your only GCP project, but will ensure that the gcloud commands are run within the context of the target project. It's idempotent, so running it just to be safe is harmless.*


## Enable Google Cloud Service APIs
Google Cloud Services are consumed as a series of APIs, most of which are disabled by default. Run each of the commands below to enable the APIs for the services that this solution will use:

    gcloud services enable pubsub.googleapis.com
    gcloud services enable cloudfunctions.googleapis.com
    gcloud services enable cloudscheduler.googleapis.com
    gcloud services enable appengine.googleapis.com
    gcloud services enable cloudbuild.googleapis.com
    gcloud services enable cloudresourcemanager.googleapis.com

Successful execution of each of the above commands will display the output: *Operation "..." finished successfully.*


## Provision Cloud Storage Bucket
[Cloud Storage](https://cloud.google.com/storage) is an object storage service that will be used to store athlete-specific configuration files for the data ingestion process, as well as a historical archive of response data from the API calls made to acquire data.

Run the command below to provision a GCS bucket with the **standard** storage class in the **South Carolina(us-east1)** GCP region. GCS bucket names must be globally unique across all GCP accounts, so we'll use the Project ID (which also must be globally unique) as the name.

    gsutil mb -b on -c standard -l us-east1 gs://${PROJECT_ID}


## Provision BigQuery Dataset
[BigQuery](https://cloud.google.com/bigquery) is a data warehouse service that will be used to store the running data that will eventually be analyzed and visualized. A dataset is simply a logical container for a series of tables. Run the command below to provision a dataset within your project:

    bq --location=US mk --dataset ${PROJECT_ID}:nunis_analytics_dwh

`Note:`*The bq command is a separate component of the Cloud SDK, and may require an extra authentication step to use.*


## Provision Pub/Sub Topics
[Cloud Pub/Sub](https://cloud.google.com/pubsub) is a message delivery service that is used in the scheduling of data ingestion. This solution requires a topic for each athlete whose data will be ingested. Create your topic name using the format **ingest_platform_athlete**, replacing *athlete* with your athlete's unique identifier (e.g. **kslifer**). Then form `gcloud` commands to create your topic.

Example `gcloud` commands for my instance are below:

    gcloud pubsub topics create ingest_strava_kslifer
    gcloud pubsub topics create ingest_strava_thebner
    gcloud pubsub topics create ingest_strava_afeher


## Provision App Engine Application
[App Engine](https://cloud.google.com/appengine) is a serverless platform for running containerized applications. This solution doesn't use App Engine, but it's required by the Cloud Scheduler cron service that is used in the scheduling of data ingestion.

Run the command below to create an app in the **South Carolina(us-east1)** GCP region:

    gcloud app create --region=us-east1


## Provision Cloud Scheduler Jobs
[Cloud Scheduler](https://cloud.google.com/scheduler) is a managed cron job service that will be used to trigger automated ingestion of athlete data. This is accomplished by sending a message to the Pub/Sub topic that we just created, which in turn will trigger the execution of a Cloud Function that performs the data ingestion.

The Cloud Function accepts two triggers:
- Load new data since the last time the API was called
  - This trigger will append data into BigQuery, based on the timestamp that the API was last called (which is internally managed by the solution)
- Reload all available data
  - This trigger will truncate existing data in BigQuery and append all available data from the API

`Note:`*The second trigger is a restore mechanism that will refresh athlete data in the event of data corruption. It's not intended for regular use.*

### Provision Cloud Scheduler Job to Load Incremental Data
Form `gcloud` statements, updating **ingest-strava-new-kslifer** and **--topic=ingest_strava_kslifer** with your athlete's identifier. The schedule can be customized per the [documentation](https://cloud.google.com/scheduler/docs/configuring/cron-job-schedules).

My instance triggers ingestion every 15 minutes:

    gcloud scheduler jobs create pubsub ingest-strava-new-kslifer --schedule="*/15 * * * *" --topic=ingest_strava_kslifer --message-body="load_new" --description="Incrementally load new data" --time-zone="America/New_York"
    gcloud scheduler jobs create pubsub ingest-strava-new-thebner --schedule="*/15 * * * *" --topic=ingest_strava_thebner --message-body="load_new" --description="Incrementally load new data" --time-zone="America/New_York"
    gcloud scheduler jobs create pubsub ingest-strava-new-afeher --schedule="*/15 * * * *" --topic=ingest_strava_afeher --message-body="load_new" --description="Incrementally load new data" --time-zone="America/New_York"

### Provision Cloud Scheduler Job to Reload All Data
Form `gcloud` statements, updating **ingest-strava-new-kslifer** and **--topic=ingest_strava_kslifer** with your athlete's identifier. The schedule is arbitrarily set to run on the first day of the first month of each year; this should be left as-is. These jobs will be created, then paused.

My instance jobs are below:

    gcloud scheduler jobs create pubsub ingest-strava-all-kslifer --schedule="0 0 1 1 0" --topic=ingest_strava_kslifer --message-body="load_all" --description="Drop and reload all data" --time-zone="America/New_York"
    gcloud scheduler jobs pause ingest-strava-all-kslifer
    gcloud scheduler jobs create pubsub ingest-strava-all-thebner --schedule="0 0 1 1 0" --topic=ingest_strava_thebner --message-body="load_all" --description="Drop and reload all data" --time-zone="America/New_York"
    gcloud scheduler jobs pause ingest-strava-all-thebner
    gcloud scheduler jobs create pubsub ingest-strava-all-afeher --schedule="0 0 1 1 0" --topic=ingest_strava_afeher --message-body="load_all" --description="Drop and reload all data" --time-zone="America/New_York"
    gcloud scheduler jobs pause ingest-strava-all-afeher



# Build Athlete Configurations
Athlete-specific configuration for the data ingestion process is managed through two components:
- A **Python ConfigParser INI** file, which contains the configuration for the data ingestion code
- A **GCP Cloud Build YAML** manifest, which contains the configuration for the CI/CD pipeline

This section contains the steps to build athlete-specific configurations.


## Python ConfigParser INI
This repo contains a sample configuration file (nunis-ingest-gcf-strava-athlete.ini). Its contents are below:

    [strava_client]
    strava_client_id = athlete-client-id
    strava_client_secret = athlete-client-secret
    strava_refresh_token = athlete-refresh-token
    strava_current_epoch = 0
    
    [gcp_dwh]
    gcp_project_id = target-gcp-project-id
    gcp_bq_dataset = nunis_analytics_dwh
    gcp_bq_table = activities_strava_athlete

 There are two sections of configuration: Strava API Client, and GCP Data Warehouse.

Make a copy of this file and use it as a starting point. Build your athlete configuration as follows:
- File Name:
  - Replace "athlete" with your athlete's unique identifier (e.g. nunis-ingest-gcf-strava-**kslifer**.ini)
- Strava API Client:
  - **strava_client_id**: Provide the **Client ID** for the athlete's Strava API application
  - **strava_client_secret**: Provide the **Client Secret** for the athlete's Strava API application
  - **strava_refresh_token**: Provide the **Refresh Token** for the athlete's Strava API application
  - **strava_current_epoch**: Don't change this value; this will force the first data ingestion to process the athlete's entire activity history
- GCP Data Warehouse:
  - **gcp_project_id**: Provide the **Project ID** of your GCP project
  - **gcp_bq_dataset**: Do not change this value; it should match the BigQuery dataset that you provisioned
  - **gcp_bq_table**: Replace "athlete" with your athlete's unique identifier (e.g. activities_strava_**kslifer**)

`Note:`*Your resulting configuration file will contain sensitive Strava API Application credentials that can be used to acquire data from the Strava API. This shouldn't be stored in a public Github repo, and the .gitignore will prevent this from happening.*

Finally, upload your configuration file into the root of the Cloud Storage bucket that you provisioned. [This documentation article](https://cloud.google.com/storage/docs/uploading-objects) outlines several ways to do this; the simplest approach is to use the Cloud Console.


## GCP Cloud Build YAML
This repo contains a sample manifest for a Cloud Build pipeline (cicd-strava-athlete.yaml). Its contents are below:

    steps:
    - name: 'docker.io/library/python:3.7'
      entrypoint: /bin/sh
      args: [-c, 'pip install -r requirements.txt', '&&', 'pytest']
    - name: 'gcr.io/google.com/cloudsdktool/cloud-sdk:slim'
      entrypoint: 'gcloud'
      args: ['functions', 'deploy', 'ingest-strava-athlete', '--region=us-east1', '--timeout=540s', '--memory=2048MB', '--trigger-topic=ingest_strava_athlete', '--runtime=python38', '--entry-point=run', '--set-env-vars=GCS_BUCKET=nunis-analytics-d123,CONFIG_FILE=nunis-ingest-gcf-strava-athlete.ini']

`Note:`*[This documentation article](https://cloud.google.com/cloud-build/docs/build-config) is a good starting point to understand the syntax of a Cloud Build manifest.*

Make a copy of this file and use it as a starting point. Build your manifest as follows:
- In the args for the second build step (the last line of the file):
  - The syntax **'functions', 'deploy', 'ingest-strava-athlete'** names the function; replace "athlete" with your athlete's unique identifier (e.g. **ingest-strava-kslifer**)
  - The syntax **'--trigger-topic=ingest_strava_athlete'** maps the function to the Pub/Sub topic that you previously provisioned; replace "athlete" with your athlete's unique identifier (e.g. **ingest_strava_kslifer**)
  - The snippet **GCS_BUCKET=nunis-analytics-d123** configures a function environment variable with the name of the GCS bucket; update this with your **Project ID** (which is also the name of the Cloud Storage bucket that you previously provisioned)
  - The syntax **CONFIG_FILE=nunis-ingest-gcf-strava-athlete.ini** configures a function environment variable with the name of the configuration file; update this with the name of the configuration file that you previously created

`Note:`*Unlike the Python ConfigParser INI, this file will live in your repo, and doesn't contain sensitive information.*



# Provision Continuous Integration and Continuous Deployment Pipeline
Google Cloud's [Cloud Build](https://cloud.google.com/cloud-build) will be used as the Continuous Integration and Continuous Deployment (CI/CD) platform for this solution.

This section contains the steps to provision a CI/CD pipeline from your Github repo into your Google Cloud environment. This will provide an automated workflow to build and deploy the solution when code changes are made to your Github repo.


## Install the Cloud Build Github App
In order to configure Cloud Build triggers on your Github repo, you need to first install and authorize the Cloud Build Github App.

Starting in the Cloud Console, follow the steps outlined in [this documentation article](https://cloud.google.com/cloud-build/docs/automating-builds/create-github-app-triggers).
- At step 8 and step 10, select your nunis-ingest-gcf repo
- At step 11, skip the option to create push triggers


## Assign IAM Permissions to the Cloud Build Service Account
The service account used by Cloud Build requires additional permissions to deploy a Cloud Function. This section contains the steps to assign those permissions.

### Obtain and Set the PROJECT_NUM Linux Environment Variable
The Cloud Build service account is named with the GCP **Project Number**. This is is a unique identifier assigned by Google that is different than the **Project ID**.

To simplify the usage of `gcloud` commands, your **Project Number** will be exported into a Linux environment variable. It will be set one time, and then the variable will be referenced going forward.

Set the PROJECT_NUM environment variable by running the command below in the Cloud Shell:

    export PROJECT_NUM=$(gcloud projects list --filter="$PROJECT_ID" --format="value(PROJECT_NUMBER)")

To verify that it's been correctly set, run the echo command below. You should see a sequence of numbers as the output (e.g. '1060504504364')

    echo ${PROJECT_NUM}

### Assign the "Cloud Functions Developer" IAM Role to the Service Account
To assign this role, run the command below in the Cloud Shell:

    gcloud projects add-iam-policy-binding $PROJECT_ID --member="serviceAccount:${PROJECT_NUM}@cloudbuild.gserviceaccount.com" --role='roles/cloudfunctions.developer'

### Assign the "Service Account User" IAM Role to the Service Account
To assign this role, run the command below in the Cloud Shell:

    gcloud projects add-iam-policy-binding $PROJECT_ID --member="serviceAccount:${PROJECT_NUM}@cloudbuild.gserviceaccount.com" --role='roles/iam.serviceAccountUser'


## Obtain and Set the GH_USERNAME Linux Environment Variable
To simplify the usage of `gcloud` commands, your **Github Username** will be exported into a Linux environment variable. It will be set one time, and then the variable will be referenced going forward.

Set the GH_USERNAME environment variable by running the command below in the Cloud Shell, replacing *kslifer* with your Github Username:

    export GH_USERNAME="kslifer"

To verify that it's been correctly set, run the echo command below. You should see **your** Github Username as the output:

    echo ${GH_USERNAME}


## Provision Cloud Build Triggers
Cloud Build Triggers will start the build and deploy workflow when code changes are pushed to the master branch of your Github repo. Form a `gcloud` command for each athlete, using my examples below as a template.

The syntax **--build-config="cicd-strava-kslifer.yaml"** identifies the Cloud Build YAML manifest; this should be replaced with the name of the manifest that you previously created.

    gcloud beta builds triggers create github --repo-owner=${GH_USERNAME} --repo-name="nunis-ingest-gcf" --branch-pattern="^master$" --included-files="**" --build-config="cicd-strava-kslifer.yaml"
    gcloud beta builds triggers create github --repo-owner=${GH_USERNAME} --repo-name="nunis-ingest-gcf" --branch-pattern="^master$" --included-files="**" --build-config="cicd-strava-thebner.yaml"
    gcloud beta builds triggers create github --repo-owner=${GH_USERNAME} --repo-name="nunis-ingest-gcf" --branch-pattern="^master$" --included-files="**" --build-config="cicd-strava-afeher.yaml"



# Deploy The Solution
Now that all components of the solution framework are in place, the last step is to perform an initial build and deploy of the Cloud Function.

Since there are now code changes waiting to be committed to your Github repo, pushing them back to your master branch will trigger the CI/CD pipeline, which will automatically build and deploy the function for you!



# Run and Verify The Deployment
Successful deployment of the solution will result in athlete data being infested into a table in BigQuery. But since this solution is distributed across many Google Cloud services, we don't want to wait for the first scheduled run to know if there's an issue. Instead, we'll force a run and inspect each step along the way.


## Cloud Build CI/CD
Cloud Build needs to run in order for the Cloud Function to be deployed and available to execute. The push to your master branch should have triggered this execution. This can be verified by selecting the menu in the upper left corner of the Cloud Console and navigating to Cloud Build -> History.

The build history is displayed in reverse chronological order, with the most recent build at the top. Since this is a new deployment, a successful build will be indicated by a green check. A failure will be indicated by a red exclamation point. If Cloud Build didn't run, there won't be an entry in the history.

`Note:`*The first deployment will occasionally fail due to a 403 Error with the Details "...gcf-admin-robot.iam.gserviceaccount.com does not have storage.objects.create access to the Google Cloud Storage object." This appears to be a timing error while the underlying IAM permissions are still propagating through the platform. Forcing the build process to run a second time by navigating to Cloud Build -> Triggers and clicking the "Run Trigger" button typically results in a successful build on the second attempt.*


## Data Ingestion Process
Since the automated ingestion processes have been scheduled to run in the middle of the night, we'll force the first run.

### Cloud Scheduler Jobs
Select the menu in the upper left corner of the Cloud Console and navigate to Cloud Scheduler. Click the "Run Now" button for each of the enabled jobs that you configured (there will be one per athlete) to trigger the data ingestion process.

### Cloud Function
Select the menu in the upper left corner of the Cloud Console and navigate to Cloud Functions. You should see a function deployed for each athlete. A successfully deployed function will be indicated by a green check; a failure will be indicated by a red exclamation point.

### Cloud Logging
The Cloud Function is instrumented with debug logging, which is ingested directly into [Cloud Logging](https://cloud.google.com/logging). Logs can be viewed by selecting the menu in the upper left corner of the Cloud Console and navigating to Logging -> Logs Viewer. Logs are continuously loaded in chronological order; to refresh the very latest at any time, click the "Jump to Now" button.

This solution's debug log lines will start with **"nunis-ingest-function: "**. You should see a log line that starts with the function name (**ingest-strava-athlete**) that says *"Function execution took...ms, finished with status: 'ok'"*.

A similar log line that ends with *"finished with status: 'crash'"* indicates an error that needs to be triaged.

### BigQuery Table
Select the menu in the upper left corner of the Cloud Console and navigate to BigQuery. On the left side of the UI, your project name will be pinned.

Expanding it will show the **"nunis_analytics_dwh"** dataset. If the data ingestion process ran successfully, you will be able to expand the dataset and see an **"activities_strava_athlete"** table for each athlete that you configured.

To view the athlete data, click on the table, then click the "Preview" tab. This will load the first 100 rows of data. Text at the bottom right of the UI will indicate that you're viewing **"1 - 100 of N"**. The final number "N" is the number of activities that have been ingested for the athlete.

### Cloud Storage JSON Archive
Select the menu in the upper left corner of the Cloud Console and navigate to Cloud Storage -> Browser. Navigate into your bucket by clicking on the name. Ignore the buckets with "appspot.com" in the name; these are automatically created by Google for the App Engine service.

If the data ingestion process ran successfully, a folder named **"nunis-ingest-function"** should exist. That folder should contain a .json file dump of the web service response that was loaded into BigQuery.
