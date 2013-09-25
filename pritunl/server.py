from constants import *
from pritunl import app_server, openssl_lock
from config import Config
from organization import Organization
from event import Event
from log_entry import LogEntry
import uuid
import os
import shutil
import subprocess

class Server(Config):
    str_options = ['name', 'network', 'interface', 'protocol',
        'local_network', 'primary_organization', 'primary_user',
        'organizations']
    int_options = ['port']
    list_options = ['organizations']

    def __init__(self, id=None, name=None, network=None, interface=None,
            port=None, protocol=None, local_network=None, organizations=[]):
        Config.__init__(self)

        if id is None:
            self._initialized = False
            self.id = uuid.uuid4().hex
            self.name = name
            self.network = network
            self.interface = interface
            self.port = port
            self.protocol = protocol
            self.local_network = local_network
            self.organizations = organizations
        else:
            self._initialized = True
            self.id = id

        self.path = os.path.join(app_server.data_path, SERVERS_DIR, self.id)
        self.ovpn_conf_path = os.path.join(self.path, TEMP_DIR, OVPN_CONF_NAME)
        self.dh_param_path = os.path.join(self.path, DH_PARAM_NAME)
        self.ifc_pool_path = os.path.join(self.path, IFC_POOL_NAME)
        self.ca_cert_path = os.path.join(self.path, TEMP_DIR, OVPN_CA_NAME)
        self.set_path(os.path.join(self.path, 'server.conf'))

        if not self._initialized:
            self._initialize()

    def _initialize(self):
        os.makedirs(os.path.join(self.path, TEMP_DIR))
        self._generate_dh_param()
        self.commit()
        LogEntry(message='Created new server.')

    def remove(self):
        self._remove_primary_user()
        shutil.rmtree(self.path)
        LogEntry(message='Deleted server.')
        Event(type=SERVERS_UPDATED)

    def commit(self):
        Config.commit(self)
        Event(type=SERVERS_UPDATED)

    def _create_primary_user(self):
        if not self.organizations:
            return
        org = Organization(self.organizations[0])
        self.primary_organization = org.id
        user = org.new_user(CERT_SERVER, SERVER_USER_PREFIX + self.id)
        self.primary_user = user.id
        try:
            self.commit()
        except:
            user.remove()
            raise

    def add_org(self, org_id):
        org = Organization(org_id)
        if org.id in self.organizations:
            return
        self.organizations.append(org.id)
        self.commit()
        Event(type=SERVER_ORGS_UPDATED)

    def _remove_primary_user(self):
        if not self.primary_organization or not self.primary_user:
            return
        org = Organization(self.primary_organization)
        if not org:
            return
        user = org.get_user(self.primary_user)
        if not user:
            return
        self.primary_organization = None
        self.primary_user = None
        if user:
            user.remove()

    def remove_org(self, org_id):
        if org_id in self.organizations:
            if self.primary_organization == org_id:
                self._remove_primary_user()
            self.organizations.remove(org_id)
            self.commit()
            Event(type=SERVER_ORGS_UPDATED)

    def _generate_dh_param(self):
        args = [
            'openssl', 'dhparam',
            '-out', self.dh_param_path, str(DH_PARAM_BITS)
        ]
        subprocess.check_call(args, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE)

    def _parse_network(self, network):
        network_split = network.split('/')
        address = network_split[0]
        cidr = int(network_split[1])
        subnet = ('255.' * (cidr / 8)) + str(
            int(('1' * (cidr % 8)).ljust(8, '0'), 2))
        subnet += '.0' * (3 - subnet.count('.'))
        return (address, subnet)

    def _generate_ovpn_conf(self):
        if not self.organizations:
            return

        if not self.primary_organization or not self.primary_user:
            self._create_primary_user()

        if not os.path.isfile(self.dh_param_path):
            self._generate_dh_param()

        primary_org = Organization(self.primary_organization)
        primary_user = primary_org.get_user(self.primary_user)

        with open(self.ca_cert_path, 'w') as server_ca_cert:
            for org_id in self.organizations:
                ca_path = Organization(org_id).ca_cert.cert_path
                with open(ca_path, 'r') as org_ca_cert:
                    server_ca_cert.write(org_ca_cert.read())

        if self.local_network:
            push = 'route %s %s' % self._parse_network(
                self.local_network)
        else:
            push = 'redirect-gateway'

        with open(self.ovpn_conf_path, 'w') as ovpn_conf:
            ovpn_conf.write(OVPN_SERVER_CONF % (
                self.port,
                self.protocol,
                self.interface,
                self.ca_cert_path,
                primary_user.cert_path,
                primary_user.key_path,
                self.dh_param_path,
                '%s %s' % self._parse_network(self.network),
                self.ifc_pool_path,
                push,
            ))

    @staticmethod
    def count_servers():
        return len(os.listdir(os.path.join(app_server.data_path, SERVERS_DIR)))

    @staticmethod
    def get_servers():
        path = os.path.join(app_server.data_path, SERVERS_DIR)
        servers = []
        if os.path.isdir(path):
            for server_id in os.listdir(path):
                servers.append(Server(server_id))
        return servers
