import webob.exc
import os
import traceback
import requests


from jae.common import cfg
from jae.common import log as logging
from jae.common.cfg import Int, Str
from jae.common import utils
from jae.common.mercu import MercurialControl
from jae.common.exception import NetWorkError
from jae.common import nwutils
from jae.common import codeutils
from jae.common import executils

from jae.container import driver
from jae import base
from jae.network import manager


CONF = cfg.CONF

LOG = logging.getLogger(__name__)


class Manager(base.Base):
    def __init__(self):
        super(Manager, self).__init__()

        self.driver = driver.API()
        self.mercurial = MercurialControl()
        self.network = manager.NetworkManager()

    def service_init(self):
        """There will be three things doing here:
              1. Register host in db.
              2. Start containers on this node if necessary.
              3. Create rpc producers and consumers. 
           You must do these things before service start.
        """
        return NotImplementedError()

    def create(self,
               id,
               name,
               image_id,
               image_uuid,
               repository,
               tag,
               repos,
               branch,
               app_type,
               app_env,
               ssh_key,
               fixed_ip,
               user_id,
               maven_flags):
        """
            Create new container
        """

        LOG.info("CREATE +job create %s" % id)

        """Pull Specified Imag From Docker Registry."""
        LOG.info("pull image %s from registry..." % image_id)
        status = self.driver.pull_image(repository, tag)
        if status == 404:
            LOG.error("pull failed,no registry found!")
            self.db.update_container(id,
                                     status="error")
            return webob.exc.HTTPNotFound()
        if status == 500:
            LOG.error("pull failed,internal server error!")
            self.db.update_container(id,
                                     status="error")
            return webob.exc.HTTPInternalServerError()

        """Check if the image was pulled successful."""
        resp = self.driver.inspect_image(image_uuid)
        if resp.status_code == 404:
            msg = "pull image failed!"
            LOG.error(msg)
            return webob.exc.HTTPNotFound(explanation=msg)
        LOG.info("pull image succeed!")

        """
        """
        fixed_ip = self.network.get_fixed_ip()
        project = repos.split("/")[-1]
        ip_list = fixed_ip.split(".")
        project_template = "%s%s%s" % (project, ip_list[2], ip_list[3])
        hostname = CONF.hostname_template % (project_template, app_env)

        image_name = "%s/%s:%s" % (CONF.image_registry_endpoint,repository,tag)
        port = resp.json()['Config']['ExposedPorts']
        kwargs = {'Hostname': hostname.lower(),
                  'User': '',
                  'Memory': '',
                  'MemorySwap': '',
                  'AttachStdin': False,
                  'AttachStdout': False,
                  'AttachStderr': False,
                  'PortSpecs': [],
                  'Tty': True,
                  'OpenStdin': True,
                  'StdinOnce': False,
                  'Env': ["REPO_PATH=%s" % repos,
                          "BRANCH=%s" % branch,
                          "APP_TYPE=%s" % app_type,
                          "APP_ENV=%s" % app_env,
                          "SSH_KEY=%s" % ssh_key,
                          "APP_PATH=%s" % maven_flags,
                          "APP_NAME=%s" % repos.split("/")[-1]],
                  'Cmd': [CONF.init_script],
                  'Dns': CONF.dns.split(","),
                  #'Image': image_uuid,
                  'Image': image_name,
                  'Volumes': {},
                  'VolumesFrom': '',
                  'ExposedPorts': port,
                  "RestartPolicy": {"Name": "always"}}

        """Invork docker remote api to create container."""
        resp = self.driver.create(name, kwargs)
        if resp.status_code == 201:
            """Update container status to ``created``"""
            uuid = resp.json()['Id']
            self.db.update_container(id, uuid=uuid, status="created")

            """
            Clone or Pull code before container start.
                This method contains the following two steps:
                1. Create code and logs directory for each container.
                2. Clone or Pull the specified code and update to the specified branch.
            """
            self.db.update_container(id, uuid=uuid, status="init")

            short_uuid = uuid[:12]

            root_path = utils.create_root_path(user_id, short_uuid)
            log_path = utils.create_log_path(root_path)

            repo_name = os.path.basename(repos)
            if utils.repo_exist(root_path, repo_name):
                try:
                    self.mercurial.pull(root_path, repos, branch)
                except:
                    LOG.error("Pull code from %s failed" % repos)
                    LOG.error(traceback.format_exc())
                    return
            else:
                try:
                    self.mercurial.clone(root_path, repos)
                except:
                    LOG.error("Clone code from %s failed" % repos)
                    LOG.error(traceback.format_exc())
                    return
                try:
                    self.mercurial.pull(root_path, repos, branch)
                except:
                    LOG.error("Pull code from %s failed" % repos)
                    LOG.error(traceback.format_exc())
                    return
            try:
                self.mercurial.update(root_path, repos, branch)
            except:
                LOG.error("Update repos %s to branch %s failed" % (repos, branch))
                LOG.error(traceback.format_exc())
                return

            

            """Begin to start the container"""
            #www_path = ["/home/www", "/home/jm/www"]
            if app_type == "php":
                www_path = maven_flags
         
            if app_type == "java":
                www_path = "/home/jm/www"

            log_pathes = ["/home/jm/logs", "/home/logs"]

            kwargs = {
                'Binds':
                    ['%s/%s:%s' % (root_path, os.path.basename(repos), www_path),
                     '%s:%s' % (log_path, log_pathes[0]),
                     '%s:%s' % (log_path, log_pathes[1]),
                    ],
                'Dns': CONF.dns.split(","),
                "DnsSearch": CONF.domainname,
                "NetworkMode": "none",
            }

            """
            start container and update db status.
            """
            status = self.driver.start(uuid, kwargs)
            if status == 204:
                """If start container succeed, inject fixed
                   ip addr to container"""
                # network = self.network.get_fixed_ip()
                network = fixed_ip

                """
                Add db entry immediately to prevent this fixed ip be used again.
                """
                self.db.add_network(dict(container_id=id, fixed_ip=network))
                try:
                    nwutils.set_fixed_ip(uuid, network)
                except:
                    LOG.error("Set fixed ip %s to container %s failed" % (network, uuid))
                    """Cleanup db entry for ip reuse"""
                    self.db.delete_network(id)
                    """Update container's status"""
                    self.db.update_container(id, status="error")
                    #return
                    raise

                """Update container's network"""
                self.db.update_container(id, fixed_ip=network)
                """Update container's status"""
                self.db.update_container(id, status="running")

                try:
                    nwutils.host_init(uuid)
                except:
                    LOG.warning("Container %s start succeed,but init failed" % uuid)

                #"""Update the container's code and start the service after container started"""
                #SWAN_ENDPOINT = "https://swan.int.jumei.com/adminset/server/expand/projects/?keys=4c1342dc-c89e-11e4-b942-002219d55db7"
                #data = requests.get(SWAN_ENDPOINT)
                #for project in data.json():
                #    codes_list = project["codes"]
                #    for codes in codes_list:
                #        if codes["repo"] == repos:
                #            is_java = codes["java"]
                #            if is_java:
                #                path_split = codes["path"].split("-JM")
                #                maven_flags = path_split[0]
                #                root_war = path_split[1] 
                #                project_type = "java" 
                #            else:
                #                project_type = "php"

                if app_type == "php":
                    LOG.info("I am PHP project...")
                    codeutils.composer_code(uuid,
                                            user_id,
                                            repos)
      
                if app_type == "java":
                    LOG.info("I am JAVA Project...")
                    codeutils.maven_code(uuid,
                                         user_id,
                                         repos,
                                         maven_flags)

                self.db.update_container(id, flags=maven_flags)
            if status == 500:
                LOG.error("start container %s error" % uuid)
                self.db.update_container(id, status="error")
        if resp.status_code == 500:
            self.db.update_container(id, status='error')
            raise web.exc.HTTPInternalServerError()
        if resp.status_code == 404:
            LOG.error("no such image %s" % image_uuid)
            return
        if resp.status_code == 409:
            self.db.update_container(id, status='error')
            LOG.error("CONFLICT!!!")
            return

        LOG.info("CREATE -job create %s = OK" % id)

    def delete(self, id):
        """Delete container by `id`."""
        # #FIXME(nmg):
        LOG.info("DELETE +job delete %s" % id)
        query = self.db.get_container(id)
        associate_query = self.db.get_shared_container(query.uuid)
        for associate in associate_query:
            LOG.info("Delete associate containers")
            self.db.update_container(associate.id, status="deleting")
            self.db.delete_container(associate.id)
            if associate.uuid is not None:
                try:
                    executils.cleanup_data(associate.uuid[:12],associate.user_id)
                except:
                    LOG.error("Cleanup data for container %s" % associate.uuid[:12])
        status = self.driver.delete(query.uuid)
        if status == 500:
            LOG.error("Delete container %s return 500,please check the docker's log for what happend.")
        try:
            self.db.delete_network(id)
        except:
            LOG.warning("network of the container %s clean up failed" % uuid)

        LOG.info("DELETE -job delete %s" % id)

    def start(self, id):
        """
        Start container by id.

        :params id: container id
        """
        LOG.info("START +job start %s" % id)
        self.db.update_container(id, status="starting")
        #"""Update container's code first"""
        #query = self.db.get_container(id)
        #self.refresh(id, query.branch)
       
        """Get container's uuid from db entry"""
        query = self.db.get_container(id)
        uuid = query.uuid
        network = query.fixed_ip
        kwargs = {"Cmd": [CONF.init_script],
                  "NetworkMode": "none",
                  'Dns': CONF.dns.split(","),
                  "DnsSearch": CONF.domainname}
        status = self.driver.start(uuid, kwargs)
        if status == 204:
            """If container start succeed, inject fixed_ip
               to container."""
            try:
                nwutils.set_fixed_ip(uuid, network)
            except:
                self.db.update_container(id, status="error")
                raise

            """Update container status to running."""
            self.db.update_container(id, status="running")

            try:
                nwutils.host_init(uuid)
            except:
                LOG.warning("Container %s start succeed,but init failed" % uuid)

        LOG.info("START -job start %s" % id)

    def stop(self, id):
        """
        Stop container by id.

        :params id: container id

        """
        LOG.info("STOP +job stop %s" % id)

        query = self.db.get_container(id)
        if query.status == "stoped":
            return
        self.db.update_container(id, status="stoping")
        status = self.driver.stop(query.uuid)
        if status == 204:
            #try:
            #    nwutils.delete_virtual_interface(query.uuid)
            #except:
            #    LOG.warning("veth%s delete failed,please do it manual" % query.uuid[:8])
            self.db.update_container(id, status="stoped")

        LOG.info("STOP -job stop %s" % id)

    def destroy(self, name):
        """
        Destroy a temporary container by a given name.

        :params name: container name
        """
        self.driver.stop(name)
        self.driver.delete(name)

    def refresh(self, id, branch):
        """
        Refresh code in container

        :params id: container id

        """
        LOG.info("REFRESH +job refresh %s" % id)
        self.db.update_container(id, status="refreshing")

        query = self.db.get_container(id)
        if query:
            uuid = query.uuid
            user_id = query.user_id
            repos = query.repos
            branch = branch
            maven_flags=query.flags
            app_type=query.app_type
            try:
                self.driver.refresh(uuid=uuid,
                                    user_id=user_id,
                                    repos=repos,
                                    branch=branch,
                                    maven_flags=maven_flags,
                                    app_type=app_type,
                                    mercurial=self.mercurial)
                self.db.update_container(id, status="running")
                #self.db.update_container(id, branch=branch)
        
                associate_query = self.db.get_shared_container(query.uuid)
                for associate in associate_query:
                    #if associate.uuid != query.uuid:
                    LOG.info("Update associate container's branch")
                    self.db.update_container(associate.id, branch=branch)
            except:
                LOG.info("REFRESH -job refresh %s = ERR" % id)
                self.db.update_container(id, status="refresh-failed")
                raise
            LOG.info("REFRESH -job refresh %s = OK" % id)

    def share(self,id,shared_id,uuid,key,origin_user,shared_user):
        """Add public key to container identified by id"""
        LOG.info("SHARE +job sharing %s" % id) 

        try:
            """Update the origin container's status"""
            self.db.update_container(id,status="sharing")

            """Update the shared container's status"""
            self.db.update_container(shared_id,status="sharing")

            self.driver.share(uuid,key,origin_user,shared_user)

            """Update the origin container's status"""
            self.db.update_container(id,status="shared")

            """Update the shared container's status"""
            self.db.update_container(shared_id,status="shared")

            LOG.info("SHARE -job share %s = OK" % id)
        except:
            LOG.info("SHARE -job share %s = ERR" % id)
            """Update the origin container's status"""
            self.db.update_container(id, status="shared-failed")

            """Update the shared container's status"""
            self.db.update_container(shared_id, status="shared-failed")
            raise
