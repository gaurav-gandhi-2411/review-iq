variable "project_id" {
  description = "GCP project ID"
  type        = string
  default     = "review-iq-prod"
}

variable "project_number" {
  description = "GCP project number (numeric)"
  type        = string
  default     = "432538168127"
}

variable "region" {
  description = "GCP region for Cloud Function"
  type        = string
  default     = "us-central1"
}

variable "billing_account_id" {
  description = "GCP billing account ID"
  type        = string
  default     = "014DAE-6B3556-077365"
}

variable "alert_email" {
  description = "Email address for budget alert notifications"
  type        = string
  default     = "gaurav.gandhi2411@gmail.com"
}

variable "budget_amount_inr" {
  description = "Monthly budget cap in INR (billing account currency). ₹100 ≈ $1.20 USD. Kept tight because GCP billing latency is ~24h — a higher cap could allow that amount to accumulate before the kill switch fires."
  type        = number
  default     = 100
}

variable "dry_run" {
  description = "If 'true', function logs intent but does NOT disable billing. 'false' = production mode."
  type        = string
  default     = "false"
}
