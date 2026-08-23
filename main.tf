terraform {
	required_providers {
		google = {
			source  = "hashicorp/google"
			version = "6.8.0"
		}
	}
}

provider "google" {
	project = "< PROJECT NAME >"
	region  = "europe-west2"		# alter if using a different region/zone
	zone    = "europe-west2-a"		# for subsequent runs of the experiment
}

resource "google_compute_network" "vpc_network" {
	name                    = "terraform-network"
	auto_create_subnetworks = false
}

resource "google_compute_subnetwork" "benchmarking_subnet" {
	name          = "benchmarking-subnet"
	ip_cidr_range = "10.0.1.0/24"
	region        = "europe-west2"
	network       = google_compute_network.vpc_network.id
}

resource "google_compute_firewall" "allow_ssh" {
	name    = "allow-ssh"
	network = google_compute_network.vpc_network.name

	allow {
		protocol = "tcp"
		ports    = ["22"]
	}

	# SSH needs to stay reachable from wherever the orchestrator (_run.py) runs.
	source_ranges = ["0.0.0.0/0"]
}

resource "google_compute_firewall" "allow_db_internal" {
	name    = "allow-databases-internal-only"
	network = google_compute_network.vpc_network.name

	allow {
		protocol = "tcp"
		ports    = ["3306", "27017"]
	}

	# Only the driver (and other nodes in the VPC) ever need to reach these ports, so scope them to the internal subnet.
	source_ranges = [google_compute_subnetwork.benchmarking_subnet.ip_cidr_range]
}

resource "google_compute_instance" "ycsb_driver_node" {
	name         = "sme-ycsb-driver-node"
	machine_type = "e2-standard-8" 		# 8 vCPUs and 32GB RAM to eliminate bottlenecks
	zone         = "europe-west2-a"

	boot_disk {
		initialize_params {
		  image = "ubuntu-os-cloud/ubuntu-2204-lts"
		  size  = 60 
		  type  = "pd-ssd"
		}
	}

	network_interface {
		subnetwork = google_compute_subnetwork.benchmarking_subnet.id
		access_config {}
	}

	# Ensure the instance has permission to pull/push metadata attributes natively
	service_account {
		scopes = ["cloud-platform"]
	}

	metadata = { 
		ssh-keys = "ubuntu:${file("~/.ssh/id_rsa.pub")}" 
	}
}

resource "google_compute_instance" "mysql_target_node" {
	name         = "sme-mysql-target-node"
	machine_type = "e2-medium" 				# 2 vCPUs, 4GB RAM
	zone         = "europe-west2-a"

	boot_disk {
		initialize_params {
			image = "ubuntu-os-cloud/ubuntu-2204-lts"
			size  = 30 						# 30GB standard storage database allocation
			type  = "pd-ssd"
		}
	}

	network_interface {
		subnetwork = google_compute_subnetwork.benchmarking_subnet.id
		access_config {}
	}

	metadata_startup_script = <<EOF
		#!/bin/bash
		echo "Bare metal MySQL host booted cleanly"
		EOF

	metadata = { 
		ssh-keys = "ubuntu:${file("~/.ssh/id_rsa.pub")}" 
	}
}

resource "google_compute_instance" "mongodb_target_node" {
	name         = "sme-mongodb-target-node"
	machine_type = "e2-medium" 				# 2 vCPUs, 4GB RAM
	zone         = "europe-west2-a"

	boot_disk {
		initialize_params {
			image = "ubuntu-os-cloud/ubuntu-2204-lts"
			size  = 30 						# 30GB standard storage database allocation
			type  = "pd-ssd"
		}
	}

	network_interface {
		subnetwork = google_compute_subnetwork.benchmarking_subnet.id
		access_config {}
	}

	metadata_startup_script = <<EOF
		#!/bin/bash
		echo "Bare metal MongoDB host booted cleanly"
		EOF

	metadata = { 
		ssh-keys = "ubuntu:${file("~/.ssh/id_rsa.pub")}" 
	}
}

output "driver_public_ip" {
  value       = google_compute_instance.ycsb_driver_node.network_interface[0].access_config[0].nat_ip
  description = "The public IP of the driver node"
}

output "mysql_internal_ip" {
  value       = google_compute_instance.mysql_target_node.network_interface[0].network_ip
  description = "The true internal IP of the MySQL node"
}

output "mongodb_internal_ip" {
  value       = google_compute_instance.mongodb_target_node.network_interface[0].network_ip
  description = "The true internal IP of the MongoDB node"
}