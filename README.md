# MySQL-MongoDB-Benchmarking
End-to-end scripts for benchmarking tests for MySQL and MongoDB using a synthetic dataset exceeding available RAM. Uses Terraform to provision GCP instances, custom data generation script and six functionally identical queries.

## Run experiments

- Create a project in Google Cloud Platform (GCP)
- Update `main.tf` to include the correct project name
  - Amend region and zone if necessary
- Create a `.env` file containing a secure password assigned to `DB_BENCHMARK_PASSWORD`
- Update `benchmark_mongo.py` and `benchmark_mysql.py` to use your secure password (ideally this should use dotenv, but it doesn't)
- Run `_run.py`

The process should automatically create the three relevant instances in GCP (a driver node, a MySQL node, and a MongoDB node) and complete all setup. A synthetic dataset will be generated on the driver node, which is then ingested into the two target nodes automatically. A series of six queries/pipelines (with identical functionality) are then run multiple times against their relevant database systems, and a number of metrics are logged. 
