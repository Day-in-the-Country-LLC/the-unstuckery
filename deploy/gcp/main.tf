terraform {
  required_version = ">= 1.6"
  required_providers { google = { source = "hashicorp/google", version = "~> 6.0" } }
}
variable "project" { type = string }
variable "region" { default = "us-central1" }
variable "zone" { default = "us-central1-a" }
variable "name" { default = "the-unstuckery" }
variable "ssh_public_key" { type = string }
provider "google" {
  project = var.project
  region  = var.region
  zone    = var.zone
}
resource "google_compute_network" "app" {
  name                    = var.name
  auto_create_subnetworks = false
}
resource "google_compute_subnetwork" "app" {
  name          = var.name
  ip_cidr_range = "10.42.0.0/24"
  region        = var.region
  network       = google_compute_network.app.id
}
resource "google_compute_address" "app" {
  name         = var.name
  region       = var.region
  network_tier = "STANDARD"
}
resource "google_compute_firewall" "web" {
  name          = "${var.name}-web"
  network       = google_compute_network.app.id
  source_ranges = ["0.0.0.0/0"]
  target_tags   = [var.name]
  allow {
    protocol = "tcp"
    ports    = ["80", "443"]
  }
}
resource "google_compute_firewall" "admin" {
  name          = "${var.name}-iap-ssh"
  network       = google_compute_network.app.id
  source_ranges = ["35.235.240.0/20"]
  target_tags   = [var.name]
  allow {
    protocol = "tcp"
    ports    = ["22"]
  }
}
resource "google_compute_instance" "app" {
  name                = var.name
  zone                = var.zone
  machine_type        = "e2-micro"
  tags                = [var.name]
  deletion_protection = true
  boot_disk {
    auto_delete = false
    initialize_params {
      image = "debian-cloud/debian-12"
      size  = 30
      type  = "pd-standard"
    }
  }
  network_interface {
    subnetwork = google_compute_subnetwork.app.id
    access_config {
      nat_ip       = google_compute_address.app.address
      network_tier = "STANDARD"
    }
  }
  metadata = {
    block-project-ssh-keys = "true"
    ssh-keys               = "unstuckeryops:${var.ssh_public_key}"
  }
  shielded_instance_config { enable_secure_boot = true }
  metadata_startup_script = file("${path.module}/bootstrap.sh")
  # No Google service account is attached: the app needs no project API access.
}
output "address" { value = google_compute_address.app.address }
output "instance" { value = google_compute_instance.app.name }
