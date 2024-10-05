# MySQL Operator

This project is a Kubernetes operator for managing MySQL instances in a Kubernetes cluster. The operator handles creating and managing MySQL deployments, persistent volume claims (PVCs), services, and other related Kubernetes resources.

## Features

- Automatically deploys MySQL instances based on custom MySQL resources.
- Manages storage, memory, and CPU resources for MySQL pods.
- Ensures updates to MySQL resources while maintaining restrictions (e.g., preventing storage downgrades).
- Reflects the status of MySQL instances in the custom resource status.

## Prerequisites

- Kubernetes cluster (v1.18+)
- Python 3.8+
- pip

## Installation

1. Clone the repository:
   ```bash
   git clone https://github.com/ShamimRahimi/MySQL-Operator.git
   ```
   
2. Change into the project directory:
   ```bash
   cd mysql-operator
   ```

3. Install the required Python packages:
   ```bash
   pip install -r requirements.txt
   ```

## Usage

1. Deploy the custom resource definition (CRD) for MySQL:
   ```bash
   kubectl apply -f crds/crd.yaml
   ```

2. Deploy the operator:
   ```bash
   kubectl apply -f deploy/operator.yaml
   ```

3. Create a MySQL instance by applying a custom resource:
   ```yaml
   apiVersion: dbaas.shamim.dev/v1
   kind: MySQL
   metadata:
     name: shamim-mysql
     namespace: default
   spec:
     image: mysql:8.0
     resources:
       cpu: "100m"
       memory: "500Mi"
       storage: "2Gi"
     secretName: mysql-root-secret
   ```
   ```bahs
   kubectl apply -f mysql_instance.yaml
   ```

4. Monitor the status of the MySQL instance:
   ```bash
   kubectl get mysql shamim-mysql -n default -o yaml
   ```
