# Copyright (c) 2014 Hoang Do, Phuc Vo, P. Michiardi, D. Venzano
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import os

from sahara.plugins import context
from sahara.plugins import utils
from sahara_plugin_spark.i18n import _
from sahara_plugin_spark.plugins.spark import config_helper as c_helper
from sahara_plugin_spark.plugins.spark import run_scripts as run


@utils.event_wrapper(True, step=_("Decommission %s") % "Slaves")
def decommission_sl(master, inst_to_be_deleted, survived_inst):
    if survived_inst is not None:
        slavenames = []
        for slave in survived_inst:
            slavenames.append(slave.hostname())
        slaves_content = c_helper.generate_spark_slaves_configs(slavenames)
    else:
        slaves_content = "\n"

    cluster = master.cluster
    sp_home = utils.get_config_value_or_default("Spark", "Spark home", cluster)
    r_master = utils.get_remote(master)
    run.stop_spark(r_master, sp_home)

    # write new slave file to master
    files = {os.path.join(sp_home, 'conf/slaves'): slaves_content}
    r_master.write_files_to(files)

    # write new slaves file to each survived slave as well
    for i in survived_inst:
        with utils.get_remote(i) as r:
            r.write_files_to(files)

    run.start_spark_master(r_master, sp_home)


def _is_decommissioned(r, inst_to_be_deleted):
    cmd = r.execute_command("sudo -u hdfs hadoop dfsadmin -report")
    datanodes_info = parse_dfs_report(cmd[1])
    for i in inst_to_be_deleted:
        for dn in datanodes_info:
            if (dn["Name"].startswith(i.internal_ip)) and (
                    dn["Decommission Status"] != "Decommissioned"):
                return False
    return True


@utils.event_wrapper(True, step=_("Decommission %s") % "DataNodes")
def decommission_dn(nn, inst_to_be_deleted, survived_inst):
    with utils.get_remote(nn) as r:
        r.write_file_to('/etc/hadoop/dn.excl',
                        utils.generate_fqdn_host_names(
                            inst_to_be_deleted))
        run.refresh_nodes(utils.get_remote(nn), "dfsadmin")
        context.sleep(3)

        utils.plugin_option_poll(
            nn.cluster, _is_decommissioned, c_helper.DECOMMISSIONING_TIMEOUT,
            _("Decommission %s") % "DataNodes", 3, {
                'r': r, 'inst_to_be_deleted': inst_to_be_deleted})

        r.write_files_to({
            '/etc/hadoop/dn.incl': utils.
            generate_fqdn_host_names(survived_inst),
            '/etc/hadoop/dn.excl': ""})


def parse_dfs_report(cmd_output):
    report = cmd_output.rstrip().split(os.linesep)
    array = []
    started = False
    for line in report:
        if started:
            array.append(line)
        if line.startswith("Datanodes available"):
            started = True

    res = []
    datanode_info = {}
    for i in range(0, len(array)):
        if array[i]:
            idx = str.find(array[i], ':')
            name = array[i][0:idx]
            value = array[i][idx + 2:]
            datanode_info[name.strip()] = value.strip()
        if not array[i] and datanode_info:
            res.append(datanode_info)
            datanode_info = {}
    if datanode_info:
        res.append(datanode_info)
    return res
