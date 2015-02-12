import webob.exc
import os 
import traceback

from jae.common import cfg
from jae.common import log as logging
from jae.common.cfg import Int, Str
from jae.common import utils
from jae.common.mercu import MercurialControl 
from jae.common.exception import NetWorkError
from jae.common import nwutils

from jae.container import driver
from jae import base 
from jae.network import manager


CONF = cfg.CONF

LOG = logging.getLogger(__name__)


class Manager(base.Base):
    def __init__(self):
	super(Manager,self).__init__()

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
		user_id):
	"""
           Create new container.
           There are  steps to do this:
           1. Pull specified images from docker registry.
        """

	LOG.info("CREATE +job create %s" % id)

        """Pull Specified Imag From Docker Registry."""
	LOG.info("pull image %s from registry..." % image_id)
	status = self.driver.pull_image(repository,tag)
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
	    msg="pull image failed!"
	    LOG.error(msg)
	    return webob.exc.HTTPNotFound(explanation=msg)
	LOG.info("pull image succeed!")

        """
        """
        port=resp.json()['Config']['ExposedPorts']
	kwargs = {'Hostname'       : '',
                  'User'           : '',
                  'Memory'         : '',
                  'MemorySwap'     : '',
                  'AttachStdin'    : False,
                  'AttachStdout'   : False,
                  'AttachStderr'   : False,
                  'PortSpecs'      : [],
                  'Tty'            : True,
                  'OpenStdin'      : True,
                  'StdinOnce'      : False,
		  'Env'            : ["REPO_PATH=%s" % repos,
			              "BRANCH=%s" % branch,
	                              "APP_TYPE=%s" % app_type,
	                              "APP_ENV=%s" % app_env,
                                      "SSH_KEY=%s" % ssh_key],
            	  'Cmd'            : ["/opt/start.sh"], 
                  'Dns'            : None,
	          'Image'          : image_uuid,
                  'Volumes'        : {},
                  'VolumesFrom'    : '',
                  'ExposedPorts'   : port,
		  "RestartPolicy": { "Name": "always" }}

	"""Invork docker remote api to create container."""
	resp = self.driver.create(name,kwargs)
	if resp.status_code == 201:
            """Update container status to ``created``"""
	    uuid = resp.json()['Id'] 
	    self.db.update_container(id,uuid=uuid,status="created")



	    """
	    Clone or Pull code before container start.
            This method contains the following two steps:
            1. Create code and logs directory for each container.
            2. Clone or Pull the specified code and update to the specified branch.
	    """
	    self.db.update_container(id,uuid=uuid,status="init")

            short_uuid = uuid[:12]

            root_path = utils.create_root_path(user_id,short_uuid)
            log_path = utils.create_log_path(root_path) 

            repo_name=os.path.basename(repos)
            if utils.repo_exist(root_path,repo_name):
                try:
                    self.mercurial.pull(root_path,repos)
                except:
                    LOG.error("Pull code from %s failed" % repos)
                    raise
            else:
                try:
                    self.mercurial.clone(root_path,repos)
                except:
                    raise
            try:
                self.mercurial.update(root_path,repos,branch)
            except:
                raise
    

            www_path = ["/home/www","/home/jm/www"]
            log_pathes = ["/home/jm/logs","/home/logs"]

            kwargs = {
                'Binds':
                     ['%s:%s' % (root_path,www_path[0]),
                      '%s:%s' % (root_path,www_path[1]),
                      '%s:%s' % (log_path,log_pathes[0]),
                      '%s:%s' % (log_path,log_pathes[1]),
                     ],
                'Dns':
                    [
                      CONF.dns
                    ],
            }

	    """
	    start container and update db status.
	    """
	    status = self.driver.start(uuid,kwargs)
	    if status == 204:
                """If start container succeed, inject fixed
                   ip addr to container"""
	        network = self.network.get_fixed_ip() 
                
                """
                Add db entry immediately to prevent this fixed ip be used again.
                """
                self.db.add_network(dict(container_id=id,fixed_ip=network))
                try:
                    nwutils.set_fixed_ip(uuid,network) 
                except:
                    LOG.error("Set fixed ip %s to container %s failed" % (network,uuid))
                    """Cleanup db entry for ip reuse"""
                    self.db.delete_network(id)
                    return 

                """Update container's network"""
	        self.db.update_container(id,fixed_ip=network)
                """Update container's status"""
		self.db.update_container(id,status="running")
	    if status == 500:
		LOG.error("start container %s error" % uuid)
		self.db.update_container(id,status="error")
	if resp.status_code == 500:
	    self.db.update_container(id,status='error')
	    raise web.exc.HTTPInternalServerError()
	if resp.status_code == 404:
	    LOG.error("no such image %s" % image_uuid)
	    return
        if resp.status_code == 409:
	    self.db.update_container(id,status='error')
            LOG.error("CONFLICT!!!")
            return

	LOG.info("CREATE -job create %s = OK" % id)
    def delete(self,id):
        """Delete container by `id`."""
        ##FIXME(nmg):
	LOG.info("DELETE +job delete %s" % id)
	query = self.db.get_container(id)
	if query.status == 'running':
	    self.db.update_container(id,status="stoping")
	    status = self.driver.stop(query.uuid)
	    if status in (204,304,404):
		self.db.update_container(id,status="deleting")
		status = self.driver.delete(query.uuid)
		if status in (204,404):
		    self.db.delete_container(id)
                    self.db.delete_network(id)
	    if status == 500:
		LOG.error("I donot known what to do")
		return 
	elif query.status == 'error':
	    self.db.update_container(id,status="deleting")
	    status = self.driver.delete(query.uuid)
	    if status in (204,404):
		self.db.delete_container(id)
                self.db.delete_network(id)
	elif query.status == "stoped":
	    status = self.driver.delete(query.uuid)
	    if status in (204,404):
		self.db.delete_container(id)
                self.db.delete_network(id)
	else:
           self.db.update_container(id,status="deleting")
           self.db.delete_container(id)
           self.db.delete_network(id)
        #try:
        #    nwutils.delete_virtual_iface(query.uuid[:8])
        #except:
        #    LOG.warning("delete virtual interface error")  
        #    raise

	LOG.info("DELETE -job delete %s" % id)

    def start(self,id):
	"""
        Start container by id.

        :params id: container id
        """
	LOG.info("START +job start %s" % id)
	self.db.update_container(id,status="starting") 
	query = self.db.get_container(id)
	uuid = query.uuid
	network = query.fixed_ip
        kwargs={"Cmd":["/opt/start.sh"]}
	status = self.driver.start(uuid,kwargs)
	if status == 204:
            """If container start succeed, inject fixed_ip
               to container."""
            try:
                nwutils.set_fixed_ip(uuid,network)
            except:
	        self.db.update_container(id,status="error")
                raise
            """Update container status to running."""
	    self.db.update_container(id,status="running")
	LOG.info("START -job start %s" % id)

    def stop(self,id):
	"""
	Stop container by id.
        :params id: container id
	"""
	LOG.info("STOP +job stop %s" % id)
	
	query = self.db.get_container(id)
	if query.status == "stoped":
	    return
	self.db.update_container(id,status="stoping") 
	status = self.driver.stop(query.uuid)
	if status == 204:
	    self.db.update_container(id,status="stoped")
    
	LOG.info("STOP -job stop %s" % id)

    def destroy(self,name):
	"""
	Destroy a temporary container by a given name.
        :params name: container name
	"""
	self.driver.stop(name)
	self.driver.delete(name)

    def refresh(self,id):
        """Refresh code in container."""
        :params id: container id
        LOG.info("REFRESH +job refresh %s" % id)
        query = self.db.get_container(id)
        if query:
            uuid    = query.uuid
	    user_id = query.user_id
	    repos   = query.repos
	    branch  = query.branch 
            try:
                self.driver.refresh(uuid=uuid,
                                    user_id=user_id,
                                    repos=repos,
                                    branch=branch,
                                    mercurial=self.mercurial)
                self.db.update_container(id,status="running")
  	    except:
                LOG.info("REFRESH -job refresh %s = ERR" % id)
                self.db.update_container(id,status="refresh-failed")
                raise
            LOG.info("REFRESH -job refresh %s = OK" % id)
