import uuid
import copy
import webob.exc
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.util import object_state
from sqlalchemy import inspect
import jsonschema

from jae import wsgi
from jae import base
from jae.common.timeutils import isotime
from jae.common import log as logging
from jae.common.response import Response, ResponseObject

LOG = logging.getLogger(__name__)


class Controller(base.Base):
    def __init__(self):
        super(Controller, self).__init__()

    def index(self, request):
        """
        Get all hosts.
        """
        hosts = []
        query = self.db.get_hosts()
        for item in query:
            host = {
                'id': item.id,
                'host': item.host,
                'port': item.port,
                'zone': item.zone,
            }
            hosts.append(host)

        return ResponseObject(hosts)

    def show(self, request, id):
        """
        Show host detail according to host's id. 
        """
        host = {} 
        query = self.db.get_host(id)
        print 'query',query
        if query is not None:
            host = {'id': query.id,
                    'host': query.host,
                    'port': query.port,
                    'zone': query.zone}

        return ResponseObject(host)

    def create(self, request, body):
        """
        Add user db entry for specified project.
       
        NOTE(nmg):`project` isa `project instance` which
                   get from db. you must insert a `project
                   instance` object in model `User`'s `projects`
                   attribute.and deepcoy is used for disattach session
                   which `project` attached.
        FIXME(nmg):this is ugly,try to fixed it.
        """

        schema = {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 255
                },
                "email": {
                    "type": "string",
                    "pattern": "^.*@.*$",
                },
                "role_id": {
                    "enum": ['0', '1', '2','3']
                },
                "swan": {
                    "enum": ['0','1']
                },
                "project_id": {
                    "type": "string",
                    "minLength": 32,
                    "maxLength": 64,
                    "pattern": "^[a-zA-z0-9]*$",
                }
            }
        }
        try:
            self.validator(body, schema)
        except jsonschema.exceptions.ValidationError as ex:
            LOG.error(ex)
            return webob.exc.HTTPBadRequest()

        name = body.pop("name", "")
        email = body.pop("email", "")
        role_id = body.pop("role_id", "")
        swan = body.pop("swan","-1")
        project_id = body.pop("project_id", "")
        project = self.db.get_project(project_id)

        try:
            user_ref = self.db.add_user(dict(id=uuid.uuid4().hex,
                                             name=name,
                                             email=email,
                                             role_id=role_id,
                                             swan=swan),
                                        project=project)
        except IntegrityError, err:
            LOG.error(err)
            return webob.exc.HTTPInternalServerError()

        return webob.exc.HTTPCreated()

    def delete(self, request, id):
        """
        Delete user by `id`
        """
        try:
            LOG.info("Delete user %s" % id)
            self.db.delete_user(id)
        except:
            raise
        """return webob.exc.HTTPNoContent() seems more better."""
        return webob.exc.HTTPNoContent()


def create_resource():
    return wsgi.Resource(Controller())
