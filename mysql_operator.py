import kopf
import kubernetes.client as k8s
from kubernetes.client.rest import ApiException
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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
    pvc_list = v1.list_namespaced_persistent_volume_claim(namespace="default", label_selector=f"app={name}")
    for pvc in pvc_list.items:
        try:
            v1.delete_namespaced_persistent_volume_claim(pvc.metadata.name, namespace="default")
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
        v1.delete_namespaced_persistent_volume_claim(name + '-pvc', namespace="default")
    except ApiException as e:
        logger.error(f"Failed to delete pvc: {e}")

def create_mysql_configmap(name, config):
    v1 = k8s.CoreV1Api()
    # Convert the config object into key-value pairs
    config_data = {str(key): str(value) for key, value in config.items()}
    configmap = k8s.V1ConfigMap(
        metadata=k8s.V1ObjectMeta(name=f"{name}-config"),
        data=config_data
    )
    try:
        v1.create_namespaced_config_map(namespace="default", body=configmap)
    except ApiException as e:
        logger.error(f"Failed to create ConfigMap: {e}")

def create_mysql_service(name):
    v1 = k8s.CoreV1Api()
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
        'name': name + '-mysql-pvc',
        'mountPath': '/var/lib/mysql'
    }]

    volumes = [{
        'name': name + '-mysql-pvc',
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
    v1.create_namespaced_persistent_volume_claim(namespace="default", body=pvc)
