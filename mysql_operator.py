import kopf
import kubernetes.config as k8s_config
import kubernetes.client as k8s
from kubernetes.client.rest import ApiException
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load Kubernetes configuration
try:
    k8s_config.load_incluster_config()
    logger.info("Loaded in-cluster Kubernetes configuration")
except k8s_config.ConfigException:
    k8s_config.load_kube_config()
    logger.info("Loaded local kubeconfig")

@kopf.on.create('dbaas.shamim.dev', 'v1', 'mysqls')
def create_mysql(spec, **kwargs):
    name = kwargs['name']
    secret_name = spec.get('secretName') 
    config = spec.get('config', {})

    if config:
        create_mysql_configmap(name, config)

    create_mysql_pvc(name, spec)
    statefulset = create_mysql_statefulset(name, spec, secret_name, config)
    apps_v1 = k8s.AppsV1Api()
    try:
        apps_v1.create_namespaced_stateful_set(namespace="default", body=statefulset)
    except ApiException as e:
        logger.error(f"Failed to create StatefulSet: {e}")

    service = create_mysql_service(name)
    v1 = k8s.CoreV1Api()
    try:
        v1.create_namespaced_service(namespace="default", body=service)
    except ApiException as e:
        logger.error(f"Failed to create Service: {e}")
    
    create_mysql_exporter(name, secret_name)

    service = create_exporter_service(name)
    try:
        v1.create_namespaced_service(namespace="default", body=service)
    except ApiException as e:
        logger.error(f"Failed to create Exporter Service: {e}")
    
    # Create the VMServiceScrape for the exporter
    apps_v1 = k8s.CustomObjectsApi()
    vm_service_scrape = create_vmservicescrape(name)
    try:
        apps_v1.create_namespaced_custom_object(
            group="operator.victoriametrics.com",
            version="v1beta1",
            namespace="monitoring-system",
            plural="vmservicescrapes",
            body=vm_service_scrape
        )
    except ApiException as e:
        logger.error(f"Failed to create VMServiceScrape: {e}")

@kopf.on.delete('dbaas.shamim.dev', 'v1', 'mysqls')
def delete_mysql(spec, **kwargs):
    name = kwargs['name']
    k8s_client = k8s.ApiClient()
    apps_v1 = k8s.AppsV1Api(k8s_client)

    try:
        apps_v1.delete_namespaced_stateful_set(name, namespace="default")
    except ApiException as e:
        logger.error(f"Failed to delete StatefulSet: {e}")

    v1 = k8s.CoreV1Api(k8s_client)
    try:
        v1.delete_namespaced_persistent_volume_claim(name + '-pvc', namespace="default")
    except ApiException as e:
        logger.error(f"Failed to delete PVC: {e}")

    try:
        v1.delete_namespaced_config_map(name + '-config', namespace="default")
    except ApiException as e:
        logger.error(f"Failed to delete ConfigMap: {e}")

    try:
        v1.delete_namespaced_service(name, namespace="default")
    except ApiException as e:
        logger.error(f"Failed to delete Service: {e}")

    try:
        v1.delete_namespaced_service(name + "-exporter", namespace="default")
    except ApiException as e:
        logger.error(f"Failed to delete Service: {e}")

    try:
        apps_v1.delete_namespaced_deployment(name=f"{name}-exporter", namespace="default")
    except ApiException as e:
        logger.error(f"Failed to delete MySQL exporter deployment: {e}")

    crd_api = k8s.CustomObjectsApi(k8s_client)
    try:
        crd_api.delete_namespaced_custom_object(
            group="operator.victoriametrics.com",
            version="v1beta1",
            namespace="monitoring-system",
            plural="vmservicescrapes",
            name=f"{name}-exporter-scrape"
        )
        logger.info(f"Deleted VMServiceScrape {name}-exporter-scrape")
    except ApiException as e:
        logger.error(f"Failed to delete VMServiceScrape: {e}")

def create_exporter_service(name):
    service = k8s.V1Service(
        metadata=k8s.V1ObjectMeta(
            name=f"{name}-exporter",
            labels={"app": f"{name}-exporter"}
        ),
        spec=k8s.V1ServiceSpec(
            ports=[k8s.V1ServicePort(
                name="metrics",
                port=9104,
                target_port=9104
            )],
            selector={"app": f"{name}-exporter"},
            type="ClusterIP"
        )
    )
    return service

def create_vmservicescrape(name):
    vm_service_scrape = {
        "apiVersion": "operator.victoriametrics.com/v1beta1",
        "kind": "VMServiceScrape",
        "metadata": {
            "name": f"{name}-exporter-scrape",
            "namespace": "monitoring-system"
        },
        "spec": {
            "endpoints": [
                {
                    "port": "metrics",
                    "path": "/metrics"
                }
            ],
            "namespaceSelector": {
                "matchNames": ["default"]
            },
            "selector": {
                "matchLabels": {
                    "app": f"{name}-exporter"
                }
            }
        }
    }
    return vm_service_scrape

def create_mysql_configmap(name, config):
    v1 = k8s.CoreV1Api()
    # Convert the config object into key-value pairs
    config_data = {"my.cnf": '\n'.join(f"{key}={value}" for key, value in config.items())}
    configmap = k8s.V1ConfigMap(
        metadata=k8s.V1ObjectMeta(name=f"{name}-config"),
        data=config_data
    )
    try:
        v1.create_namespaced_config_map(namespace="default", body=configmap)
    except ApiException as e:
        logger.error(f"Failed to create ConfigMap: {e}")

def create_mysql_service(name):
    service = k8s.V1Service(
        metadata=k8s.V1ObjectMeta(name=name),
        spec=k8s.V1ServiceSpec(
            ports=[k8s.V1ServicePort(port=3306)],
            selector={"app": name}
        )
    )
    return service

def create_mysql_statefulset(name, spec, secret_name, config):
    volume_mounts = [{
        'name': f"{name}-pvc",
        'mountPath': '/var/lib/mysql'
    }]

    volumes = [{
        'name': f"{name}-pvc",
        'persistentVolumeClaim': {
            'claimName': f"{name}-pvc"
        }
    }]

    if config:
        volumes.append({
            'name': 'config-volume',
            'configMap': {
                'name': f"{name}-config"
            }
        })
        volume_mounts.append({
            'name': 'config-volume',
            'mountPath': '/etc/mysql/conf.d/my.cnf',
            'subPath': 'my.cnf'
        })
    
    return {
        'apiVersion': 'apps/v1',
        'kind': 'StatefulSet',
        'metadata': {
            'name': name,
            'labels': {
                'app': name
            }
        },
        'spec': {
            'serviceName': name,
            'replicas': 1,
            'selector': {'matchLabels': {'app': name}},
            'template': {
                'metadata': {'labels': {'app': name}},
                'spec': {
                    'containers': [{
                        'name': 'mysql',
                        'image': spec.get('image', 'hub.hamdocker.ir/mysql:latest'),
                        'env': [{
                            'name': 'MYSQL_ROOT_PASSWORD',
                            'valueFrom': {
                                'secretKeyRef': {
                                    'name': secret_name,
                                    'key': 'password'
                                }
                            }
                        }],
                        'ports': [{'containerPort': 3306}],
                        'resources': {
                            'requests': {
                                'cpu': spec['resources'].get('cpu', '500m'),
                                'memory': spec['resources'].get('memory', '512Mi')
                            }
                        },
                        'volumeMounts': volume_mounts
                    }],
                    'nodeSelector': spec.get('nodeSelector', {}), 
                    'tolerations': spec.get('tolerations', []),  
                    'volumes': volumes
                }
            },
        }
    }

def create_mysql_pvc(name, spec):
    v1 = k8s.CoreV1Api()
    pvc = k8s.V1PersistentVolumeClaim(
        metadata=k8s.V1ObjectMeta(name=f"{name}-pvc"),
        spec=k8s.V1PersistentVolumeClaimSpec(
            access_modes=['ReadWriteOnce'],
            resources=k8s.V1ResourceRequirements(
                requests={
                    'storage': spec['resources']['storage']
                }
            ),
            storage_class_name='rawfile-localpv'
        )
    )
    try:
        v1.create_namespaced_persistent_volume_claim(namespace="default", body=pvc)
    except ApiException as e:
        logger.error(f"Failed to create PVC: {e}")


def create_mysql_exporter(name, secret_name):
    v1 = k8s.AppsV1Api()
    exporter_deployment = k8s.V1Deployment(
        metadata=k8s.V1ObjectMeta(name=f"{name}-exporter", labels={"app": f"{name}-exporter"}),
        spec=k8s.V1DeploymentSpec(
            replicas=1,
            selector=k8s.V1LabelSelector(
                match_labels={"app": f"{name}-exporter"}
            ),
            template=k8s.V1PodTemplateSpec(
                metadata=k8s.V1ObjectMeta(labels={"app": f"{name}-exporter"}),
                spec=k8s.V1PodSpec(
                    containers=[
                        k8s.V1Container(
                            name="mysqld-exporter",
                            image="hub.hamdocker.ir/prom/mysqld-exporter:latest",
                            command=[
                                "/bin/mysqld_exporter", 
                                "--mysqld.username=root:$MYSQL_ROOT_PASSWORD",
                                f"--mysqld.address={name}.default.svc.cluster.local:3306"
                            ],
                            env=[
                                k8s.V1EnvVar(
                                    name="MYSQL_ROOT_PASSWORD",
                                    value_from=k8s.V1EnvVarSource(
                                        secret_key_ref=k8s.V1SecretKeySelector(
                                            name=secret_name,
                                            key="password"
                                        )
                                    )
                                ),
                            ],
                            ports=[k8s.V1ContainerPort(container_port=9104)]
                        )
                    ]
                )
            )
        )
    )
    try:
        v1.create_namespaced_deployment(namespace="default", body=exporter_deployment)
    except ApiException as e:
        logger.error(f"Failed to create MySQL exporter deployment: {e}")