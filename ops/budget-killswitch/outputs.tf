output "pubsub_topic_id" {
  description = "Full resource ID of the billing-alerts Pub/Sub topic"
  value       = google_pubsub_topic.billing_alerts.id
}

output "pubsub_topic_name" {
  description = "Short name of the Pub/Sub topic (for gcloud publish commands)"
  value       = google_pubsub_topic.billing_alerts.name
}

output "function_name" {
  description = "Cloud Function name"
  value       = google_cloudfunctions_function.killswitch.name
}

output "function_region" {
  description = "Cloud Function region"
  value       = google_cloudfunctions_function.killswitch.region
}

output "killswitch_sa_email" {
  description = "Kill-switch service account email (needs billing.admin on billing account)"
  value       = google_service_account.killswitch.email
}

output "dry_run_active" {
  description = "Whether DRY_RUN is currently enabled on the deployed function"
  value       = var.dry_run
}
