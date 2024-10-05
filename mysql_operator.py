import kopf
import kubernetes
import kubernetes.config as k8s_config
import kubernetes.client as k8s
from kubernetes.client.rest import ApiException
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

try:
    k8s_config.load_incluster_config()
    logger.info("Loaded in-cluster Kubernetes configuration")
except k8s_config.ConfigException:
    k8s_config.load_kube_config()
    logger.info("Loaded local kubeconfig")

@kopf.on.create('dbaas.shamim.dev', 'v1', 'mysqls')
def create_mysql(spec, body, namespace, **kwargs):
    name = kwargs['name']
    secret_name = spec.get('secretName') 
    config = spec.get('config', {})

    # configmap
    if config:
        create_mysql_configmap(name, config, body)

    # pvc
    create_mysql_pvc(name, spec, body)

    # sts
    statefulset = create_mysql_statefulset(name, spec, secret_name, config)
    apps_v1 = k8s.AppsV1Api()
    kopf.adopt(statefulset, owner=body)
    try:
        apps_v1.create_namespaced_stateful_set(namespace="default", body=statefulset)
    except ApiException as e:
        logger.error(f"Failed to create StatefulSet: {e}")

    # svc 
    service = create_mysql_service(name, body)
    v1 = k8s.CoreV1Api()
    try:
        v1.create_namespaced_service(namespace="default", body=service)
    except ApiException as e:
        logger.error(f"Failed to create Service: {e}")
    
    # exporter
    create_mysql_exporter(name, secret_name, body)

    # exporter svc
    service = create_exporter_service(name, body)
    try:
        v1.create_namespaced_service(namespace="default", body=service)
    except ApiException as e:
        logger.error(f"Failed to create Exporter Service: {e}")
    
    # vmss
    apps_v1 = k8s.CustomObjectsApi()
    vm_service_scrape = create_vmservicescrape(name, namespace)
    kopf.adopt(vm_service_scrape, owner=body)
    try:
        apps_v1.create_namespaced_custom_object(
            group="operator.victoriametrics.com",
            version="v1beta1",
            namespace=namespace,
            plural="vmservicescrapes",
            body=vm_service_scrape
        )
    except ApiException as e:
        logger.error(f"Failed to create VMServiceScrape: {e}")


@kopf.on.delete('dbaas.shamim.dev', 'v1', 'mysqls')
def delete_mysql(spec, namespace, **kwargs):
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

    config = spec.get('config', {})
    if config:
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
            namespace=namespace,
            plural="vmservicescrapes",
            name=f"{name}-exporter-scrape"
        )
        logger.info(f"Deleted VMServiceScrape {name}-exporter-scrape")
    except ApiException as e:
        logger.error(f"Failed to delete VMServiceScrape: {e}")

def create_owner_reference(meta):
    return [k8s.V1OwnerReference(
        api_version=meta['apiVersion'],
        kind=meta['kind'],
        name=meta['name'],
        uid=meta['uid'],
        controller=True,
        block_owner_deletion=True
    )]

def create_exporter_service(name, owner):
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
    kopf.adopt(service, owner=owner)
    return service

def create_vmservicescrape(name, namespace):
    vm_service_scrape = {
        "apiVersion": "operator.victoriametrics.com/v1beta1",
        "kind": "VMServiceScrape",
        "metadata": {
            "name": f"{name}-exporter-scrape",
            "namespace": f"{namespace}"
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


def create_mysql_configmap(name, config, owner):
    v1 = k8s.CoreV1Api()

    config_data = {"my.cnf": '\n'.join(f"{key}={value}" for key, value in config.items())}
    configmap = k8s.V1ConfigMap(
        metadata=k8s.V1ObjectMeta(name=f"{name}-config"),
        data=config_data
    )
    kopf.adopt(configmap, owner=owner)
    try:
        cm = v1.create_namespaced_config_map(namespace="default", body=configmap)
    except ApiException as e:
        logger.error(f"Failed to create ConfigMap: {e}")

def create_mysql_service(name, owner):
    service = k8s.V1Service(
        metadata=k8s.V1ObjectMeta(name=name),
        spec=k8s.V1ServiceSpec(
            ports=[k8s.V1ServicePort(port=3306)],
            selector={"app": name}
        )
    )
    kopf.adopt(service, owner=owner)
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

def create_mysql_pvc(name, spec, owner):
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
    kopf.adopt(pvc, owner=owner)
    try:
        pvc = v1.create_namespaced_persistent_volume_claim(namespace="default", body=pvc)
        return pvc
    except ApiException as e:
        logger.error(f"Failed to create PVC: {e}")
        return None


def create_mysql_exporter(name, secret_name, owner):
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
    kopf.adopt(exporter_deployment, owner=owner)
    try:
        v1.create_namespaced_deployment(namespace="default", body=exporter_deployment)
    except ApiException as e:
        logger.error(f"Failed to create MySQL exporter deployment: {e}")

def check_mysql_status(namespace, mysql_name):
    api = kubernetes.client.CoreV1Api()

    try:
        pod = api.read_namespaced_pod(namespace=namespace, name=f"{mysql_name}-0")
        pvc = api.read_namespaced_persistent_volume_claim(namespace=namespace, name=f"{mysql_name}-pvc")
        if not pod or not pvc:
            return "Pending", "No MySQL pod found.",  "No MySQL pvc found."

        phase = pod.status.phase
        try:
            pvc_status = pvc.status.conditions[0].type
        except Exception as e:
            pvc_status = pvc.status.phase

        if phase == "Running":
            return "Running", "MySQL is running.", pvc_status
        elif phase == "Pending":
            return "Pending", "MySQL is pending.", pvc_status
        elif phase == "Failed":
            return "Failed", "MySQL pod has failed.", pvc_status
        else:
            return phase, f"MySQL pod is in {phase} state.", pvc_status

    except Exception as e:
        return "Unknown", str(e), str(e)

@kopf.timer('dbaas.shamim.dev', 'v1', 'mysqls', interval=20.0)  
def update_mysql_status(spec, status, namespace, name, **kwargs):
    mysql_status, message, pvc_status = check_mysql_status(namespace, name)

    if not status:
        status = {}

    return {
        "status": {
            "state": mysql_status,
            "message": message,
            "pvc-status": pvc_status
        }
    }


def update_mysql_sts(namespace, mysql_name, cpu, memory):
    apps_api = kubernetes.client.AppsV1Api()

    try:
        sts = apps_api.read_namespaced_stateful_set(name=mysql_name, namespace=namespace)

        sts.spec.template.spec.containers[0].resources = kubernetes.client.V1ResourceRequirements(
            limits={"cpu": cpu, "memory": memory},
            requests={"cpu": cpu, "memory": memory}
        )

        apps_api.patch_namespaced_stateful_set(name=mysql_name, namespace=namespace, body=sts)
        logging.info(f"MySQL deployment {mysql_name} updated with CPU: {cpu}, Memory: {memory}.")

    except Exception as e:
        logging.error(f"Failed to update MySQL deployment {mysql_name}: {str(e)}")


@kopf.on.update('mysqls.dbaas.shamim.dev', field='spec')
def on_update(spec, old, new, name, namespace, status, **kwargs):
    original_spec = status.get('original_spec', {})

    new_storage = int(new['resources']['storage'][0])
    current_storage = int(old['resources']['storage'][0])

    api = kubernetes.client.CustomObjectsApi()
    if new_storage < current_storage:
        mysql = api.get_namespaced_custom_object(
            group="dbaas.shamim.dev",
            version="v1",
            namespace=namespace,
            plural="mysqls",
            name=name,
        )
        mysql['spec']['resources']['storage'] = f"{current_storage}Gi"
        api.patch_namespaced_custom_object(
            group="dbaas.shamim.dev",
            version="v1",
            namespace=namespace,
            plural="mysqls",
            name=name,
            body=mysql
            )
        raise kopf.PermanentError(f"New storage size {new_storage} cannot be smaller than the current size {current_storage}.")


    for field in original_spec:
        if field != 'storage' and spec.get(field) != original_spec[field]:
            raise kopf.PermanentError(f"Only storage changes are allowed. Other changes to '{field}' are not permitted.")

    core_api = kubernetes.client.CoreV1Api()
    pvc_name = f"{name}-pvc" 

    try:
        pvc = core_api.read_namespaced_persistent_volume_claim(name=pvc_name, namespace=namespace)
        pvc.spec.resources.requests['storage'] = f"{new_storage}Gi"
        core_api.patch_namespaced_persistent_volume_claim(pvc_name, namespace, body=pvc)

    except ApiException as e:
        raise kopf.PermanentError(f"Failed to update PVC: {str(e)}")