terraform {
  required_version = ">= 1.9"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.0"
    }
  }
}

provider "google" {
  project               = var.project_id
  region                = var.region
  user_project_override = true
  billing_project       = var.project_id  # routes X-Goog-User-Project header for billingbudgets API
}

# ---------------------------------------------------------------------------
# APIs — only what the kill switch itself requires
# ---------------------------------------------------------------------------

resource "google_project_service" "pubsub" {
  service            = "pubsub.googleapis.com"
  disable_on_destroy = false
}

resource "google_project_service" "cloudfunctions" {
  service            = "cloudfunctions.googleapis.com"
  disable_on_destroy = false
}

resource "google_project_service" "cloudbuild" {
  service            = "cloudbuild.googleapis.com"
  disable_on_destroy = false
}

resource "google_project_service" "cloudbilling" {
  service            = "cloudbilling.googleapis.com"
  disable_on_destroy = false
}

resource "google_project_service" "billingbudgets" {
  service            = "billingbudgets.googleapis.com"
  disable_on_destroy = false
}

# Artifact Registry is required by Cloud Functions Gen 1 to store build artifacts.
resource "google_project_service" "artifactregistry" {
  service            = "artifactregistry.googleapis.com"
  disable_on_destroy = false
}

# The GCF service agent needs Artifact Registry access for its first deployment.
# Format: service-{PROJECT_NUMBER}@gcf-admin-robot.iam.gserviceaccount.com
resource "google_project_iam_member" "gcf_artifactregistry_admin" {
  project = var.project_id
  role    = "roles/artifactregistry.admin"
  member  = "serviceAccount:service-${var.project_number}@gcf-admin-robot.iam.gserviceaccount.com"

  depends_on = [
    google_project_service.artifactregistry,
    google_project_service.cloudfunctions,
  ]
}

# ---------------------------------------------------------------------------
# Pub/Sub topic — receives budget breach notifications
# ---------------------------------------------------------------------------

resource "google_pubsub_topic" "billing_alerts" {
  name = "billing-alerts"

  depends_on = [google_project_service.pubsub]
}

# ---------------------------------------------------------------------------
# Service account for the kill-switch function
# ---------------------------------------------------------------------------

resource "google_service_account" "killswitch" {
  account_id   = "killswitch-sa"
  display_name = "Kill Switch — Budget Breach Handler"
  description  = "Disables billing when monthly spend exceeds the hard cap."
}

# Billing admin on the billing account — lets the SA call updateBillingInfo.
# billing.resourceAssociations.delete is required to unlink a billing account.
resource "google_billing_account_iam_member" "killswitch_billing_admin" {
  billing_account_id = var.billing_account_id
  role               = "roles/billing.admin"
  member             = "serviceAccount:${google_service_account.killswitch.email}"
}

# ---------------------------------------------------------------------------
# Cloud Storage bucket — stores the zipped function source
# ---------------------------------------------------------------------------

resource "google_storage_bucket" "function_source" {
  name                        = "${var.project_id}-killswitch-src"
  location                    = "US"
  uniform_bucket_level_access = true

  lifecycle_rule {
    action { type = "Delete" }
    condition { age = 30 }  # GC old zips after 30 days — stays within 5 GB free tier
  }
}

# ---------------------------------------------------------------------------
# Function source archive
# ---------------------------------------------------------------------------

data "archive_file" "function_zip" {
  type        = "zip"
  source_dir  = "${path.module}/function"
  output_path = "${path.module}/.build/killswitch.zip"
}

resource "google_storage_bucket_object" "function_zip" {
  name   = "killswitch-${data.archive_file.function_zip.output_md5}.zip"
  bucket = google_storage_bucket.function_source.name
  source = data.archive_file.function_zip.output_path

  depends_on = [google_storage_bucket.function_source]
}

# ---------------------------------------------------------------------------
# Cloud Function (Gen 1) — Pub/Sub background trigger
# ---------------------------------------------------------------------------

resource "google_cloudfunctions_function" "killswitch" {
  name        = "billing-killswitch"
  description = "Disables billing on budget breach. DRY_RUN=true during initial test."
  runtime     = "python311"
  region      = var.region

  available_memory_mb          = 128
  timeout                      = 60
  source_archive_bucket        = google_storage_bucket.function_source.name
  source_archive_object        = google_storage_bucket_object.function_zip.name
  entry_point                  = "kill_billing"
  service_account_email        = google_service_account.killswitch.email

  event_trigger {
    event_type = "google.pubsub.topic.publish"
    resource   = google_pubsub_topic.billing_alerts.name

    failure_policy {
      retry = false  # Do not retry on failure — billing disable is idempotent but retries add noise
    }
  }

  environment_variables = {
    GCP_PROJECT_ID = var.project_id
    DRY_RUN        = var.dry_run
  }

  depends_on = [
    google_project_service.cloudfunctions,
    google_project_service.cloudbuild,
    google_project_service.artifactregistry,
    google_project_iam_member.gcf_artifactregistry_admin,
    google_storage_bucket_object.function_zip,
  ]
}

# ---------------------------------------------------------------------------
# Billing budget — $10/mo cap, alerts at 5 / 10 / 50 / 100 %
# ---------------------------------------------------------------------------

resource "google_billing_budget" "monthly_cap" {
  billing_account = var.billing_account_id
  display_name    = "review-iq-prod-monthly-cap"

  budget_filter {
    projects               = ["projects/${var.project_number}"]
    credit_types_treatment = "INCLUDE_ALL_CREDITS"  # billing account currency is INR
  }

  amount {
    specified_amount {
      currency_code = "INR"
      units         = tostring(var.budget_amount_inr)
    }
  }

  # Alert thresholds — email to billing account admins at each level
  threshold_rules {
    threshold_percent = 0.05
    spend_basis       = "CURRENT_SPEND"
  }
  threshold_rules {
    threshold_percent = 0.10
    spend_basis       = "CURRENT_SPEND"
  }
  threshold_rules {
    threshold_percent = 0.50
    spend_basis       = "CURRENT_SPEND"
  }
  # 100% — also triggers the kill-switch via Pub/Sub
  threshold_rules {
    threshold_percent = 1.0
    spend_basis       = "CURRENT_SPEND"
  }

  all_updates_rule {
    pubsub_topic   = google_pubsub_topic.billing_alerts.id
    schema_version = "1.0"

    # Disable alert after kill switch fires to avoid repeated triggers
    disable_default_iam_recipients = false
  }

  depends_on = [
    google_project_service.billingbudgets,
    google_pubsub_topic.billing_alerts,
  ]
}
