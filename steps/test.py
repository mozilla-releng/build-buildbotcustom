# ***** BEGIN LICENSE BLOCK *****
# Version: MPL 1.1
#
# The contents of this file are subject to the Mozilla Public License Version
# 1.1 (the "License"); you may not use this file except in compliance with
# the License. You may obtain a copy of the License at
# http://www.mozilla.org/MPL/
#
# Software distributed under the License is distributed on an "AS IS" basis,
# WITHOUT WARRANTY OF ANY KIND, either express or implied. See the License
# for the specific language governing rights and limitations under the
# License.
#
# The Original Code is Mozilla-specific Buildbot steps.
#
# The Initial Developer of the Original Code is
# Mozilla Corporation.
# Portions created by the Initial Developer are Copyright (C) 2007
# the Initial Developer. All Rights Reserved.
#
# Contributor(s):
#   Ben Hearsum <bhearsum@mozilla.com>
#   Rob Campbell <rcampbell@mozilla.com>
#   Chris Cooper <coop@mozilla.com>
#   Alice Nodelman <anodelman@mozilla.com>
# ***** END LICENSE BLOCK *****

from buildbot.status.builder import FAILURE, SUCCESS, EXCEPTION
from buildbot.process.properties import WithProperties

from buildbotcustom.steps.base import ShellCommand


class GraphServerPost(ShellCommand):
    flunkOnFailure = True
    name = "graph_server_post"
    description = ["graph", "server", "post"]
    descriptionDone = 'graph server post results complete'

    def __init__(self, server, selector, branch, resultsname, timeout=120,
                 retries=8, sleepTime=5, propertiesFile="properties.json",
                 **kwargs):
        self.super_class = ShellCommand
        self.super_class.__init__(self, **kwargs)
        self.addFactoryArguments(server=server, selector=selector,
                                 branch=branch, resultsname=resultsname,
                                 timeout=timeout, retries=retries,
                                 sleepTime=sleepTime,
                                 propertiesFile=propertiesFile)

        self.command = ['python',
                        WithProperties(
                            '%(toolsdir)s/buildfarm/utils/retry.py'),
                        '-s', str(sleepTime),
                        '-t', str(timeout),
                        '-r', str(retries)]
        self.command.extend(['python',
                             WithProperties('%(toolsdir)s/buildfarm/utils/graph_server_post.py')])
        self.command.extend(['--server', server,
                             '--selector', selector,
                             '--branch', branch,
                             '--buildid', WithProperties('%(buildid)s'),
                             '--sourcestamp', WithProperties(
                                 '%(sourcestamp)s'),
                             '--resultsname', resultsname.replace(' ', '_'),
                             '--properties-file', propertiesFile])

    def start(self):
        timestamp = str(int(self.step_status.build.getTimes()[0]))
        self.command.extend(['--timestamp', timestamp])
        self.super_class.start(self)

    def evaluateCommand(self, cmd):
        result = self.super_class.evaluateCommand(self, cmd)
        if result == FAILURE:
            result = EXCEPTION
            self.step_status.setText(
                ["Automation", "Error:", "failed", "graph", "server", "post"])
            self.step_status.setText2(
                ["Automation", "Error:", "failed", "graph", "server", "post"])
        elif result == SUCCESS:
            self.step_status.setText(["graph", "server", "post", "ok"])
        return result
