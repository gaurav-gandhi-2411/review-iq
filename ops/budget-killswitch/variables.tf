variable "project_id" {
  description = "GCP project ID"
  type        = string
  default     = "reviewiq-prod-260813"
}

variable "project_number" {
  description = "GCP project number (numeric)"
  type        = string
  default     = "433287338182"
}

variable "region" {
  description = "GCP region for Cloud Function"
  type        = string
  default     = "us-central1"
}

variable "billing_account_id" {
  description = "GCP billing account ID"
  type        = string
  default     = "01285B-91E4CB-70AD7E"
}

variable "alert_email" {
  description = "Email address for budget alert notifications"
  type        = string
  default     = "gaurav.gandhi1129@gmail.com"
}

variable "budget_amount_inr" {
  description = "Monthly budget cap in INR (billing account currency). ₹2500 ≈ $30 USD. Raised from an original ₹100 (~$1.20) cap, which was sized to catch a runaway loop but in practice was tight enough to fire on ordinary traffic growth -- indistinguishable from the exact outage class this account spent 2026-08-12 recovering from, just automated. ₹2500 is meant to represent genuine runaway spend (a misconfigured job or infinite loop), not normal operation. GCP billing latency is still ~24h, so a real runaway could still accumulate close to this amount before the kill switch fires -- that tradeoff is accepted deliberately in exchange for not false-triggering on legitimate usage."
  type        = number
  default     = 2500
}

variable "dry_run" {
  description = "If 'true', function logs intent but does NOT disable billing. 'false' = production mode."
  type        = string
  default     = "false"
}
