import subprocess
from jae.common import log as logging 
from jae.common import cfg
import shutil
import os


CONF = cfg.CONF
LOG = logging.getLogger(__name__)


def inject_key(uuid, key):
    """Inject public key into container"""

    """Get container's pid namespace"""
    LOG.info("Get container's namespace pid")
    pid = subprocess.check_output("sudo docker inspect --format '{{.State.Pid}}' %s" % uuid, shell=True)
    LOG.info("Pid is %s" % pid.strip())

    try:
        LOG.info("Inject public key into container")
        subprocess.check_call("sudo nsenter -t %s --mount -- /bin/bash -c 'echo %s >> /root/.ssh/authorized_keys'" % (pid.strip(), key), shell=True)
        LOG.info("Done")
    except subprocess.CalledProcessError:
        LOG.error("Falied")
        raise


def copy_files(uuid, origin_user, shared_user):
    """Copy data for container shared"""

    base_dir = CONF.base_data_dir
    if not base_dir:
        base_dir = "/home/jae"
    source = os.path.join(base_dir,origin_user,uuid,"www")
    dest = os.path.join(base_dir,shared_user,uuid,"www") 
    try:
        if not os.path.exists(dest):
            LOG.info("Copy files...")
            shutil.copytree(source,dest)
            LOG.info("Done")
    except:
        LOG.error("Copy data for container shared failed")
        raise
    

def cleanup_data(uuid,user_id):
    """Cleanup data when container was deleted"""
    base_dir = CONF.base_data_dir
    if not base_dir:
        base_dir = "/home/jae"
    dest = os.path.join(base_dir,user_id,uuid)
    try:
        LOG.info("Cleanup files...")
        shutil.rmtree(dest)
        LOG.info("Done")
    except:
        LOG.error("Cleanup data for container delete failed")
        raise
