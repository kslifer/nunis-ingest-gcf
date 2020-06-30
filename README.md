# Nunis Running Analytics: nunis-ingest-gcf
Strava athlete activity ingestion into BigQuery, implemented as a Google Cloud Function.

## Overview
This repository contains the code for the ingest module of the Nunis Running Analytics project, a running analytics platform built on Google Cloud. This module provides an automated, repeated ingestion of athlete activity data from the [Strava API](https://developers.strava.com/) into BigQuery for downstream transformation and analysis.

The ingestion processor is a Python function, with a supporting Cloud Build CI/CD pipeline to automate the build and deploy processes.

Integration to the Strava API is implemented as a poll for activity data, which is then batch processed.

Google Cloud Services used in this module are:
- Cloud Scheduler: to schedule the ingestion process
- Cloud Pub/Sub: as a message delivery system between the scheduler and function
- Cloud Function: to transact with the Strava API and BigQuery
- Cloud Storage: to store configuration for the function, and to archive API response data
- BigQuery: to warehouse the athlete activity data
- Cloud Build: for automated build and deployment of the function
- Cloud Logging: for log analysis across the stack of GCP services, as needed
- Cloud Trace: for analysis of function execution, as needed

When staged for a single athlete's data, this will run well within the quotas of the always free tier of Google Cloud!


## Installation
A beginner-friendly, step-by-step Deployment Guide can be found [here](deployment-guide.md). This guide will walk through the steps to provision, build, and run this module in a Google Cloud Platform project using the Cloud Console and Cloud Shell. It assumes little previous experience with these technologies.


## Features
- Automated data ingestion (nightly is the default, but the schedule is a customizable cronjob).
- Integration to the Strava API for data acquisition.
- Two ingestion modes:
  - "New": load only new activities that have occurred since the previous call to the API (appends to any existing data in BigQuery).
  - "All": load all available activities (truncates and replaces all existing data in BigQuery).
  - The suggested ingestion mode is "New", as it's more performant and less expensive (though there are [known issues](#known-issues)).
- Multitenancy; the module can be configured to ingest data for multiple athletes.


## Known Issues
- The "New" ingestion mode could be subject to several idiosyncrasies (though none are guaranteed to occur):
  - The Strava API is polled for new activities that have been recorded and synced since the previous poll. Athletes that don't sync frequently (e.g. track a run with a watch, then sync it a few days later) may result in the ingestion process missing those activities, because they've chronologically occured before the current window of time that's being polled. The closer the sync to near real time (i.e. by using the Strava mobile app), the lower the probability of this occurring.
  - BigQuery doesn't natively care about duplicate data, so any user or configuration error that results in multiple inserts of the same data will lead to duplication. This isn't inherently problematic from an ingestion standpoint, but requires that downstream transformation processes first attempt to deduplicate the data in BigQuery.


## Future Considerations
- Automated provisioning of the GCP services with Terraform through a separate Cloud Build pipeline from a separate repo.
- Integration to the Strava API as a webhook subscription for near real time data availability (which will also eliminate the known issues).
- Support for other run tracking platforms, should the need arise.


## License
GPL 3.0 - See [LICENSE](LICENSE) for more information.
