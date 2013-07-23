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


def formatBytes(bytes, sigDigits=3):
    # Force a float calculation
    bytes = float(str(bytes) + '.0')

    if bytes > 1024 ** 3:
        formattedBytes = setSigDigits(bytes / 1024 ** 3, sigDigits) + 'G'
    elif bytes > 1024 ** 2:
        formattedBytes = setSigDigits(bytes / 1024 ** 2, sigDigits) + 'M'
    elif bytes > 1024 ** 1:
        formattedBytes = setSigDigits(bytes / 1024, sigDigits) + 'K'
    else:
        formattedBytes = setSigDigits(bytes, sigDigits)
    return str(formattedBytes) + 'B'


def formatCount(number, sigDigits=3):
    number = float(str(number) + '.0')
    return str(setSigDigits(number, sigDigits))


def setSigDigits(num, sigDigits=3):
    if num == 0:
        return '0'
    elif num < 10 ** (sigDigits - 5):
        return '%.5f' % num
    elif num < 10 ** (sigDigits - 4):
        return '%.4f' % num
    elif num < 10 ** (sigDigits - 3):
        return '%.3f' % num
    elif num < 10 ** (sigDigits - 2):
        return '%.2f' % num
    elif num < 10 ** (sigDigits - 1):
        return '%.1f' % num
    return '%(num)d' % {'num': num}


def tinderboxPrint(testName,
                   testTitle,
                   numResult,
                   units,
                   printName,
                   printResult,
                   unitsSuffix=""):
    output = "TinderboxPrint:"
    output += "<abbr title=\"" + testTitle + "\">"
    output += printName + "</abbr>:"
    output += "%s\n" % str(printResult)
    output += unitsSuffix
    return output


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
