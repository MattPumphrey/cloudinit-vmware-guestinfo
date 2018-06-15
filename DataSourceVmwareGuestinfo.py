import subprocess
import os
import json
import xml.etree.ElementTree as ET
import cloudinit.util as util

from cloudinit import log as logging
from cloudinit import sources
from cloudinit.net import eni

LOG = logging.getLogger(__name__)

class DataSourceVmwareGuestinfo(sources.DataSource):

    dsname = "VmwareGuestinfo"

    def __init__(self, sys_cfg, distro, paths, ud_proc=None):
        sources.DataSource.__init__(self, sys_cfg, distro, paths, ud_proc)
        self.metadata = {}
        self.userdata_raw = ''

    def _get_data(self):
        rpctool = self._which("vmware-rpctool")
        if rpctool is None:
            LOG.info("No vmware-rpctool found (PATH is %s)" % self._paths())
            return False
        try:
            self.userdata_raw = self._guestinfo("cloudinit.userdata")
            if self.userdata_raw is None:
                return False
            self.metadata = {}
            meta = self._guestinfo("cloudinit.metadata")
            if meta is not None:
                try:
                    self.metadata = json.loads(meta)
                except ValueError as e:
                    LOG.error("Failed to decode json %r: %r", e, meta)
                    return False
            ovf = self._guestinfo("ovfEnv")
            if ovf is not None:
                try:
                    self.metadata.update( self._parse_ovf(ovf) )
                except ValueError as e:
                    LOG.error("Failed to parse ovf %r: %r", e, ovf)
                    return False
        except (CommunicationError, OSError) as e:
            LOG.error(e)
            return False
        self._network_interfaces_from_metadata()
        return True

    def _network_interfaces_from_metadata(self):
        """Brings up the network"""
        if eni is not None and 'network-config' in self.metadata:
            LOG.info("Skipped setting network-interfaces")
        elif 'network-interfaces' in self.metadata:
            if NETWORK_VIA_DISTRO:
                self._network_interfaces_via_distro()
            else:
                self._network_interfaces_direct()

    def _network_interfaces_via_distro(self):
        self.distro.apply_network(self.metadata['network-interfaces'])

    def _network_interfaces_direct(self):
        util.write_file("/etc/network/interfaces",
            self.metadata['network-interfaces'])
        try:
            (out, err) = util.subp(['ifup', '--all'])
            if len(out) or len(err):
                LOG.warn("ifup --all had stderr: %s" % err)
        except subprocess.CalledProcessError as exc:
            LOG.warn("ifup --all failed: %s" % (exc.output[1]))

    def _parse_ovf(self, ovf):
        """Parses ovfEnv guestinfo"""
        if ovf == "":
          return {}
        tree = ET.fromstring(ovf)
        rt = {}
        for props in tree.findall("{http://schemas.dmtf.org/ovf/environment/1}PropertySection"):
          for prop in props:
              rt[ prop.attrib["{http://schemas.dmtf.org/ovf/environment/1}key"] ] = prop.attrib["{http://schemas.dmtf.org/ovf/environment/1}value"]
        return rt

    def _which(self, filename):
        """Finds an executable"""
        candidates = []
        for location in self._paths():
            candidate = os.path.join(location, filename)
            if os.path.isfile(candidate):
                return candidate
        return None

    def _guestinfo(self, key):
        rpctool = self._which("vmware-rpctool")
        if rpctool is None:
            LOG.info("No vmware-rpctool found (PATH is %s)" % self._paths())
            return False
        LOG.debug("Running %s 'info-get guestinfo.%s'", rpctool, key)
        p1 = subprocess.Popen([rpctool,"info-get guestinfo."+key], stdout=subprocess.PIPE, stdin=None)
        ud, _ = p1.communicate()
        if p1.returncode == 1:
            LOG.info("vmware-rpctool found no guestinfo.%s",key)
            return None
        if p1.returncode != 0:
            raise CommunicationError("vmware-rpctool exited with %d" % p1.returncode)
        return ud.decode(encoding="UTF-8")

    def _paths(self):
        locations = os.environ.get("PATH").split(os.pathsep)
        if 'path' in self.ds_cfg:
          locations = self.ds_cfg["path"] + locations
        return locations

    def get_instance_id(self):
        if not self.metadata or 'instance-id' not in self.metadata:
            # vmware puts a uuid in the bios
            with open('/sys/class/dmi/id/product_uuid', 'r') as f:
                return str(f.read()).rstrip()
        return str(self.metadata['instance-id'])

    @property
    def network_config(self):
        if 'network-config' in self.metadata:
            return self.metadata['network-config']
        if 'network-interfaces' in self.metadata and eni is not None:
            return eni.convert_eni_data(self.metadata['network-interfaces'])
        return None

# Used to match classes to dependenciess
datasources = [
    (DataSourceVmwareGuestinfo, (sources.DEP_FILESYSTEM, sources.DEP_NETWORK)),
    (DataSourceVmwareGuestinfo, []),
]

# Return a list of data sources that match this set of dependencies
def get_datasource_list(depends):
    return sources.list_from_depends(depends, datasources)
